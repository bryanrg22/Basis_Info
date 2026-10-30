"""``python -m evals.manual_add``: adding an object the pipeline missed, measured.

The product's path (``POST /v1/studies/{id}/photos/{document_id}/objects``) replayed on the
real photos with the product's own code (:mod:`basis.vision.selection`,
:func:`basis.vision.localize.pick_prompted`): every verified object of the count classes
(``detection_boxes_v1.json``, 191 on the 52 photos) is selected as an engineer would,
against the instances the pipeline stores for that photo (the shipped v3 SAM 3 run, kept
from :data:`~basis.vision.localize.CANDIDATE_THRESHOLD`, counted at each class's threshold).

* Objects: a verified box the counted instances match (greedy, highest score first, box
  IoU >= 0.5: the harness's ``match_class``) is *counted*; the others are *misses*, what an
  engineer adds.
* Selections: the verified box drawn three ways, ``exact`` and two sloppy ones (``big``:
  sides x1.15, shifted 10% down-right; ``small``: x0.85, shifted 10% up-left), and a tap at
  its centre (``plan_point``: the stored instance under it, else 422) with the review's
  cycling ceiling (some instance of the label under the tap is the object).
* A box that matches no stored instance is sent to SAM 3 with the label's v3 phrases and
  the box; the product keeps the returned instance overlapping the box best at IoU >= 0.5
  with score >= 0.5. Those answers are prompted live once (``--live``, the product's
  request: one prompt object per phrase carrying the box, batched one prediction per photo,
  parsed by the product's ``parse_result``) and saved to ``--prompts``; every other run
  replays them. Each phrase's answer is saved apart, so the product's "every v3 phrase"
  and the "first v3 phrase only" alternative are both scored.
* Outcomes, first submission: ``outlined`` (a new instance with an outline, ``correct``
  when its stored box has IoU >= 0.5 with the verified box, the detection eval's match,
  else ``wrong``; how often the outline's own bounds match too is reported), ``box_only``
  (nothing outlined it: the drawn box is stored), ``duplicate`` (the guard answered a
  counted instance: nothing added), ``nothing`` (a tap over no instance). After "add
  anyway" (``allow_duplicate``) for the duplicates: the same, again.
* On counted objects any add is a duplicate created (the guard is what should stop it).

Gates (:func:`gates`): on the misses, the exact box outlines the right object at least
:data:`MIN_CORRECT` of the time and a wrong object never (sloppy boxes included); on the
counted objects, at most :data:`MAX_DUPLICATES` of all box selections create a duplicate.

Only indices, labels, boxes and rates are written or printed (no file names or pixels):
``--out`` gets ``records.json`` (one row per object and selection) and ``summary.json``,
plus ``console.log``, ``manifest.json``, ``results.json`` and ``report.md``, the run record
(:mod:`evals.record`; filed under ``detection`` / ``manual_add``, one case per gate, passed
or failed as the gate decides).

Usage (from ``backEnd/``)::

    python -m evals.manual_add --data ~/basis-eval-data --out OUT          # replay
    python -m evals.manual_add --data ~/basis-eval-data --out OUT --live    # prompt SAM 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from basis.vision.localize import (
    CANDIDATE_THRESHOLD,
    DEFAULT_THRESHOLD,
    Box,
    Instance,
    Point,
    box_iou,
    count_threshold,
    covers,
    model_input,
    parse_result,
    pick_prompted,
    prompt_phrases,
)
from basis.vision.schemas import ComponentLabel
from basis.vision.selection import (
    Candidate,
    Counted,
    Prompt,
    Stored,
    bare_box,
    duplicate_of,
    overlap,
    plan_box,
    plan_point,
)
from evals import record as eval_record
from evals.metrics import wilson_interval

ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"
SAM3_RUNS: Final = ("predictions/sam3_photos_v3", "predictions/sam3_photos_v3_redo1")
LABELS_FILE: Final = "detection_boxes_v1.json"
IMAGES_DIR: Final = "detection/images"
PROMPTS_FILE: Final = "predictions/manual_add_v1/prompted.json"
CLASSES: Final = (
    "window",
    "interior_door",
    "exterior_door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
)
# Side scale and diagonal shift (fractions of the box) of each drawn box.
VARIANTS: Final[dict[str, tuple[float, int]]] = {
    "exact": (1.0, 0),
    "big": (1.15, 1),
    "small": (0.85, -1),
}
SHIFT: Final = 0.1
MATCH_IOU: Final = 0.5
MIN_CORRECT: Final = 0.95
MAX_DUPLICATES: Final = 0.02
GUARD_THRESHOLDS: Final = (0.3, 0.5)
PhraseMode = Literal["all", "first"]
SAM3_VERSION_SETTING: Final = "sam3_replicate_version"


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class Target:
    """One verified object: ``photo`` is its photo's index in file-name order."""

    photo: int
    label: str
    box: Box
    counted: bool

    @property
    def key(self) -> str:
        b = self.box
        return f"{self.photo}:{self.label}:{b.x1:.6f},{b.y1:.6f},{b.x2:.6f},{b.y2:.6f}"


