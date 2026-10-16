"""Evaluation metrics, small and owned (BUILD_PLAN 13.2), each unit-tested by hand.

* Field accuracy, exact and normalized, with per-field tolerances (text exact after
  normalization, integers +/-1, currency +/-0.5% by default).
* Per-class precision/recall/F1, macro-F1, accuracy with a Wilson interval, and an
  explicit abstention label scored as abstention (coverage and selective accuracy).
* IR: nDCG@k, recall@k and reciprocal rank are :mod:`basis.retrieval.metrics` (linear
  gains, log2 discounts as trec_eval's ``ndcg_cut``); graded qrels at doc+page. A query
  with no relevant judgment is excluded from the means, as trec_eval does.
* Citation support rate (verified / claimed), and the status of a ``(doc_id, page,
  excerpt)`` claim against page text (the rules table's citations, as
  :func:`basis.classify.router.resolve_rule_citations` checks them).
* Standard error of a mean clustered by document (CR1: ``G/(G-1)`` small-sample factor),
  because photos and fields from one property are not independent.
* pass^k (all of k repeats succeed), with the unbiased estimator
  ``C(c, k) / C(n, k)`` per item when n repeats ran.
* Room inventories (VISION_RESEARCH.md section 3, metric 3): per (room, label) over the
  union of gold and predicted labels, count MAE, exact-count rate and signed bias
  (predicted - gold: over-counting is the duplicate failure, so the sign matters), with
  standard errors clustered by room; per room, exact inventory and label-set
  precision/recall/F1 (counts ignored).

"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from basis.classify.verify import excerpt_supported
from basis.retrieval.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from basis.retrieval.verify import MIN_EXCERPT_CHARS
from basis.retrieval.verify import normalize_text as normalize_excerpt

FieldType = Literal["text", "int", "float", "currency", "percent", "date", "bool"]

DEFAULT_TOLERANCE: Final[dict[str, tuple[float, float]]] = {
    # type -> (absolute, relative)
    "text": (0.0, 0.0),
    "int": (1.0, 0.0),
    "float": (0.01, 0.0),
    "currency": (0.0, 0.005),
    "percent": (0.0, 0.0),
    "date": (0.0, 0.0),
    "bool": (0.0, 0.0),
}
Z_95: Final = 1.959963984540054

_WS: Final = re.compile(r"\s+")
_NUMBER: Final = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+")
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y")
_TRUE: Final = frozenset({"true", "yes", "y", "x", "1"})
_FALSE: Final = frozenset({"false", "no", "n", "0", ""})


# --------------------------------------------------------------------------- fields


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return _WS.sub(" ", text).strip().rstrip(".")


def parse_number(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    match = _NUMBER.search(str(value))
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = normalize_text(value)
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def within(predicted: Decimal, expected: Decimal, absolute: float, relative: float) -> bool:
    diff = abs(predicted - expected)
    if diff <= Decimal(str(absolute)):
        return True
    return relative > 0 and diff <= abs(expected) * Decimal(str(relative))


def field_matches(
    predicted: object,
    expected: object,
    kind: FieldType,
    *,
    absolute: float | None = None,
    relative: float | None = None,
) -> tuple[bool, bool]:
    """``(exact, normalized)`` for one field. A missing prediction matches neither.

    Exact: the string forms are equal. Normalized: type-aware comparison (text folded for
    case/whitespace, numbers parsed from printed forms like ``$4,321`` and compared within
    the tolerance, dates in common formats, booleans as yes/no).
    """
    if predicted is None:
        return False, False
    exact = str(predicted) == str(expected)
    default_abs, default_rel = DEFAULT_TOLERANCE[kind]
    abs_tol = default_abs if absolute is None else absolute
    rel_tol = default_rel if relative is None else relative
    if kind == "text":
        return exact, normalize_text(predicted) == normalize_text(expected)
    if kind == "date":
        p, e = parse_date(predicted), parse_date(expected)
        return exact, p is not None and p == e
    if kind == "bool":
        pb, eb = parse_bool(predicted), parse_bool(expected)
        return exact, pb is not None and pb == eb
    pn, en = parse_number(predicted), parse_number(expected)
    if pn is None or en is None:
        return exact, False
    return exact, within(pn, en, abs_tol, rel_tol)


@dataclass(frozen=True)
class FieldScore:
    field: str
    exact: bool
    normalized: bool
    cluster: str = ""


@dataclass(frozen=True)
class FieldAccuracy:
    n: int
    exact: float
    normalized: float
    per_field: dict[str, tuple[int, int, int]]  # field -> (n, exact hits, normalized hits)


def field_accuracy(scores: Sequence[FieldScore]) -> FieldAccuracy:
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for s in scores:
        row = per[s.field]
        row[0] += 1
        row[1] += s.exact
        row[2] += s.normalized
    n = len(scores)
    return FieldAccuracy(
        n=n,
        exact=sum(s.exact for s in scores) / n if n else math.nan,
        normalized=sum(s.normalized for s in scores) / n if n else math.nan,
        per_field={k: (v[0], v[1], v[2]) for k, v in sorted(per.items())},
    )


# --------------------------------------------------------------------------- classes


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a proportion; ``(nan, nan)`` when ``n`` is 0."""
    if n <= 0:
        return math.nan, math.nan
    if not 0 <= successes <= n:
        raise ValueError(f"successes {successes} not in 0..{n}")
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


