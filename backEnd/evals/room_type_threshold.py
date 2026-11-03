"""``python -m evals.room_type_threshold``: the room-type review flag, measured on stored answers.

The per-photo step flags a photo for the engineer when its room type is ``ambiguous`` or its
self-reported ``room_confidence`` is below :data:`basis.vision.analyze.ROOM_REVIEW_THRESHOLD`
(:func:`basis.vision.analyze.review`); the threshold is a fixed value nothing measured. This
module measures it, with no model call, on the per-photo answers a stored run kept
(``photo_raw.jsonl`` of ``evals.predict_photos`` or of a live-answer export): the room type
and its confidence of every answer, against the photo's label
(``photo_rooms_v1.json``; the labels' ``entry`` is ``hallway`` and ``stairs`` is ``stairway``,
as ``evals.room_metadata`` scores).

What it reports, per run and over all the runs given (EVAL_REPORT house style tables in
``<out>/tables.md``, the numbers in ``<out>/*.json``):

* **Calibration** of the self-reported confidence: per confidence bin, how often the room
  type is right against the bin's mean confidence; the expected calibration error (Guo et
  al. 2017: the bin-weighted mean of |accuracy - confidence|), the Brier score, and the AUROC
  of the confidence as a ranking of right over wrong answers (0.5: no separation).
* **The threshold sweep**: at each threshold ``t`` the production rule's flag (``ambiguous``
  or confidence below ``t``) and, of the flagged photos, how many were wrong (errors caught),
  of the unflagged how many were wrong (errors missed), the precision of the flag, the flag
  rate, and the review cost as photos flagged per error caught. An ``ambiguous`` answer
  counts as wrong (it is not the right type) and as flagged at every threshold (the rule
  flags it whatever its confidence), so every row's errors caught includes them; the
  ``by confidence`` column is what the threshold adds beyond the abstentions.
* **Alternative rules**, scored the same way: flag when the answer disagrees with the room
  type the photo's file name says (:func:`basis.vision.rooms.name_room_type`; free), flag
  when the file name is silent or disagrees, flag when another repeat of the same run
  answered a different type, flag when another run (model) answered a different type for the
  same photo and repeat, and the threshold combined with each.
* **Operating points** by stated rules: the smallest threshold that catches every error
  (the largest confidence of a wrong answer, plus a step), the threshold with the best
  precision among those that flag something beyond the abstentions, and the smallest
  threshold that catches at least a target share of the errors (``--target``, default 0.8,
  the error-coverage target Microsoft's Content Understanding guidance makes the business
  decision). The useful range of a threshold is between the smallest confidence any
  non-ambiguous answer carries (below it the threshold flags nothing) and the largest
  confidence of a wrong answer (above it, nothing more is caught).
* **Regression**: at :data:`ROOM_REVIEW_THRESHOLD` the sweep's flagged set must equal what
  the production code path flags on the same answers: every stored answer is rebuilt as a
  :class:`~basis.vision.schemas.PhotoAnalysis` with the review fields cleared and passed
  through :func:`basis.vision.analyze.review`; a run that recorded ``needs_review`` at
  prediction time (``evals.predict_photos`` calls the same function) is also compared with
  what it recorded. Each is a case in ``results.json``.

Every candidate costs $0 (stored answers, no call); the wall time of each is measured. The
run is recorded with :mod:`evals.record` under component ``room_type_threshold``.

Usage, from ``backEnd/``::

    python -m evals.room_type_threshold \\
        --run ~/basis-eval-data/predictions/cost28_surfaces_v2_gpt6luna \\
        --run ~/basis-eval-data/predictions/cost28_surfaces_v2_gpt61sol \\
        --out ~/basis-eval-data/predictions/s81_room_type_threshold
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from basis.vision.analyze import ROOM_REVIEW_THRESHOLD, review
from basis.vision.rooms import name_room_type, room_name
from basis.vision.schemas import PhotoAnalysis, RoomType
from evals import datasets as ds
from evals import record
from evals.room_metadata import LABEL_TYPES, load_answers

COMPONENT: Final = "room_type_threshold"
AMBIGUOUS: Final = RoomType.AMBIGUOUS.value
DEFAULT_THRESHOLDS: Final[tuple[float, ...]] = tuple(round(i * 0.05, 2) for i in range(21))
# Bin edges; the confidences cluster at 0.95 and above, so the top is split finer. The last
# bin is closed at 1.0.
DEFAULT_BINS: Final[tuple[float, ...]] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0)
DEFAULT_TARGET: Final = 0.8
ALL_RUNS: Final = "all runs"

Key = tuple[str, str, int]  # (run, file, repeat)


@dataclass(frozen=True, slots=True)
class Answer:
    """One stored per-photo answer with its truth and what the file name says."""

    run: str
    file: str
    repeat: int
    room_type: str
    confidence: float
    truth: str
    name_type: str | None
    recorded_flag: bool
    recorded_reason: str | None

    @property
    def key(self) -> Key:
        return (self.run, self.file, self.repeat)

    @property
    def ambiguous(self) -> bool:
        return self.room_type == AMBIGUOUS

    @property
    def wrong(self) -> bool:
        return self.room_type != self.truth


@dataclass(frozen=True, slots=True)
class RuleResult:
    """A flagging rule scored on a set of answers."""

    rule: str
    answers: int
    errors: int
    flagged: int
    flagged_ambiguous: int
    flagged_by_rule: int  # flagged and not ambiguous: what the rule adds beyond abstentions
    caught: int  # wrong among the flagged
    missed: int  # wrong among the unflagged
    seconds: float

    @property
    def precision(self) -> float:
        return self.caught / self.flagged if self.flagged else math.nan

    @property
    def flag_rate(self) -> float:
        return self.flagged / self.answers if self.answers else math.nan

    @property
    def error_recall(self) -> float:
        return self.caught / self.errors if self.errors else math.nan

    @property
    def cost(self) -> float:
        """Photos flagged per error caught (lower is better)."""
        return self.flagged / self.caught if self.caught else math.inf

    @property
    def unflagged_accuracy(self) -> float:
        unflagged = self.answers - self.flagged
        return (unflagged - self.missed) / unflagged if unflagged else math.nan

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "precision": self.precision,
            "flag_rate": self.flag_rate,
            "error_recall": self.error_recall,
            "cost": self.cost,
            "unflagged_accuracy": self.unflagged_accuracy,
        }


# --------------------------------------------------------------------------- loading


def load_truth(labels_dir: Path) -> dict[str, str]:
    """``{file: room type}`` in the model's vocabulary from ``photo_rooms_v1.json``."""
    data = json.loads((labels_dir / ds.DATASET_FILES["photo_rooms"]).read_text(encoding="utf-8"))
    truth: dict[str, str] = {}
    for item in data["items"]:
        room_type = str(item["room_type"])
        truth[str(item["file"])] = LABEL_TYPES.get(room_type, room_type)
    return truth