@dataclass
class Photo:
    index: int
    file: str
    sha256: str
    stored: list[Stored]
    targets: list[Target] = field(default_factory=list)


def stored_instances(rows: Sequence[Mapping[str, Any]]) -> list[Stored]:
    """The rows ``localize_photo`` stores: instances from the candidate threshold, counted
    at their class's threshold."""
    out = []
    for raw in rows:
        instance = Instance.model_validate(raw)
        if instance.score >= CANDIDATE_THRESHOLD:
            out.append(Stored(instance, counted=instance.score >= count_threshold(instance.label)))
    return out


def counted_targets(
    photo: int, stored: Sequence[Stored], gold: Sequence[Mapping[str, Any]]
) -> list[Target]:
    """The verified boxes of the count classes, each marked counted when a counted
    instance matches it (greedy by score at box IoU >= 0.5, each box at most once)."""
    targets: list[Target] = []
    for label in CLASSES:
        boxes = [
            Box(x1=g["x1"], y1=g["y1"], x2=g["x2"], y2=g["y2"]) for g in gold if g["label"] == label
        ]
        taken = [False] * len(boxes)
        counted = sorted(
            (s.instance for s in stored if s.counted and s.instance.label.value == label),
            key=lambda i: -i.score,
        )
        for instance in counted:
            if not boxes:
                break
            overlaps = [box_iou(instance.box, b) for b in boxes]
            best = max(range(len(boxes)), key=overlaps.__getitem__)
            if overlaps[best] >= MATCH_IOU and not taken[best]:
                taken[best] = True
        targets += [Target(photo, label, b, t) for b, t in zip(boxes, taken, strict=True)]
    return targets