@dataclass(frozen=True)
class ClassScore:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationMetrics:
    n: int
    accuracy: float
    accuracy_interval: tuple[float, float]
    macro_f1: float
    per_class: dict[str, ClassScore]
    abstentions: int = 0
    coverage: float = 1.0
    selective_accuracy: float = math.nan
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    abstain: str | None = None,
) -> ClassificationMetrics:
    """Per-class P/R/F1 over ``labels`` (default: every class seen in truth or prediction,
    except the abstention label unless it is also a true class).

    A prediction equal to ``abstain`` is an abstention: it counts as wrong for accuracy
    unless the truth is also ``abstain``; ``coverage`` is the share not abstained and
    ``selective_accuracy`` the accuracy on those. A class with no predictions has
    precision 0 (scikit-learn's ``zero_division=0``).
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred differ in length")
    n = len(y_true)
    if labels is not None:
        classes = list(labels)
    else:  # the true classes plus wrong predictions; an abstention is not a class
        classes = sorted(set(y_true) | ({p for p in y_pred} - {abstain} - {None}))
    confusion: dict[str, dict[str, int]] = {t: defaultdict(int) for t in sorted(set(y_true))}
    for t, p in zip(y_true, y_pred, strict=True):
        confusion[t][p] += 1
    per_class: dict[str, ClassScore] = {}
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p != c)
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        f1 = _ratio(2 * tp, 2 * tp + fp + fn)
        per_class[c] = ClassScore(precision, recall, f1, tp + fn)
    correct = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p)
    abstained = sum(1 for p in y_pred if abstain is not None and p == abstain)
    answered = [(t, p) for t, p in zip(y_true, y_pred, strict=True) if p != abstain]
    selective = sum(1 for t, p in answered if t == p) / len(answered) if answered else math.nan
    return ClassificationMetrics(
        n=n,
        accuracy=correct / n if n else math.nan,
        accuracy_interval=wilson_interval(correct, n),
        macro_f1=sum(s.f1 for s in per_class.values()) / len(per_class) if per_class else math.nan,
        per_class=per_class,
        abstentions=abstained,
        coverage=(n - abstained) / n if n else math.nan,
        selective_accuracy=selective,
        confusion={t: dict(sorted(row.items())) for t, row in confusion.items()},
    )


# --------------------------------------------------------------------------- IR


@dataclass(frozen=True)
class IrMetrics:
    n: int
    # Queries with no relevant judgment: no defined nDCG, recall or reciprocal rank,
    # so they are left out of the means (as trec_eval does) and counted here.
    excluded: tuple[str, ...]
    ndcg_10: float
    recall_5: float
    recall_10: float
    mrr: float
    per_query: dict[str, dict[str, float]]


def ir_metrics(
    run: Mapping[str, Sequence[str]], qrels: Mapping[str, Mapping[str, int]]
) -> IrMetrics:
    """Means over the judged queries; a query missing from ``run`` scores 0, a query
    with no relevant judgment is excluded (``excluded``) rather than averaged as 0."""
    per_query: dict[str, dict[str, float]] = {}
    excluded: list[str] = []
    for qid in sorted(qrels):
        ranking = list(run.get(qid, []))
        judged = qrels[qid]
        if not any(grade > 0 for grade in judged.values()):
            excluded.append(qid)
            continue
        per_query[qid] = {
            "ndcg_10": ndcg_at_k(ranking, judged, 10),
            "recall_5": recall_at_k(ranking, judged, 5),
            "recall_10": recall_at_k(ranking, judged, 10),
            "mrr": reciprocal_rank(ranking, judged),
        }
    n = len(per_query)

    def mean(key: str) -> float:
        return sum(q[key] for q in per_query.values()) / n if n else math.nan

    return IrMetrics(
        n,
        tuple(excluded),
        mean("ndcg_10"),
        mean("recall_5"),
        mean("recall_10"),
        mean("mrr"),
        per_query,
    )


# --------------------------------------------------------------------------- citations


def citation_support_rate(pairs: Iterable[tuple[int, int]]) -> tuple[float, int]:
    """``(verified / claimed, claimed)`` over ``(claimed, verified)`` pairs (micro)."""
    claimed = verified = 0
    for c, v in pairs:
        if v > c or c < 0 or v < 0:
            raise ValueError(f"verified {v} cannot exceed claimed {c}")
        claimed += c
        verified += v
    return (verified / claimed if claimed else math.nan), claimed


PageCitationStatus = Literal[
    "resolved", "excerpt_too_short", "document_missing", "page_out_of_range", "excerpt_not_on_page"
]


def page_citation_status(
    excerpt: str, page: int, pages: Sequence[str] | None
) -> PageCitationStatus:
    """Whether a ``(doc, page, excerpt)`` claim holds against the document's page texts.

    ``pages`` is the document's text per page (index 0 is page 1), ``None`` when the
    document is not in the corpus. The claim resolves when the page exists and
    :func:`basis.classify.verify.excerpt_supported` accepts the excerpt on that page's
    text (normalized substring, at least ``MIN_EXCERPT_CHARS`` normalized characters):
    the checks ``resolve_rule_citations`` applies to the chunk it finds, at page level.
    """
    if len(normalize_excerpt(excerpt)) < MIN_EXCERPT_CHARS:
        return "excerpt_too_short"
    if pages is None:
        return "document_missing"
    if not 1 <= page <= len(pages):
        return "page_out_of_range"
    return "resolved" if excerpt_supported(excerpt, pages[page - 1]) else "excerpt_not_on_page"


# --------------------------------------------------------------------------- variance


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def clustered_standard_error(values: Sequence[float], clusters: Sequence[Hashable]) -> float:
    """Cluster-robust (CR1) standard error of the mean of ``values``.

    ``SE^2 = G/(G-1) * sum_g (sum_{i in g} (x_i - mean))^2 / n^2`` with ``G`` clusters.
    With every observation its own cluster this is the usual ``s / sqrt(n)``. ``nan`` for
    fewer than two clusters (not estimable).
    """
    if len(values) != len(clusters):
        raise ValueError("values and clusters differ in length")
    n = len(values)
    groups: dict[Hashable, float] = defaultdict(float)
    center = mean(values)
    for x, g in zip(values, clusters, strict=True):
        groups[g] += x - center
    g_count = len(groups)
    if g_count < 2:
        return math.nan
    variance = g_count / (g_count - 1) * sum(s * s for s in groups.values()) / (n * n)
    return math.sqrt(variance)


def pass_hat_k(outcomes: Sequence[Sequence[bool]], k: int) -> float:
    """Mean over items of ``C(c, k) / C(n, k)``: the chance all of k repeats succeed."""
    if k < 1:
        raise ValueError("k must be at least 1")
    scores = []
    for runs in outcomes:
        n, c = len(runs), sum(bool(r) for r in runs)
        if n < k:
            raise ValueError(f"an item has {n} repeats, fewer than k={k}")
        scores.append(math.comb(c, k) / math.comb(n, k))
    return mean(scores)


def summarize_proportion(
    successes: Sequence[bool], clusters: Sequence[Hashable] | None = None
) -> dict[str, Any]:
    """Mean, Wilson interval and (clustered) standard error of a list of outcomes."""
    values = [1.0 if s else 0.0 for s in successes]
    n = len(values)
    low, high = wilson_interval(int(sum(values)), n)
    groups = list(clusters) if clusters is not None else list(range(n))
    return {
        "value": mean(values),
        "n": n,
        "interval": [low, high],
        "stderr": clustered_standard_error(values, groups) if n else math.nan,
    }


# --------------------------------------------------------------------------- inventories


@dataclass(frozen=True)
class RoomInventoryScore:
    """One room: ``(label, gold, predicted)`` per label in either inventory, label order."""

    pairs: tuple[tuple[str, int, int], ...]
    exact: bool
    precision: float
    recall: float
    f1: float


def _present(inventory: Mapping[str, int]) -> dict[str, int]:
    for label, count in inventory.items():
        if count < 0:
            raise ValueError(f"negative count {count} for {label!r}")
    return {label: count for label, count in inventory.items() if count > 0}


def room_inventory_score(
    gold: Mapping[str, int], predicted: Mapping[str, int]
) -> RoomInventoryScore:
    """Score one room. A label with count 0 is absent. Label-set precision/recall/F1 use
    scikit-learn's ``zero_division=0`` (nothing predicted -> precision 0), except that two
    empty inventories agree perfectly (1, 1, 1)."""
    truth, guess = _present(gold), _present(predicted)
    labels = sorted(set(truth) | set(guess))
    pairs = tuple((label, truth.get(label, 0), guess.get(label, 0)) for label in labels)
    if not truth and not guess:
        return RoomInventoryScore(pairs, True, 1.0, 1.0, 1.0)
    tp = len(set(truth) & set(guess))
    return RoomInventoryScore(
        pairs=pairs,
        exact=truth == guess,
        precision=_ratio(tp, len(guess)),
        recall=_ratio(tp, len(truth)),
        f1=_ratio(2 * tp, len(guess) + len(truth)),
    )


@dataclass(frozen=True)
class InventoryMetrics:
    rooms: int
    pairs: int
    count_mae: float
    count_mae_se: float
    exact_count_rate: float
    exact_count_se: float
    signed_bias: float
    signed_bias_se: float
    room_exact_rate: float
    label_precision: float  # macro over rooms
    label_recall: float
    label_f1: float
    per_room: dict[str, RoomInventoryScore]


def inventory_metrics(
    gold: Mapping[str, Mapping[str, int]], predicted: Mapping[str, Mapping[str, int]]
) -> InventoryMetrics:
    """Per-(room, label) count metrics and per-room inventory metrics over ``gold``'s rooms.

    A room missing from ``predicted`` has an empty inventory (every gold label is
    predicted 0); predicted rooms that are not in ``gold`` are ignored (the caller reports
    them). Pair metrics (count MAE, exact-count rate, signed bias) are means over every
    (room, label) in the union of the room's gold and predicted labels, with standard
    errors clustered by room (pairs of one room are not independent). Room metrics (exact
    inventory, label-set precision/recall/F1) are means over rooms.
    """
    per_room = {room: room_inventory_score(gold[room], predicted.get(room, {})) for room in gold}
    errors: list[float] = []
    hits: list[float] = []
    signed: list[float] = []
    clusters: list[str] = []
    for room, score in per_room.items():
        for _, truth, guess in score.pairs:
            errors.append(float(abs(guess - truth)))
            hits.append(1.0 if guess == truth else 0.0)
            signed.append(float(guess - truth))
            clusters.append(room)
    scores = list(per_room.values())
    return InventoryMetrics(
        rooms=len(per_room),
        pairs=len(errors),
        count_mae=mean(errors),
        count_mae_se=clustered_standard_error(errors, clusters) if errors else math.nan,
        exact_count_rate=mean(hits),
        exact_count_se=clustered_standard_error(hits, clusters) if hits else math.nan,
        signed_bias=mean(signed),
        signed_bias_se=clustered_standard_error(signed, clusters) if signed else math.nan,
        room_exact_rate=mean([1.0 if s.exact else 0.0 for s in scores]),
        label_precision=mean([s.precision for s in scores]),
        label_recall=mean([s.recall for s in scores]),
        label_f1=mean([s.f1 for s in scores]),
        per_room=per_room,
    )