def answers_of(
    run_dir: Path, truth: Mapping[str, str], *, run_name: str | None = None
) -> tuple[list[Answer], list[str]]:
    """The run's answers with a label, and the files skipped (no analysis or no label)."""
    name = run_name or run_dir.name
    answers: list[Answer] = []
    skipped: list[str] = []
    for repeat, analyses in sorted(load_answers(run_dir).items()):
        for file, analysis in analyses.items():
            if analysis is None or file not in truth:
                skipped.append(f"{file}#{repeat}")
                continue
            named = name_room_type(room_name(file))
            answers.append(
                Answer(
                    run=name,
                    file=file,
                    repeat=repeat,
                    room_type=analysis.room_type.value,
                    confidence=analysis.room_confidence,
                    truth=truth[file],
                    name_type=None if named is None else named.value,
                    recorded_flag=analysis.needs_review,
                    recorded_reason=analysis.review_reason,
                )
            )
    return answers, skipped


def run_models(run_dir: Path) -> str:
    """The model whose confidences a run holds, from its ``run.json`` (or "not recorded")."""
    path = run_dir / "run.json"
    if not path.is_file():
        return "not recorded in the run"
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "not recorded in the run"
    reported = summary.get("models_reported") or []
    model = ", ".join(str(m) for m in reported) or str(summary.get("model_requested") or "")
    if not model:
        return "not recorded in the run"
    effort = summary.get("reasoning_effort")
    prompt = summary.get("prompt")
    parts = [f"`{model}`"]
    if effort:
        parts.append(f"effort {effort}")
    text = " (".join(parts) + (")" if len(parts) > 1 else "")
    return f"{text}, prompt {prompt}" if prompt else text


