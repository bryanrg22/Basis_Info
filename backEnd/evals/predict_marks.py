"""``python -m evals.predict_marks``: SAM 3 first, then the vision model reviews numbered marks.

Set-of-Mark prompting (Yang et al. 2023, arXiv 2310.11441): SAM 3's saved detections
(``evals.predict_boxes`` runs, ``instances_raw.jsonl``; several ``--sam3`` runs are pooled)
at a recall-oriented ``--min-score`` become numbered marks
(:func:`basis.vision.marks.select_marks`), drawn on the photo as boxes or mask outlines
(``--style``), with or without the unmarked photo first (``--original``/``--no-original``).
The model (``photo_marks``) keeps or rejects every mark with its label and adds the
objects no mark covers, each with an approximate point.
Count = kept + added (:func:`basis.vision.marks.tally_review`).

``--loop`` (the SAM 3 Agent pattern, arXiv 2511.16719): SAM 3 again for the added points so
every counted object has a mask. Each added point takes the highest-scoring saved candidate of
its label family under it (``--pool`` runs, at least ``--pool-min-score``), or with
``--prompt-sam`` the instance a fresh SAM 3 point prompt returns (Replicate; costs one call per
point); a point inside a kept mark or whose mask overlaps a counted mask by more than
``--overlap`` is a duplicate (:func:`basis.vision.marks.resolve_points`). ``--from-raw`` redoes
only this step from a previous run's ``marks_raw.jsonl`` (no model calls).

Output in ``--out``:

* ``photo_counts.json``: kept + added per photo and repeat; with ``--base``, the base run's
  counts with the countable labels replaced (so the other labels, and with them
  ``photo_components.json``, which gains or loses the countable labels by count, and
  ``photo_rooms.json``, copied, stay the base's: this step changes counts only);
* with ``--loop``: ``loop_masked/`` (only objects with a mask) and ``loop_all/`` (masked plus
  added points no mask was found for), each a predictions directory of the same three files;
* ``marks_raw.jsonl`` (per photo and repeat: the marks, the answer, the tally, timings),
  ``run.json`` (models reported, spend, seconds, mask coverage).

Usage (from ``backEnd/``)::

    python -m evals.predict_marks --sam3 P/sam3_photos_off_v3 --sam3 P/sam3_photos_v2phrases \\
        --photos DIR --out OUT --model claude-opus-5-5 --reasoning-effort medium \\
        --base P/counts_fused_sol_low_opus55_med_loo --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import is_anthropic_model
from basis.llm.errors import ProviderAccountError
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatRequest, ImagePart, Message
from basis.llm.recorder import MemoryRecorder
from basis.vision import marks as mk
from basis.vision.localize import Instance, Point
from basis.vision.prep import PreparedImage, prepare_image
from basis.vision.schemas import ComponentLabel
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import photo_files
from evals.predict_tiles import ask_structured

PROMPT: Final = "photo_marks"
WITH_ORIGINAL: Final = (
    "The first image is the photo. The second is the same photo with the detector's numbered "
    "marks ({style}).\n\nMarks (number: the detector's guess):\n{marks}"
)
MARKED_ONLY: Final = (
    "The image is the photo with the detector's numbered marks ({style}).\n\n"
    "Marks (number: the detector's guess):\n{marks}"
)
# The base's countable labels, replaced by this step's: the seven, plus a pooled "door" (the
# fused base, evals.fuse_counts, writes doors under "door").
REPLACED: Final = frozenset((*mk.MARK_LABELS, "door"))
NO_MARKS: Final = "(no marks: the detector proposed nothing; add every countable object)"
STYLE_WORDS: Final = {"box": "a box per mark", "outline": "the outline of each mark"}


# --------------------------------------------------------------------------- shared helpers


def load_instances(dirs: Sequence[Path], min_score: float = 0.0) -> dict[str, list[Instance]]:
    """``{file: instances}`` pooled over ``evals.predict_boxes`` runs (the first record with
    instances per file and run), at least ``min_score``."""
    pooled: dict[str, list[Instance]] = {}
    for directory in dirs:
        seen: set[str] = set()
        for line in (directory / "instances_raw.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            file = record.get("file")
            if "instances" not in record or file in seen:
                continue
            seen.add(file)
            pooled.setdefault(file, []).extend(
                Instance.model_validate(i)
                for i in record["instances"]
                if float(i["score"]) >= min_score
            )
    return pooled


def sam_seconds(dirs: Sequence[Path]) -> dict[str, float]:
    """Billed SAM 3 predict seconds per file in the first ``--sam3`` run (the detector's own
    latency, for the per-photo time)."""
    out: dict[str, float] = {}
    if not dirs:
        return out
    for line in (dirs[0] / "instances_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if "instances" in record and record["file"] not in out:
                out[record["file"]] = sum(
                    float(c.get("predict_seconds", 0.0)) for c in record.get("calls", [])
                )
    return out


def _repeats(value: Any) -> list[Any]:
    if isinstance(value, Mapping) and "repeats" in value:
        return list(value["repeats"])
    return [value]


def merge_with_base(
    counts: Mapping[str, Sequence[Mapping[str, int] | None]], base: Path | None
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """``(photo_counts, photo_components, photo_rooms)``: per file and repeat, the base run's
    counts with :data:`REPLACED` replaced by ``counts`` (a ``None``
    repeat stays missing); components gain/lose those labels by count; rooms copied. Without
    a base: the counts alone and no components or rooms."""

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    if base is None:
        return (
            {f: fold([dict(c) if c is not None else None for c in v]) for f, v in counts.items()},
            None,
            None,
        )
    base_counts = json.loads((base / "photo_counts.json").read_text(encoding="utf-8"))
    base_components = json.loads((base / "photo_components.json").read_text(encoding="utf-8"))
    rooms = json.loads((base / "photo_rooms.json").read_text(encoding="utf-8"))
    out_counts: dict[str, Any] = {}
    out_components: dict[str, Any] = {}
    for file, runs in counts.items():
        b_counts = _repeats(base_counts.get(file, {}))
        b_components = _repeats(base_components.get(file, []))
        merged_counts: list[Any] = []
        merged_components: list[Any] = []
        for i, mine in enumerate(runs):
            if mine is None:
                merged_counts.append(None)
                merged_components.append(None)
                continue
            bc = b_counts[i % len(b_counts)] or {}
            bl = b_components[i % len(b_components)] or []
            row = {k: v for k, v in bc.items() if k not in REPLACED}
            row.update({k: v for k, v in mine.items() if v})
            merged_counts.append(dict(sorted(row.items())))
            labels = {label for label in bl if label not in REPLACED}
            labels |= {k for k, v in mine.items() if v}
            merged_components.append(sorted(labels))
        out_counts[file] = fold(merged_counts)
        out_components[file] = fold(merged_components)
    return out_counts, out_components, rooms


def write_predictions(
    out: Path, counts: Mapping[str, Sequence[Mapping[str, int] | None]], base: Path | None
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    photo_counts, components, rooms = merge_with_base(counts, base)

    def clean(value: Any) -> Any:
        # A missing repeat cannot be written as null inside "repeats" (the scorer reads every
        # entry as counts): a photo with any missing repeat keeps only its answered ones.
        if isinstance(value, Mapping) and "repeats" in value:
            kept = [v for v in value["repeats"] if v is not None]
            return kept[0] if len(kept) == 1 else {"repeats": kept}
        return value

    photo_counts = {f: clean(v) for f, v in photo_counts.items() if v is not None}
    (out / "photo_counts.json").write_text(json.dumps(photo_counts, indent=1, sort_keys=True))
    if components is not None and rooms is not None:
        components = {f: clean(v) for f, v in components.items() if v is not None}
        (out / "photo_components.json").write_text(json.dumps(components, indent=1, sort_keys=True))
        (out / "photo_rooms.json").write_text(json.dumps(rooms, indent=1, sort_keys=True))


def image_part(jpeg: bytes) -> ImagePart:
    prepared: PreparedImage = prepare_image(jpeg)
    return ImagePart(media_type="image/jpeg", data_base64=prepared.base64, detail="high")


def user_text(marks: Sequence[mk.Mark], style: str, original: bool) -> str:
    listed = mk.mark_list_text(marks) or NO_MARKS
    template = WITH_ORIGINAL if original else MARKED_ONLY
    return template.format(style=STYLE_WORDS[style], marks=listed)


# --------------------------------------------------------------------------- loop


def mark_record(mark: mk.Mark) -> dict[str, Any]:
    i = mark.instance
    return {
        "number": mark.number,
        "label": i.label.value,
        "score": round(i.score, 4),
        "box": i.box.model_dump(),
        "has_mask": i.mask is not None,
    }


def loop_counts(
    tally_kept: Sequence[tuple[mk.Mark, str]],
    added: Sequence[Mapping[str, Any]],
    pool: Sequence[Instance],
    overlap: float,
    prompted: Mapping[int, Instance | None] | None = None,
) -> tuple[dict[str, int], dict[str, int], list[mk.Resolved]]:
    """``(masked, all, resolved)`` for one answer: kept marks plus added points resolved to
    masks; ``all`` also counts added points no mask was found for."""
    kept_counts: dict[str, int] = {}
    counted: list[Instance] = []
    for mark, label in tally_kept:
        kept_counts[label] = kept_counts.get(label, 0) + 1
        counted.append(mark.instance.model_copy(update={"label": ComponentLabel(label)}))
    points = [(str(a["label"]), Point(x=float(a["x"]), y=float(a["y"]))) for a in added]
    resolved = mk.resolve_points(points, pool, counted=counted, overlap=overlap, prompted=prompted)
    masked = mk.add_counts(kept_counts, mk.resolved_counts(resolved, include_unmasked=False))
    everything = mk.add_counts(kept_counts, mk.resolved_counts(resolved, include_unmasked=True))
    return masked, everything, resolved


def resolved_record(r: mk.Resolved) -> dict[str, Any]:
    return {
        "label": r.label,
        "x": round(r.point.x, 4),
        "y": round(r.point.y, 4),
        "status": r.status,
        "duplicate_of": r.duplicate_of,
        "mask_score": round(r.instance.score, 4) if r.instance is not None else None,
    }


async def prompt_sam(
    files: Mapping[str, Path], needs: Mapping[tuple[str, int, int], tuple[str, Point]]
) -> tuple[dict[tuple[str, int, int], Instance | None], float]:
    """One SAM 3 point prompt per ``(file, repeat, index)``; ``(instances, dollars)``."""
    from basis.vision.localize import build_replicate_localizer

    token = os.environ.get("REPLICATE_API_TOKEN", "")
    version = load_settings().sam3_replicate_version
    localizer = build_replicate_localizer(token, version, concurrency=2)
    budget = Budget(remaining_usd=2.0)
    localizer.budget = budget
    out: dict[tuple[str, int, int], Instance | None] = {}

    async def one(key: tuple[str, int, int]) -> None:
        label, point = needs[key]
        image = prepare_image(files[key[0]].read_bytes())
        try:
            prompted = await localizer.segment_prompt(
                image,
                label=ComponentLabel(label),
                points=[point],
                threshold=0.1,
                # EVAL_REPORT 21 ran this with the first v1 phrase; segment_prompt now sends
                # every phrase of the label in the set, one prompt object each.
                phrase_set="v1",
            )
            found = list(prompted.instances)
        except Exception as exc:  # noqa: BLE001 - recorded, the point stays unmasked
            print(f"SAM 3 point prompt failed for {key}: {exc}", file=sys.stderr)
            found = []
        out[key] = found[0].model_copy(update={"source": "model"}) if found else None

    await asyncio.gather(*(one(k) for k in needs))
    await localizer.client.aclose()
    return out, budget.spent_usd


# --------------------------------------------------------------------------- runner


def write_raw(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as out:
        for r in records:
            out.write(json.dumps(r, sort_keys=True) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_marks", description=__doc__)
    parser.add_argument("--sam3", type=Path, action="append", required=True)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base", type=Path, default=None)
    parser.add_argument("--min-score", type=float, default=mk.DEFAULT_MIN_SCORE)
    parser.add_argument("--nms", type=float, default=mk.DEFAULT_MARK_NMS)
    parser.add_argument("--max-marks", type=int, default=mk.DEFAULT_MAX_MARKS)
    parser.add_argument("--style", choices=("box", "outline"), default="box")
    parser.add_argument("--original", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--budget-usd", type=float, default=8.0)
    parser.add_argument("--only", nargs="+", default=None, help="these photo files only")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--pool", type=Path, action="append", default=None)
    parser.add_argument("--pool-min-score", type=float, default=0.1)
    parser.add_argument("--overlap", type=float, default=mk.DEFAULT_OVERLAP)
    parser.add_argument("--prompt-sam", action="store_true")
    parser.add_argument("--from-raw", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.from_raw is not None and not args.loop:
        parser.error("--from-raw only redoes the loop: add --loop")
    return args


@stops_on_account_error
async def run(args: argparse.Namespace) -> int:
    photos = {p.name: p for p in photo_files(args.photos)}
    if args.only:
        photos = {k: v for k, v in photos.items() if k in set(args.only)}
    instances = load_instances(args.sam3)
    marks = {
        f: mk.select_marks(
            instances.get(f, []), min_score=args.min_score, nms=args.nms, max_marks=args.max_marks
        )
        for f in photos
    }
    args.out.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]]
    run_info: dict[str, Any]
    if args.from_raw is not None:
        records = [
            json.loads(line)
            for line in args.from_raw.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = [r for r in records if r["file"] in photos]
        run_info = {"from_raw": str(args.from_raw)}
    else:
        asked = await ask_all(args, photos, marks)
        if asked is None:
            return 2
        records, run_info = asked
    repeats = max((int(r["repeat"]) for r in records), default=args.repeats)
    files = sorted(photos)
    counts: dict[str, list[dict[str, int] | None]] = {f: [None] * repeats for f in files}
    for r in records:
        if r.get("tally") is not None:
            counts[r["file"]][int(r["repeat"]) - 1] = r["tally"]["counts"]
    write_predictions(args.out, counts, args.base)
    if args.from_raw is None:
        write_raw(args.out / "marks_raw.jsonl", records)
    if args.loop:
        run_info["loop"] = await do_loop(args, photos, marks, records, repeats)
    write_raw(args.out / "marks_raw.jsonl", records)
    kept = sum(len(r["tally"]["kept"]) for r in records if r.get("tally"))
    added = sum(len(r["tally"]["added"]) for r in records if r.get("tally"))
    run_info.update(
        {
            "sam3": [str(d) for d in args.sam3],
            "base": str(args.base) if args.base else None,
            "min_score": args.min_score,
            "nms": args.nms,
            "max_marks": args.max_marks,
            "style": args.style,
            "original": args.original,
            "photos": len(files),
            "marks_per_photo": round(statistics.mean(len(marks[f]) for f in files), 2)
            if files
            else 0,
            "kept": kept,
            "added": added,
            "mask_coverage": round(kept / (kept + added), 4) if kept + added else None,
        }
    )
    previous = args.out / "run.json"
    if args.from_raw is not None and previous.exists():
        run_info = {**json.loads(previous.read_text()), **run_info}
    previous.write_text(json.dumps(run_info, indent=1, sort_keys=True))
    if args.loop:
        for sub in ("loop_masked", "loop_all"):
            shutil.copy(previous, args.out / sub / "run.json")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


async def ask_all(
    args: argparse.Namespace, photos: Mapping[str, Path], marks: Mapping[str, list[mk.Mark]]
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    settings = load_settings()
    pool = guard(ProviderPool.from_settings(settings))
    if pool is None:
        print("no model provider configured", file=sys.stderr)
        return None
    prompt = prompts.load(PROMPT, args.prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = MemoryRecorder()
    call = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.predict_marks",
        budget=budget,
        recorder=recorder,
    )
    model = args.model or settings.vision_model
    semaphore = asyncio.Semaphore(args.concurrency)
    detector_seconds = sam_seconds(args.sam3)
    rendered = {
        f: mk.render_marks(photos[f].read_bytes(), marks[f], style=args.style) for f in photos
    }
    originals = {f: photos[f].read_bytes() for f in photos}

    async def one(file: str, repeat: int) -> dict[str, Any]:
        images: tuple[ImagePart, ...] = (image_part(originals[file]),) if args.original else ()
        images += (image_part(rendered[file]),)
        request = ChatRequest(
            model=model,
            system=prompt.text,
            messages=(
                Message(
                    role="user",
                    content=user_text(marks[file], args.style, args.original),
                    images=images,
                ),
            ),
            response_model=mk.MarksReview,
            max_tokens=args.max_tokens,
            temperature=0.0,
            reasoning_effort=args.reasoning_effort,
            prompt_version=prompt.version_id,
            study_id=str(call.study_id),
            activity=call.activity,
        )
        async with semaphore:
            started = time.monotonic()
            try:
                parsed, attempts, errors, reported = await ask_structured(pool, call, request)
            except ProviderAccountError:
                raise  # stops the run (evals.account_stop)
            except Exception as exc:  # noqa: BLE001 - transient after retries: photo missing
                parsed, attempts, errors, reported = None, 0, [repr(exc)], None
            seconds = round(time.monotonic() - started, 2)
        record: dict[str, Any] = {
            "file": file,
            "repeat": repeat,
            "marks": [mark_record(m) for m in marks[file]],
            "attempts": attempts,
            "errors": errors,
            "model": reported,
            "seconds": seconds,
            "detector_seconds": detector_seconds.get(file),
            "review": None,
            "tally": None,
        }
        if isinstance(parsed, mk.MarksReview):
            tally = mk.tally_review(parsed, marks[file])
            record["review"] = parsed.model_dump()
            record["tally"] = {
                "counts": tally.counts,
                "kept": [[m.number, label] for m, label in tally.kept],
                "added": [a.model_dump() for a in tally.added],
                "rejected": tally.rejected,
                "unanswered": tally.unanswered,
                "unknown": tally.unknown,
                "repeated": tally.repeated,
            }
        return record

    records: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        records += await asyncio.gather(*(one(f, repeat) for f in sorted(photos)))
        print(
            f"repeat {repeat}/{args.repeats}: {len(photos)} photos, spent ${budget.spent_usd:.4f}",
            file=sys.stderr,
        )
    answered = [r for r in records if r["tally"] is not None]
    info = {
        "prompt": prompt.version_id,
        "model_requested": model,
        "models_reported": sorted({str(r.model) for r in recorder.records} - {""}),
        "reasoning_effort": args.reasoning_effort
        or (settings.anthropic_effort if is_anthropic_model(model) else None),
        "max_tokens": args.max_tokens,
        "repeats": args.repeats,
        "calls": len(recorder.records),
        "unanswered_photos": len(records) - len(answered),
        "errors": [
            {"file": r["file"], "repeat": r["repeat"], "errors": r["errors"]}
            for r in records
            if r["tally"] is None
        ],
        "spent_usd": round(budget.spent_usd, 6),
        "usd_per_photo": round(budget.spent_usd / len(records), 6) if records else None,
        "model_seconds_per_photo": round(statistics.mean(r["seconds"] for r in answered), 2)
        if answered
        else None,
        "detector_seconds_per_photo": round(
            statistics.mean(v for v in detector_seconds.values()), 2
        )
        if detector_seconds
        else None,
    }
    return records, info


async def do_loop(
    args: argparse.Namespace,
    photos: Mapping[str, Path],
    marks: Mapping[str, list[mk.Mark]],
    records: list[dict[str, Any]],
    repeats: int,
) -> dict[str, Any]:
    pool = load_instances(args.pool or args.sam3, args.pool_min_score)
    by_number = {f: {m.number: m for m in marks[f]} for f in photos}

    def kept_of(record: Mapping[str, Any]) -> list[tuple[mk.Mark, str]]:
        return [(by_number[record["file"]][n], label) for n, label in record["tally"]["kept"]]

    prompted: dict[tuple[str, int, int], Instance | None] = {}
    sam_usd = 0.0
    if args.prompt_sam:
        needs: dict[tuple[str, int, int], tuple[str, Point]] = {}
        for r in records:
            if r.get("tally") is None:
                continue
            _, _, resolved = loop_counts(
                kept_of(r), r["tally"]["added"], pool.get(r["file"], []), args.overlap
            )
            for i, res in enumerate(resolved):
                if res.status == "unmasked":
                    needs[(r["file"], int(r["repeat"]), i)] = (res.label, res.point)
        print(f"SAM 3 point prompts: {len(needs)}", file=sys.stderr)
        prompted, sam_usd = await prompt_sam(photos, needs)
    masked: dict[str, list[dict[str, int] | None]] = {f: [None] * repeats for f in sorted(photos)}
    everything: dict[str, list[dict[str, int] | None]] = {
        f: [None] * repeats for f in sorted(photos)
    }
    statuses: dict[str, int] = {}
    for r in records:
        if r.get("tally") is None:
            continue
        key = (r["file"], int(r["repeat"]))
        mine = {i: v for (f, rep, i), v in prompted.items() if (f, rep) == key}
        m, a, resolved = loop_counts(
            kept_of(r), r["tally"]["added"], pool.get(r["file"], []), args.overlap, mine
        )
        masked[r["file"]][int(r["repeat"]) - 1] = m
        everything[r["file"]][int(r["repeat"]) - 1] = a
        r["loop"] = {"masked": m, "all": a, "resolved": [resolved_record(x) for x in resolved]}
        for x in resolved:
            statuses[x.status] = statuses.get(x.status, 0) + 1
    write_predictions(args.out / "loop_masked", masked, args.base)
    write_predictions(args.out / "loop_all", everything, args.base)
    kept = sum(len(r["tally"]["kept"]) for r in records if r.get("tally"))
    with_mask = kept + statuses.get("pool", 0) + statuses.get("prompted", 0)
    counted_all = with_mask + statuses.get("unmasked", 0)
    return {
        "pool": [str(d) for d in (args.pool or args.sam3)],
        "pool_min_score": args.pool_min_score,
        "overlap": args.overlap,
        "prompt_sam": args.prompt_sam,
        "sam_point_prompts": len(prompted),
        "sam_spent_usd": round(sam_usd, 6),
        "added_point_status": dict(sorted(statuses.items())),
        "mask_coverage_all": round(with_mask / counted_all, 4) if counted_all else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