def load(data: Path) -> list[Photo]:
    """The photos (file-name order) with their stored instances and verified objects."""
    labels = json.loads((data / LABELS_FILE).read_text(encoding="utf-8"))
    raw: dict[str, dict[str, Any]] = {}
    for run in SAM3_RUNS:
        for line in (data / run / "instances_raw.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "instances" in record:
                raw[record["file"]] = record
    photos = []
    for index, image in enumerate(sorted(labels["images"], key=lambda i: i["file"])):
        record = raw[image["file"]]
        stored = stored_instances(record["instances"])
        photos.append(
            Photo(
                index=index,
                file=image["file"],
                sha256=record["image"]["sha256"],
                stored=stored,
                targets=counted_targets(index, stored, image["boxes"]),
            )
        )
    return photos


def drawn(box: Box, variant: str) -> Box:
    """The box an engineer draws: scaled and shifted (:data:`VARIANTS`), clamped."""
    scale, sign = VARIANTS[variant]
    width, height = box.x2 - box.x1, box.y2 - box.y1
    cx = (box.x1 + box.x2) / 2 + sign * SHIFT * width
    cy = (box.y1 + box.y2) / 2 + sign * SHIFT * height

    def clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    return Box(
        x1=clamp(cx - scale * width / 2),
        y1=clamp(cy - scale * height / 2),
        x2=clamp(cx + scale * width / 2),
        y2=clamp(cy + scale * height / 2),
    )


# --------------------------------------------------------------------------- replay

SavedPrompts = Mapping[str, Mapping[str, Sequence[Instance]]]  # key:variant -> phrase -> found


def geometry(instance: Instance) -> Box:
    """What the engineer sees: the outline's bounding box (the box when no outline)."""
    if instance.mask is not None:
        bounds = instance.mask.bbox()
        if bounds is not None:
            x1, y1, x2, y2 = bounds
            return Box.from_pixels(x1, y1, x2, y2, instance.mask.width, instance.mask.height)
    return instance.box


def prompt_key(target: Target, variant: str) -> str:
    return f"{target.key}:{variant}"


def prompted_instance(
    found: Mapping[str, Sequence[Instance]] | None, box: Box, phrases: PhraseMode
) -> Instance | None | Literal["missing"]:
    """What the product keeps of a saved answer (``pick_prompted`` over the phrases sent)."""
    if found is None:
        return "missing"
    sent = list(found) if phrases == "all" else list(found)[:1]
    pool = [i for phrase in sent for i in found[phrase] if i.score >= DEFAULT_THRESHOLD]
    picked = pick_prompted(pool, [], [box])
    return picked[0] if picked else None


def proposed(
    photo: Photo,
    target: Target,
    box: Box,
    prompted: SavedPrompts,
    variant: str,
    phrases: PhraseMode,
    *,
    allow_duplicate: bool,
) -> tuple[str, Instance | None]:
    """What a box submission would add before the duplicate guard: ``("duplicate", None)``
    when it is a counted instance, ``("outlined", instance)`` for a stored candidate or
    SAM 3's answer, ``("box_only", the box)``, or ``("unprompted", None)`` when no answer
    was saved for a selection that reaches SAM 3."""
    label = ComponentLabel(target.label)
    plan = plan_box(photo.stored, label, box, allow_duplicate=allow_duplicate)
    if isinstance(plan, Counted):
        return "duplicate", None
    if isinstance(plan, Candidate):
        return "outlined", photo.stored[plan.index].instance
    assert isinstance(plan, Prompt)
    kept = prompted_instance(prompted.get(prompt_key(target, variant)), box, phrases)
    if kept == "missing":
        return "unprompted", None
    if kept is None:
        return "box_only", bare_box(label, box)
    return "outlined", kept


def submit(
    photo: Photo,
    target: Target,
    box: Box,
    prompted: SavedPrompts,
    variant: str,
    phrases: PhraseMode,
    *,
    allow_duplicate: bool,
) -> tuple[str, Instance | None]:
    """One box submission, as the route answers it: ``(outcome, instance added)``."""
    outcome, new = proposed(
        photo, target, box, prompted, variant, phrases, allow_duplicate=allow_duplicate
    )
    if new is not None and not allow_duplicate and duplicate_of(photo.stored, new) is not None:
        return "duplicate", None
    return outcome, new


def score(target: Target, outcome: str, new: Instance | None) -> str:
    """``correct``/``wrong`` for an added outline by its stored box (box IoU >= 0.5 with the
    verified box: the detection eval's match, EVAL_REPORT 10); other outcomes as they are."""
    if outcome == "outlined":
        assert new is not None
        return "correct" if box_iou(new.box, target.box) >= MATCH_IOU else "wrong"
    return outcome


def outline_iou(outcome: str, new: Instance | None, target: Target) -> float | None:
    """The added outline's own bounds against the verified box (what the engineer sees;
    reported beside :func:`score`, which uses the stored box)."""
    if outcome != "outlined" or new is None:
        return None
    return round(box_iou(geometry(new), target.box), 4)


def needs_prompt(photo: Photo, target: Target, variant: str) -> bool:
    """Whether a selection reaches SAM 3: on its first submission, or, for a miss, after
    "add anyway" (counted objects are scored on their first submission only)."""
    box = drawn(target.box, variant)
    label = ComponentLabel(target.label)
    allows = (False,) if target.counted else (False, True)
    return any(
        isinstance(plan_box(photo.stored, label, box, allow_duplicate=allow), Prompt)
        for allow in allows
    )


def evaluate(
    photos: Sequence[Photo], prompted: SavedPrompts, phrases: PhraseMode = "all"
) -> list[dict[str, Any]]:
    """One record per object and selection."""
    records = []
    for photo in photos:
        for target in photo.targets:
            base = {
                "photo": target.photo,
                "label": target.label,
                "box": [target.box.x1, target.box.y1, target.box.x2, target.box.y2],
                "counted": target.counted,
            }
            for variant in VARIANTS:
                box = drawn(target.box, variant)
                first, new = submit(
                    photo, target, box, prompted, variant, phrases, allow_duplicate=False
                )
                anyway, iou = score(target, first, new), outline_iou(first, new, target)
                if first == "duplicate" and not target.counted:
                    again, added = submit(
                        photo, target, box, prompted, variant, phrases, allow_duplicate=True
                    )
                    anyway, iou = score(target, again, added), outline_iou(again, added, target)
                guard = guard_overlap(photo, target, box, prompted, variant, phrases)
                records.append(
                    {
                        **base,
                        "selection": f"box_{variant}",
                        "outcome": score(target, first, new),
                        "after_add_anyway": anyway,
                        "outline_iou": iou,
                        "guard_overlap": guard,
                    }
                )
            records.append({**base, "selection": "tap", **tap(photo, target)})
    return records


def guard_overlap(
    photo: Photo,
    target: Target,
    box: Box,
    prompted: SavedPrompts,
    variant: str,
    phrases: PhraseMode,
) -> float | None:
    """How much the instance a box's first submission would add covers the counted ones of
    its label (the guard's measure), for comparing guard thresholds; None when the box is a
    counted instance or nothing was saved."""
    _, new = proposed(photo, target, box, prompted, variant, phrases, allow_duplicate=False)
    if new is None:
        return None
    values = [
        overlap(new, s.instance)
        for s in photo.stored
        if s.counted and s.instance.label is new.label
    ]
    return round(max(values, default=0.0), 4)


def tap(photo: Photo, target: Target) -> dict[str, Any]:
    """A tap at the object's centre: what the product does, and the cycling ceiling."""
    b = target.box
    point = Point(x=(b.x1 + b.x2) / 2, y=(b.y1 + b.y2) / 2)
    label = ComponentLabel(target.label)
    plan = plan_point(photo.stored, label, point)
    if isinstance(plan, Counted):
        outcome = "duplicate"
    elif isinstance(plan, Candidate):
        new = photo.stored[plan.index].instance
        outcome = "duplicate" if duplicate_of(photo.stored, new) else score(target, "outlined", new)
    else:
        outcome = "nothing"

    under = [
        s.instance for s in photo.stored if s.instance.label is label and covers(s.instance, point)
    ]
    reachable = any(box_iou(geometry(i), target.box) >= MATCH_IOU for i in under)
    return {"outcome": outcome, "after_add_anyway": outcome, "cycle_reaches": reachable}


# --------------------------------------------------------------------------- summary


def rate(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [low, high]}


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    misses = [r for r in records if not r["counted"]]
    counted = [r for r in records if r["counted"]]
    summary: dict[str, Any] = {"objects": {"misses": 0, "counted": 0}, "misses": {}, "counted": {}}
    summary["objects"]["misses"] = sum(1 for r in misses if r["selection"] == "tap")
    summary["objects"]["counted"] = sum(1 for r in counted if r["selection"] == "tap")
    for selection in [f"box_{v}" for v in VARIANTS] + ["tap"]:
        rows = [r for r in misses if r["selection"] == selection]
        outcomes = Counter(r["outcome"] for r in rows)
        after = Counter(r["after_add_anyway"] for r in rows)
        entry = {
            "outcomes": dict(sorted(outcomes.items())),
            "correct": rate(outcomes["correct"], len(rows)),
            "wrong": rate(outcomes["wrong"], len(rows)),
            "correct_after_add_anyway": rate(after["correct"], len(rows)),
            "wrong_after_add_anyway": rate(after["wrong"], len(rows)),
            # The same after add anyway, judged by the outline's own bounds (boxes only).
            "outline_matches_after_add_anyway": rate(
                sum(1 for r in rows if (r.get("outline_iou") or 0.0) >= MATCH_IOU), len(rows)
            ),
            "by_class": {
                label: dict(
                    sorted(Counter(r["outcome"] for r in rows if r["label"] == label).items())
                )
                for label in CLASSES
            },
        }
        if selection == "tap":
            del entry["outline_matches_after_add_anyway"]
            entry["cycle_reaches"] = rate(sum(1 for r in rows if r["cycle_reaches"]), len(rows))
        summary["misses"][selection] = entry
        rows = [r for r in counted if r["selection"] == selection]
        created = [r for r in rows if r["outcome"] in ("correct", "wrong", "box_only")]
        summary["counted"][selection] = {
            "outcomes": dict(sorted(Counter(r["outcome"] for r in rows).items())),
            "duplicates_created": rate(len(created), len(rows)),
        }
    boxes = [r for r in counted if r["selection"].startswith("box_")]
    created = [r for r in boxes if r["outcome"] in ("correct", "wrong", "box_only")]
    summary["counted"]["boxes_all"] = {"duplicates_created": rate(len(created), len(boxes))}
    summary["guard"] = guard_table(records)
    return summary


def guard_table(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per guard threshold, over box selections: duplicates it would block on counted
    objects, and correct adds it would block on misses."""
    boxes = [
        r for r in records if r["selection"].startswith("box_") and r["guard_overlap"] is not None
    ]
    table = {}
    for threshold in GUARD_THRESHOLDS:
        on_counted = [r for r in boxes if r["counted"]]
        correct_adds = [r for r in boxes if not r["counted"] and r["after_add_anyway"] == "correct"]
        table[f"{threshold:g}"] = {
            "blocks_duplicates": rate(
                sum(r["guard_overlap"] >= threshold for r in on_counted), len(on_counted)
            ),
            "blocks_correct_adds": rate(
                sum(r["guard_overlap"] >= threshold for r in correct_adds), len(correct_adds)
            ),
        }
    return table


@dataclass(frozen=True)
class Gate:
    """One release gate: its id, whether it holds, and the measure it was judged on."""

    id: str
    passed: bool
    detail: str


def gate_checks(summary: Mapping[str, Any]) -> list[Gate]:
    """Every release gate (module doc) with its verdict."""
    checks: list[Gate] = []
    exact = summary["misses"]["box_exact"]["correct"]
    checks.append(
        Gate(
            "exact_box_outlines_misses",
            exact["rate"] is not None and exact["rate"] >= MIN_CORRECT,
            f"exact box outlines {exact['k']}/{exact['n']} misses, need >= {MIN_CORRECT}",
        )
    )
    for variant in VARIANTS:
        wrong = summary["misses"][f"box_{variant}"]["wrong"]
        after = summary["misses"][f"box_{variant}"]["wrong_after_add_anyway"]
        checks.append(
            Gate(
                f"box_{variant}_never_wrong",
                not (wrong["k"] or after["k"]),
                f"box_{variant} outlines a wrong object: {wrong['k']} "
                f"({after['k']} after add anyway)",
            )
        )
    duplicates = summary["counted"]["boxes_all"]["duplicates_created"]
    checks.append(
        Gate(
            "counted_boxes_no_duplicates",
            duplicates["rate"] is not None and duplicates["rate"] <= MAX_DUPLICATES,
            f"boxes on counted objects created {duplicates['k']}/{duplicates['n']} duplicates, "
            f"allowed <= {MAX_DUPLICATES}",
        )
    )
    return checks


def gates(summary: Mapping[str, Any]) -> list[str]:
    """The release gates (module doc) that do not hold; empty when every one holds."""
    return [gate.detail for gate in gate_checks(summary) if not gate.passed]


def table(summary: Mapping[str, Any]) -> str:
    """The summary as Markdown (rates and counts only)."""

    def cell(r: Mapping[str, Any]) -> str:
        if not r["n"]:
            return "-"
        return f"{r['k']}/{r['n']} = {r['rate']:.2f} [{r['ci95'][0]:.2f}, {r['ci95'][1]:.2f}]"

    lines = [
        f"objects: {summary['objects']['misses']} misses, {summary['objects']['counted']} counted",
        "",
        "| misses | correct | wrong object | correct after add anyway | outline matches "
        "(after add anyway) | outcomes |",
        "|---|---|---|---|---|---|",
    ]
    for selection, entry in summary["misses"].items():
        outline = entry.get("outline_matches_after_add_anyway")
        lines.append(
            f"| {selection} | {cell(entry['correct'])} | {cell(entry['wrong'])} | "
            f"{cell(entry['correct_after_add_anyway'])} | {cell(outline) if outline else '-'} | "
            f"{entry['outcomes']} |"
        )
    if "cycle_reaches" in summary["misses"]["tap"]:
        lines.append(f"\ntap cycling ceiling: {cell(summary['misses']['tap']['cycle_reaches'])}")
    lines += ["", "| counted objects | duplicates created | outcomes |", "|---|---|---|"]
    for selection, entry in summary["counted"].items():
        lines.append(
            f"| {selection} | {cell(entry['duplicates_created'])} | {entry.get('outcomes', '')} |"
        )
    lines += ["", "| guard IoS | blocks duplicates | blocks correct adds |", "|---|---|---|"]
    for threshold, entry in summary["guard"].items():
        lines.append(
            f"| {threshold} | {cell(entry['blocks_duplicates'])} | "
            f"{cell(entry['blocks_correct_adds'])} |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- prompts


def load_prompts(path: Path) -> dict[str, dict[str, list[Instance]]]:
    """Saved SAM 3 answers: ``{key:variant: {phrase: [instance]}}`` (phrases in order)."""
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, list[Instance]]] = defaultdict(dict)
    for record in document["records"]:
        out[record["key"]][record["phrase"]] = [
            Instance.model_validate(i) for i in record["instances"]
        ]
    return dict(out)


async def prompt_live(
    photos: Sequence[Photo], data: Path, path: Path, *, max_usd: float, concurrency: int = 2
) -> float:
    """Prompt SAM 3 for every selection that reaches it and save the answers to ``path``
    (merged with those saved before); returns what the new calls cost. Stops before a
    prediction that could pass ``max_usd``."""
    from basis.config import load_settings
    from basis.vision.localize import L40S_USD_PER_SECOND, RESERVE_SECONDS, _center_box
    from basis.vision.localizer_config import build_localizer
    from basis.vision.prep import prepare_image

    settings = load_settings()
    localizer = build_localizer(settings)
    if localizer is None or not hasattr(localizer, "_run"):
        raise SystemExit("no localizer configured (REPLICATE_API_TOKEN or LOCALIZER_URL)")
    saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"records": []}
    have = {(r["key"], r["phrase"]) for r in saved["records"]}
    spent = 0.0
    lock = asyncio.Lock()
    gate = asyncio.Semaphore(concurrency)

    async def one(photo: Photo) -> None:
        nonlocal spent
        wanted = []
        for target in photo.targets:
            for variant in VARIANTS:
                if not needs_prompt(photo, target, variant):
                    continue
                box = drawn(target.box, variant)
                for phrase in prompt_phrases(ComponentLabel(target.label)):
                    key = prompt_key(target, variant)
                    if (key, phrase.text) not in have:
                        wanted.append((key, phrase, box))
        if not wanted:
            return
        async with gate:
            async with lock:
                if spent + RESERVE_SECONDS * L40S_USD_PER_SECOND > max_usd:
                    print(
                        f"photo {photo.index}: skipped, the cap ${max_usd} is reached",
                        file=sys.stderr,
                    )
                    return
            image = prepare_image((data / IMAGES_DIR / photo.file).read_bytes())
            if image.sha256 != photo.sha256:
                raise SystemExit(f"photo {photo.index} prepares differently from the stored run")
            prompts = [
                json.dumps({"text": p.text, "positive_boxes": [_center_box(b)]})
                for _, p, b in wanted
            ]
            results, call = await localizer._run(
                model_input(image, prompts, DEFAULT_THRESHOLD, True),
                tuple(p.text for _, p, _ in wanted),
            )
        async with lock:
            spent += call.cost_usd
            for (key, phrase, _), result in zip(wanted, results, strict=True):
                found = parse_result(result, phrase, image.width, image.height, DEFAULT_THRESHOLD)
                saved["records"].append(
                    {
                        "key": key,
                        "phrase": phrase.text,
                        "instances": [i.model_dump(mode="json") for i in found],
                    }
                )
        print(
            f"photo {photo.index}: {len(wanted)} prompts, ${call.cost_usd:.4f} "
            f"(total ${spent:.4f})",
            file=sys.stderr,
        )

    try:
        await asyncio.gather(*(one(p) for p in photos))
    finally:
        saved["spent_usd"] = round(saved.get("spent_usd", 0.0) + spent, 6)
        saved["model_version"] = getattr(localizer, "version", None)
        saved["threshold"] = DEFAULT_THRESHOLD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved), encoding="utf-8")
        client = getattr(localizer, "client", None)
        if client is not None:
            await client.aclose()
    return spent


# --------------------------------------------------------------------------- runner


def run(
    data: Path, prompts_path: Path, phrases: PhraseMode = "all"
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    photos = load(data)
    records = evaluate(photos, load_prompts(prompts_path), phrases)
    return records, summarize(records)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.manual_add", description=__doc__)
    default = os.environ.get(ENV_VAR)
    parser.add_argument(
        "--data", type=Path, default=Path(default).expanduser() if default else None
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, default=None, help=f"default: DATA/{PROMPTS_FILE}")
    parser.add_argument("--phrases", choices=("all", "first"), default="all")
    parser.add_argument("--live", action="store_true", help="prompt SAM 3 for missing answers")
    parser.add_argument("--max-usd", type=float, default=0.5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data is None:
        raise SystemExit(f"--data or {ENV_VAR} is required")
    eval_run = eval_record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="detection", version="manual_add"
    )
    try:
        code = _main(args, eval_run)
    except BaseException as exc:
        eval_record.finish_pending(args, exit_code=1, error=exc)
        raise
    eval_run.finish(None, exit_code=code)
    return code


def _main(args: argparse.Namespace, eval_run: eval_record.EvalRun) -> int:
    prompts_path = args.prompts or args.data / PROMPTS_FILE
    if args.live:
        spent = asyncio.run(
            prompt_live(load(args.data), args.data, prompts_path, max_usd=args.max_usd)
        )
        print(f"SAM 3 spend this run: ${spent:.4f}", file=sys.stderr)
    eval_run.dataset(args.data / LABELS_FILE)
    for name in SAM3_RUNS:
        eval_run.input(name, args.data / name / "instances_raw.jsonl")
    eval_run.input("prompts", prompts_path)
    records, summary = run(args.data, prompts_path, args.phrases)
    checks = gate_checks(summary)
    for gate in checks:
        eval_run.case(
            gate.id,
            eval_record.PASSED if gate.passed else eval_record.FAILED,
            kind="gate",
            detail=gate.detail,
        )
    failures = [gate.detail for gate in checks if not gate.passed]
    summary["gates"] = {"passed": not failures, "failures": failures, "phrases": args.phrases}
    eval_run.note(phrases=args.phrases, live=args.live, gates=summary["gates"])
    (args.out / "records.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(table(summary))
    print("gates:", "passed" if not failures else "; ".join(failures))
    unprompted = sum(1 for r in records if "unprompted" in (r["outcome"], r["after_add_anyway"]))
    if unprompted:
        print(
            f"{unprompted} selections have no saved SAM 3 answer (run with --live)", file=sys.stderr
        )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