# --------------------------------------------------------------------------- rules

Flagger = Callable[[Answer], bool]


def threshold_flag(answer: Answer, threshold: float) -> bool:
    """The production rule at ``threshold``: ambiguous, or confidence below it."""
    return answer.ambiguous or answer.confidence < threshold


def production_flag(answer: Answer) -> bool:
    """What :func:`basis.vision.analyze.review` flags for this answer: the stored answer
    rebuilt as a :class:`PhotoAnalysis` with the review fields cleared (so a flag recorded
    at prediction time does not short-circuit the check) and passed through the production
    function. The regression compares this with :func:`threshold_flag` at
    :data:`ROOM_REVIEW_THRESHOLD`."""
    analysis = PhotoAnalysis(
        room_type=RoomType(answer.room_type),
        room_confidence=answer.confidence,
        needs_review=False,
        review_reason=None,
    )
    return review(analysis).needs_review


def name_disagrees(answer: Answer) -> bool:
    """The file name says a room type and the answer is not it (``ambiguous`` disagrees)."""
    return answer.name_type is not None and answer.room_type != answer.name_type


def name_silent_or_disagrees(answer: Answer) -> bool:
    return answer.name_type is None or answer.room_type != answer.name_type


def disagreement_flags(answers: Sequence[Answer], *, across: str) -> set[Key]:
    """Keys flagged because another answer of the same photo disagrees: ``across="repeats"``
    compares the repeats of one run, ``across="runs"`` the runs for one file and repeat."""
    groups: dict[tuple[str, ...], list[Answer]] = defaultdict(list)
    for answer in answers:
        group = (
            (answer.run, answer.file) if across == "repeats" else (answer.file, str(answer.repeat))
        )
        groups[group].append(answer)
    flagged: set[Key] = set()
    for members in groups.values():
        types = {m.room_type for m in members}
        if len(types) > 1:
            flagged.update(m.key for m in members)
    return flagged


def score(
    rule: str, answers: Sequence[Answer], flagged: Iterable[Key], seconds: float
) -> RuleResult:
    flagged_keys = set(flagged)
    errors = sum(a.wrong for a in answers)
    hit = [a for a in answers if a.key in flagged_keys]
    caught = sum(a.wrong for a in hit)
    ambiguous = sum(a.ambiguous for a in hit)
    return RuleResult(
        rule=rule,
        answers=len(answers),
        errors=errors,
        flagged=len(hit),
        flagged_ambiguous=ambiguous,
        flagged_by_rule=len(hit) - ambiguous,
        caught=caught,
        missed=errors - caught,
        seconds=seconds,
    )


def score_flagger(rule: str, answers: Sequence[Answer], flagger: Flagger) -> RuleResult:
    t0 = time.perf_counter()
    flagged = [a.key for a in answers if flagger(a)]
    return score(rule, answers, flagged, time.perf_counter() - t0)


def _at(threshold: float) -> Flagger:
    def flag(answer: Answer) -> bool:
        return threshold_flag(answer, threshold)

    return flag


def sweep(answers: Sequence[Answer], thresholds: Sequence[float]) -> list[RuleResult]:
    return [score_flagger(f"threshold {t:.2f}", answers, _at(t)) for t in thresholds]


def alternative_rules(answers: Sequence[Answer], *, runs: int) -> list[RuleResult]:
    """The alternatives and their combinations with the production threshold."""
    production = ROOM_REVIEW_THRESHOLD
    results = [
        score_flagger(
            f"threshold {production:.2f} (production)",
            answers,
            lambda a: threshold_flag(a, production),
        ),
        score_flagger("ambiguous only (threshold 0.00)", answers, lambda a: a.ambiguous),
        score_flagger("file name disagrees", answers, name_disagrees),
        score_flagger("file name silent or disagrees", answers, name_silent_or_disagrees),
    ]
    t0 = time.perf_counter()
    repeats = disagreement_flags(answers, across="repeats")
    results.append(score("repeats disagree", answers, repeats, time.perf_counter() - t0))
    if runs > 1:
        t0 = time.perf_counter()
        models = disagreement_flags(answers, across="runs")
        results.append(
            score("models disagree (across the runs)", answers, models, time.perf_counter() - t0)
        )
    t0 = time.perf_counter()
    combined = {a.key for a in answers if threshold_flag(a, production) or name_disagrees(a)}
    results.append(
        score(
            f"threshold {production:.2f} or file name disagrees",
            answers,
            combined,
            time.perf_counter() - t0,
        )
    )
    t0 = time.perf_counter()
    combined = {a.key for a in answers if threshold_flag(a, production)} | repeats
    results.append(
        score(
            f"threshold {production:.2f} or repeats disagree",
            answers,
            combined,
            time.perf_counter() - t0,
        )
    )
    return results


# --------------------------------------------------------------------------- calibration


def _bin_index(confidence: float, edges: Sequence[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= confidence < edges[i + 1]:
            return i
    return len(edges) - 2  # the last bin is closed at the top edge


def auroc(answers: Sequence[Answer]) -> float:
    """Probability a right answer carries a higher confidence than a wrong one (ties half):
    the Mann-Whitney statistic on the confidences."""
    right = [a.confidence for a in answers if not a.wrong]
    wrong = [a.confidence for a in answers if a.wrong]
    if not right or not wrong:
        return math.nan
    total = 0.0
    for r in right:
        for w in wrong:
            total += 1.0 if r > w else 0.5 if r == w else 0.0
    return total / (len(right) * len(wrong))


def calibration(answers: Sequence[Answer], edges: Sequence[float]) -> dict[str, Any]:
    """Per-bin accuracy against confidence, the expected calibration error, the Brier score
    and the AUROC of the confidence (:func:`auroc`). ``ambiguous`` answers are included as
    wrong at the confidence they carry."""
    t0 = time.perf_counter()
    bins: list[dict[str, Any]] = [
        {"low": edges[i], "high": edges[i + 1], "n": 0, "right": 0, "confidence_sum": 0.0}
        for i in range(len(edges) - 1)
    ]
    brier = 0.0
    for a in answers:
        b = bins[_bin_index(a.confidence, edges)]
        b["n"] += 1
        b["right"] += not a.wrong
        b["confidence_sum"] += a.confidence
        brier += (a.confidence - (0.0 if a.wrong else 1.0)) ** 2
    n = len(answers)
    ece = 0.0
    rows: list[dict[str, Any]] = []
    for b in bins:
        count = int(b["n"])
        accuracy = b["right"] / count if count else math.nan
        mean = b["confidence_sum"] / count if count else math.nan
        gap = accuracy - mean if count else math.nan
        if count:
            ece += count / n * abs(gap)
        rows.append(
            {
                "low": b["low"],
                "high": b["high"],
                "n": count,
                "right": int(b["right"]),
                "accuracy": accuracy,
                "mean_confidence": mean,
                "gap": gap,
            }
        )
    return {
        "bins": rows,
        "n": n,
        "accuracy": sum(not a.wrong for a in answers) / n if n else math.nan,
        "mean_confidence": sum(a.confidence for a in answers) / n if n else math.nan,
        "ece": ece if n else math.nan,
        "brier": brier / n if n else math.nan,
        "auroc": auroc(answers),
        "seconds": time.perf_counter() - t0,
    }


# --------------------------------------------------------------------------- operating points


def operating_points(
    answers: Sequence[Answer],
    results: Sequence[RuleResult],
    thresholds: Sequence[float],
    target: float,
) -> dict[str, Any]:
    """The best thresholds by stated rules, and whether the production value is in the
    range where a threshold does anything (module doc)."""
    confident = [a for a in answers if not a.ambiguous]
    lowest = min((a.confidence for a in confident), default=math.nan)
    highest_wrong = max((a.confidence for a in confident if a.wrong), default=math.nan)
    by_threshold = dict(zip(thresholds, results, strict=True))

    def first(predicate: Callable[[RuleResult], bool]) -> float | None:
        return next((t for t in thresholds if predicate(by_threshold[t])), None)

    catches_all = first(lambda r: r.missed == 0)
    meets_target = first(lambda r: r.errors > 0 and r.error_recall >= target)
    adding = [t for t in thresholds if by_threshold[t].flagged_by_rule > 0]
    best_precision = max(adding, key=lambda t: (by_threshold[t].precision, -t)) if adding else None
    step = min((b - a for a, b in zip(thresholds, thresholds[1:], strict=False)), default=math.nan)
    return {
        "lowest_confidence_of_a_confident_answer": lowest,
        "highest_confidence_of_a_wrong_confident_answer": highest_wrong,
        "useful_range": [lowest, highest_wrong],
        "production_threshold": ROOM_REVIEW_THRESHOLD,
        "production_in_useful_range": bool(
            not math.isnan(lowest)
            and not math.isnan(highest_wrong)
            and lowest < ROOM_REVIEW_THRESHOLD <= highest_wrong + step
        ),
        "production_flags_beyond_ambiguous": by_threshold.get(
            ROOM_REVIEW_THRESHOLD,
            score_flagger("", answers, lambda a: threshold_flag(a, ROOM_REVIEW_THRESHOLD)),
        ).flagged_by_rule,
        "smallest_threshold_catching_every_error": catches_all,
        f"smallest_threshold_catching_{int(target * 100)}pct_of_errors": meets_target,
        "target": target,
        "best_precision_threshold": best_precision,
    }


# --------------------------------------------------------------------------- tables

_MODELS_COLUMN: Final = (
    "- **Models**: the exact model id, its provider, the reasoning effort and the temperature "
    'as sent. "temperature default" means the model takes no temperature (reasoning models), '
    "so the provider's default applies."
)
_TOKENS_COLUMN: Final = "- **Tokens (input / output)**: None (stored answers, no new call)."
_TOKENS_CELL: Final = "None (stored answers, no new call)"

_HOW_CALIBRATION: Final = (
    "**How to read this table.** Is the model's self-reported room confidence worth anything "
    "as a signal of a wrong room type?",
    "",
    _MODELS_COLUMN,
    "- **Confidence bin**: the self-reported `room_confidence` range the answers fall in (the "
    "last bin includes 1.0).",
    "- **Answers**: how many stored answers fall in the bin (photos times repeats).",
    "- **Right** (higher is better): the share of the bin's answers whose room type matches "
    "the label (`ambiguous` is not a match).",
    "- **Mean confidence**: the average confidence the model reported in the bin.",
    "- **Gap**: right minus mean confidence; negative means the model is more confident than "
    "it is right (overconfident), positive the reverse. Closer to zero is better.",
    _TOKENS_COLUMN,
    "",
)

_HOW_SWEEP: Final = (
    "**How to read this table.** At each threshold, what the review flag costs the engineer "
    "and what it catches.",
    "",
    _MODELS_COLUMN,
    '- **Rule**: the flag tested. "threshold t" is the production rule with the floor set to '
    "t: a photo is flagged when its answer is `ambiguous` or its confidence is below t.",
    "- **Flagged**: photos sent to the engineer, out of the answers scored (photos times "
    "repeats); in brackets, how many of them the rule added beyond the `ambiguous` answers, "
    "which the production rule flags at every threshold.",
    "- **Flag rate** (lower is better, for a given catch): the share of answers flagged.",
    "- **Errors caught** (higher is better): wrong room types among the flagged, out of all "
    "wrong room types (`ambiguous` counts as wrong).",
    "- **Errors missed** (lower is better): wrong room types among the unflagged, which reach "
    "the engineer as if right.",
    "- **Precision of the flag** (higher is better): of the flagged photos, the share that "
    "were wrong; the rest are the engineer's wasted looks.",
    "- **Photos per error caught** (lower is better): the review cost: how many photos the "
    "engineer looks at for each wrong type found.",
    "- **Right among unflagged** (higher is better): the accuracy of what passes without review.",
    "- **Wall time**: seconds to score the rule on the stored answers (no model call).",
    _TOKENS_COLUMN,
    "",
)

_CALIBRATION_HEADER: Final = (
    "| Setup | Models (provider, effort, temperature) | Confidence bin | Answers | Right ↑ | "
    "Mean confidence | Gap | Tokens (input / output) |",
    "|---|---|---|---|---|---|---|---|",
)
_RULES_HEADER: Final = (
    "| Setup | Models (provider, effort, temperature) | Rule | Flagged (of answers) [added by "
    "the rule] | Flag rate ↓ | Errors caught ↑ | Errors missed ↓ | Precision of the flag ↑ | "
    "Photos per error caught ↓ | Right among unflagged ↑ | Wall time | Tokens (input / output) |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|",
)


def _pct(value: float) -> str:
    return "–" if math.isnan(value) else f"{value:.1%}"


def _num(value: float, digits: int = 3) -> str:
    if math.isnan(value):
        return "–"
    if math.isinf(value):
        return "∞"
    return f"{value:.{digits}f}"


def _bin_label(row: Mapping[str, Any]) -> str:
    return f"{row['low']:.2f}–{row['high']:.2f}"


def render_calibration(models: str, calib: Mapping[str, Any]) -> str:
    lines = [*_HOW_CALIBRATION, *_CALIBRATION_HEADER]
    for row in calib["bins"]:
        if not row["n"]:
            continue
        cells = [
            f"stored answers, confidence {_bin_label(row)}",
            models,
            _bin_label(row),
            str(row["n"]),
            f"{_pct(row['accuracy'])} ({row['right']} of {row['n']})",
            _num(row["mean_confidence"]),
            _num(row["gap"], 3),
            _TOKENS_CELL,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(
        f"\nOver all {calib['n']} answers: right {_pct(calib['accuracy'])}, mean confidence "
        f"{_num(calib['mean_confidence'])}, expected calibration error {_num(calib['ece'])}, "
        f"Brier score {_num(calib['brier'])}, AUROC of the confidence {_num(calib['auroc'])} "
        f"(0.5 = no separation of right from wrong; 1.0 = perfect). "
        f"Wall time {calib['seconds']:.4f} s.\n"
    )
    return "\n".join(lines)


def render_rules(models: str, results: Sequence[RuleResult], *, title: str) -> str:
    lines = [*_HOW_SWEEP, f"*{title}*", "", *_RULES_HEADER]
    for r in results:
        cells = [
            f"stored answers, {r.rule}",
            models,
            r.rule,
            f"{r.flagged} of {r.answers} [{r.flagged_by_rule}]",
            _pct(r.flag_rate),
            f"{r.caught} of {r.errors} ({_pct(r.error_recall)})",
            str(r.missed),
            _pct(r.precision),
            _num(r.cost, 1),
            _pct(r.unflagged_accuracy),
            f"{r.seconds * 1000:.2f} ms",
            _TOKENS_CELL,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the run


def evaluate(
    answers: Sequence[Answer],
    *,
    thresholds: Sequence[float],
    bins: Sequence[float],
    target: float,
    runs: int,
) -> dict[str, Any]:
    """Everything for one set of answers (one run, or all runs together)."""
    swept = sweep(answers, thresholds)
    return {
        "answers": len(answers),
        "calibration": calibration(answers, bins),
        "sweep": [r.as_dict() for r in swept],
        "rules": [r.as_dict() for r in alternative_rules(answers, runs=runs)],
        "operating_points": operating_points(answers, swept, thresholds, target),
    }


def regression(answers: Sequence[Answer]) -> dict[str, Any]:
    """The sweep's flag at the production threshold against the production code path, and
    against the flag the run recorded at prediction time when it recorded one."""
    t0 = time.perf_counter()
    rule = {a.key for a in answers if threshold_flag(a, ROOM_REVIEW_THRESHOLD)}
    production = {a.key for a in answers if production_flag(a)}
    recorded = {a.key for a in answers if a.recorded_flag}
    reasons = sorted({str(a.recorded_reason) for a in answers if a.recorded_flag})
    return {
        "threshold": ROOM_REVIEW_THRESHOLD,
        "rule_flagged": len(rule),
        "production_flagged": len(production),
        "equal": rule == production,
        "only_rule": sorted(k[1] + f"#{k[2]}" for k in rule - production),
        "only_production": sorted(k[1] + f"#{k[2]}" for k in production - rule),
        "recorded_flagged": len(recorded),
        "recorded_reasons": reasons,
        "recorded_equal": rule == recorded if recorded else None,
        "seconds": time.perf_counter() - t0,
    }


def _thresholds(values: Sequence[float] | None) -> tuple[float, ...]:
    if not values:
        return DEFAULT_THRESHOLDS
    out = sorted({round(float(v), 4) for v in values})
    if ROOM_REVIEW_THRESHOLD not in out:
        out.append(ROOM_REVIEW_THRESHOLD)
    return tuple(sorted(out))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.room_type_threshold", description=__doc__
    )
    parser.add_argument(
        "--run",
        type=Path,
        action="append",
        required=True,
        help="a stored per-photo run (repeatable)",
    )
    parser.add_argument(
        "--labels", type=Path, default=None, help="default: $BASIS_EVAL_DATA_DIR/labels"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--thresholds", type=float, nargs="*", default=None, help="default: 0 to 1 by 0.05"
    )
    parser.add_argument("--bins", type=float, nargs="*", default=None, help="calibration bin edges")
    parser.add_argument(
        "--target", type=float, default=DEFAULT_TARGET, help="error share a threshold must catch"
    )
    args = parser.parse_args(argv)
    run = record.EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        component=COMPONENT,
        version=f"threshold-{ROOM_REVIEW_THRESHOLD}",
    )
    try:
        return _main(args, run)
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise


def _main(args: argparse.Namespace, run: record.EvalRun) -> int:
    t0 = time.perf_counter()
    labels = args.labels if args.labels is not None else ds.data_dir() / "labels"
    thresholds = _thresholds(args.thresholds)
    bins = tuple(args.bins) if args.bins else DEFAULT_BINS
    truth = load_truth(labels)
    run.dataset(labels / ds.DATASET_FILES["photo_rooms"])
    all_answers: list[Answer] = []
    per_run: dict[str, dict[str, Any]] = {}
    models: dict[str, str] = {}
    for run_dir in args.run:
        run.input(run_dir.name, run_dir)
        answers, skipped = answers_of(run_dir, truth)
        models[run_dir.name] = run_models(run_dir)
        run.case(
            f"loaded/{run_dir.name}",
            record.PASSED if answers and not skipped else record.TEST_ERROR,
            kind="run",
            detail={"answers": len(answers), "skipped": skipped, "models": models[run_dir.name]},
        )
        all_answers.extend(answers)
        result = evaluate(answers, thresholds=thresholds, bins=bins, target=args.target, runs=1)
        result["models"] = models[run_dir.name]
        result["regression"] = check = regression(answers)
        run.case(
            f"regression/production-path/{run_dir.name}",
            record.PASSED if check["equal"] else record.FAILED,
            kind="regression",
            detail={k: v for k, v in check.items() if k != "recorded_reasons"},
            seconds=check["seconds"],
        )
        run.case(
            f"regression/recorded-flag/{run_dir.name}",
            record.PASSED
            if check["recorded_equal"]
            else record.TEST_ERROR
            if check["recorded_equal"] is None
            else record.FAILED,
            kind="regression",
            detail={
                "recorded_flagged": check["recorded_flagged"],
                "recorded_reasons": check["recorded_reasons"],
                "note": "the run recorded no needs_review flag"
                if check["recorded_equal"] is None
                else None,
            },
        )
        per_run[run_dir.name] = result
    combined = evaluate(
        all_answers, thresholds=thresholds, bins=bins, target=args.target, runs=len(args.run)
    )
    combined["models"] = "; ".join(f"{name}: {m}" for name, m in models.items())
    tables = _tables(per_run, combined)
    (args.out / "tables.md").write_text(tables, encoding="utf-8")
    (args.out / "per_run.json").write_text(_dumps(per_run), encoding="utf-8")
    (args.out / "all_runs.json").write_text(_dumps(combined), encoding="utf-8")
    summary = {
        "runs": [str(p) for p in args.run],
        "answers": len(all_answers),
        "thresholds": list(thresholds),
        "bins": list(bins),
        "target": args.target,
        "spent_usd": 0.0,
        "operating_points": {name: r["operating_points"] for name, r in per_run.items()}
        | {ALL_RUNS: combined["operating_points"]},
        "calibration": {
            name: {
                k: r["calibration"][k]
                for k in ("n", "accuracy", "mean_confidence", "ece", "brier", "auroc")
            }
            for name, r in per_run.items()
        },
        "regression": {name: r["regression"]["equal"] for name, r in per_run.items()},
        "seconds": time.perf_counter() - t0,
    }
    run.note(models=models)
    run.finish(_clean(summary))
    print(tables)
    return 0 if all(r["regression"]["equal"] for r in per_run.values()) else 1


def _tables(per_run: Mapping[str, Mapping[str, Any]], combined: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for name, result in list(per_run.items()) + [(ALL_RUNS, combined)]:
        models = str(result["models"])
        parts.append(f"## {name}\n")
        parts.append(f"### Calibration ({name})\n")
        parts.append(render_calibration(models, result["calibration"]))
        swept = [_result(r) for r in result["sweep"]]
        parts.append(f"### Threshold sweep ({name})\n")
        parts.append(render_rules(models, swept, title="The production rule at every threshold"))
        rules = [_result(r) for r in result["rules"]]
        parts.append(f"### Alternative rules ({name})\n")
        parts.append(
            render_rules(models, rules, title="Other rules, and the threshold combined with them")
        )
        parts.append(f"### Operating points ({name})\n")
        parts.append("```\n" + _dumps(result["operating_points"]) + "```\n")
        if "regression" in result:
            parts.append(f"### Regression ({name})\n")
            parts.append("```\n" + _dumps(result["regression"]) + "```\n")
    return "\n".join(parts)


def _result(data: Mapping[str, Any]) -> RuleResult:
    fields = {k: data[k] for k in RuleResult.__dataclass_fields__}
    return RuleResult(**fields)


def _clean(data: Any) -> Any:
    """JSON-safe: a NaN or infinite float becomes its name as text."""
    if isinstance(data, float):
        return "nan" if math.isnan(data) else "inf" if math.isinf(data) else data
    if isinstance(data, Mapping):
        return {str(k): _clean(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_clean(v) for v in data]
    return data


def _dumps(data: Any) -> str:
    return json.dumps(_clean(data), indent=1, default=str) + "\n"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
