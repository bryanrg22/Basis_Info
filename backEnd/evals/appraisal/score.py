"""Score appraisal predictions against the truth files (``SPEC.md`` §5 and §8).

Input: the truth sets and a run's ``predictions.json`` (written by :mod:`evals.appraisal.run`).
Output: one dict (``score.json``) with every number, and :func:`render_report` (``report.md``)
with keys, outcomes and counts only, never a document's values or printed text.

Per field (document x key x repeat) one outcome: ``correct``, ``wrong``, ``missed``
(present truth), ``spurious``, ``correct_absent`` (absent / not_developed truth);
``unscorable`` truth is excluded. A predicted value is anything the reviewer would see
(``Columns.has_value``); a value that does not parse as the truth's type is ``wrong``.

Which fields count where:

* the headline (``overall``, every breakdown, page, grounding, display, the review gate)
  is over *verified catalogue* fields of the first repeat of each scored document;
* ``unverified`` and ``extra`` (keys outside the catalogue) are scored and reported apart;
  ``all_fields`` is everything scorable;
* a document whose prediction failed (``missing_recording``, an error) is listed with its
  status and excluded from the field metrics.

Sweep additions (``evals/appraisal/systems``):

* **highlight correct** (:func:`highlight_status`): over correct predictions whose truth has a
  page and a printed value, of a system that reports regions: the field's first region is on
  the truth ``page`` (or ``also_on``) and the PDF text-layer words (pdfplumber) whose centres
  fall inside its box contain the printed value's characters (whitespace and case folded;
  for a checkbox enum or bool, the label of a checked option). A correct prediction with no
  region is a miss (``no_region``); a system with no regions (``production``) is n/a. Next to
  page strict/lenient, overall and by tier, source and origin.
* **usage** (:func:`usage_metrics`): per document, model dollars, tokens and calls from the
  recorder; Azure DI pages per model id priced at :data:`AZURE_USD_PER_1000_PAGES`; wall time
  and the models' own latency, p50/p95 (nearest rank). Replay reports the recorded calls'
  usage, so a replayed run prices the system as the live run did; its wall time is not a
  latency.
* **by origin**: which reader produced the value (``azure``, ``llm``, ``azure_1004``,
  ``llm_reask``, ``vote_agree`` ...), when the prediction says (else its source).

Intervals: field-level rates carry a document-clustered standard error
(:func:`evals.metrics.clustered_standard_error`, CR1); proportions whose unit is the
document (per-key accuracy, document-level gate checks) carry a Wilson 95 % interval when
at least two documents contribute. pass^k is over fields across repeats.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

from basis.extraction.fallback import appears_in
from basis.extraction.field_mappings import FIELD_BY_KEY
from evals.appraisal.normalize import (
    ENUMS,
    Columns,
    anls,
    canonical_display,
    display_matches,
    matches,
    normalize_truth,
    prediction_value,
    review_display,
)
from evals.appraisal.truth import DocumentTruth, FieldTruth, TruthSet
from evals.metrics import clustered_standard_error, mean, pass_hat_k, wilson_interval

SCHEMA: Final = 1
OUTCOMES: Final = ("correct", "wrong", "missed", "spurious", "correct_absent")
GATE_THRESHOLDS: Final = (0.6, 0.7, 0.9)
SUCCESS: Final = frozenset({"correct", "correct_absent"})
BREAKDOWNS: Final = (
    "tier",
    "type",
    "source",
    "origin",
    "section",
    "set",
    "document",
    "critical_flag",
)
HIGHLIGHT_BREAKDOWNS: Final = ("tier", "source", "origin")
HIGHLIGHT_STATUSES: Final = ("correct", "wrong_page", "wrong_box", "no_region")
# Azure DI S0 pay-as-you-go, dollars per 1,000 pages: "S0 Pre-built Pages" $10 (prebuilt-layout
# is a prebuilt model; its keyValuePairs feature is free per the add-on capabilities page),
# Azure retail prices API (prices.azure.com, productName "Azure Document Intelligence",
# eastus), read 2026-10-26. The mortgage models have no meter of their own in that API or on
# the pricing page; they are assumed to bill as "Pre-built Pages" (unverified).
AZURE_USD_PER_1000_PAGES: Final = {"prebuilt-layout": 10.0, "prebuilt-mortgage.us.1004": 10.0}
# Production source names -> the names the report uses (SPEC §8).
SOURCE_NAMES: Final = {"azure": "azure_di"}

CRITICAL_TIER: Final = frozenset(
    {
        "cost_approach.site_value",
        "listing_and_contract.contract_price",
        "listing_and_contract.contract_date",
        "reconciliation.final_market_value",
        "reconciliation.effective_date_of_appraisal",
        "subject.property_address",
    }
)
_COST_APPROACH_NOT_AMOUNTS: Final = frozenset(
    {
        "cost_approach.cost_data_source",
        "cost_approach.quality_rating_from_cost_service",
        "cost_approach.effective_date_of_cost_data",
    }
)
HIGH_TIER: Final = frozenset(
    {
        "improvements.gla_sqft",
        "improvements.basement_area_sqft",
        "improvements.year_built",
        "improvements.effective_age_years",
        "improvements.quality",
        "improvements.condition",
        "improvements.units",
        "improvements.type",
        "improvements.stories",
        "improvements.car_storage",
        "improvements.garage_cars",
        "site.area_sqft",
        "site.area",
        "site.area_acres",
    }
)
MEDIUM_TIER: Final = frozenset(
    {
        "site.zoning_classification",
        "site.zoning_description",
        "site.zoning_compliance",
        "site.highest_and_best_use",
        "improvements.fireplaces",
        "improvements.patio_deck",
        "improvements.additional_features",
        "improvements.appliances",
        "subject.assessors_parcel",
        "subject.tax_year",
        "subject.real_estate_taxes",
        "site.special_flood_hazard",
        "site.flood_zone",
        "site.fema_map_number",
        "site.fema_map_date",
    }
)

PageTexts = Callable[[DocumentTruth], Sequence[str] | None]


@dataclass(frozen=True)
class PageWords:
    """One page's text-layer words (pdfplumber ``extract_words``): ``(text, x0, top, x1,
    bottom)`` in points, with the page size."""

    width: float
    height: float
    words: tuple[tuple[str, float, float, float, float], ...]


PageWordsFor = Callable[[DocumentTruth], Sequence[PageWords] | None]


def tier_for(key: str, override: str | None = None) -> str:
    """The cost-seg tier of a key (SPEC §5); a truth entry's ``tier`` overrides it."""
    if override is not None:
        return override
    if key in CRITICAL_TIER:
        return "critical"
    if key.startswith("cost_approach.") and key not in _COST_APPROACH_NOT_AMOUNTS:
        return "high"
    if key in HIGH_TIER or (key.startswith("improvements.") and "garage" in key):
        return "high"
    if key in MEDIUM_TIER:
        return "medium"
    return "low"


def production_critical(key: str) -> bool:
    spec = FIELD_BY_KEY.get(key)
    return spec is not None and spec.critical


# --------------------------------------------------------------------------- per field


@dataclass(frozen=True)
class FieldResult:
    set: str
    document: str
    key: str
    repeat: int
    outcome: str
    tier: str
    type: str
    source: str
    section: str
    critical_flag: bool
    verified: bool
    extra: bool
    predicted: bool
    confidence: float | None
    named: bool  # named by an issue or a review reason (not a low-confidence reason)
    page_status: str | None  # strict | lenient | wrong | no_page; None when not correct
    grounded: bool | None  # None: no page cited, or no text layer on the cited page
    display_ok: bool | None  # over correct predictions only
    anls: float | None  # text fields with present truth
    origin: str = "none"  # the reader the value came from (the source when not given)
    # correct | wrong_page | wrong_box | no_region over correct predictions; None: n/a
    highlight: str | None = None

    @property
    def error(self) -> bool:
        """An error the review gate should catch: wrong, spurious, or a missed critical."""
        if self.outcome in ("wrong", "spurious"):
            return True
        return self.outcome == "missed" and (self.tier == "critical" or self.critical_flag)

    def flagged(self, threshold: float) -> bool:
        low = self.predicted and self.confidence is not None and self.confidence < threshold
        return low or self.named


def named_keys(issues: Iterable[Mapping[str, Any]], reasons: Iterable[str]) -> set[str]:
    """Keys an issue or a review reason names. ``low_confidence:`` reasons are left out: they
    restate the production threshold, which the gate sweep replaces with its own."""
    keys = {str(k) for issue in issues for k in issue.get("keys", ())}
    for reason in reasons:
        rule, _, rest = reason.partition(":")
        if rule == "low_confidence" or not rest:
            continue
        for part in rest.split(","):
            name = part.split("=", 1)[0].strip()
            if "." in name:
                keys.add(name)
    return keys


def _outcome(truth: FieldTruth, columns: Columns | None) -> tuple[str, Any]:
    predicted = columns is not None and columns.has_value
    if truth.status in ("absent", "not_developed"):
        return ("spurious" if predicted else "correct_absent"), None
    if not predicted or columns is None or truth.type is None:
        return "missed", None
    value = prediction_value(truth.type, columns, key=truth.key)
    expected = normalize_truth(truth.type, truth.value, key=truth.key)
    ok = matches(truth.type, value, expected, tolerance=truth.tolerance)
    return ("correct" if ok else "wrong"), value


def _page_status(truth: FieldTruth, page: int | None) -> str | None:
    if truth.page is None:
        return None  # the truth has no page: not countable
    if page is None:
        return "no_page"
    if page == truth.page:
        return "strict"
    if page in truth.also_on:
        return "lenient"
    return "wrong"


# Selection marks and an "X" box mark before an option (normalize._MARK's glyphs: ballot
# box with X, ballot box with check, black square).
_BOX_GLYPHS: Final = "|".join(chr(c) for c in (0x2612, 0x2611, 0x25A0))
_MARKS: Final = re.compile(rf":(?:un)?selected:|(?<![^\s;,])(?:[xX]|{_BOX_GLYPHS})(?=\s)")
_NO_SPACE: Final = re.compile(r"\s+")
# Typographic single and double quotes read as straight ones.
_HIGHLIGHT_QUOTES: Final = str.maketrans(
    {chr(0x2019): "'", chr(0x2018): "'", chr(0x201C): '"', chr(0x201D): '"'}
)


def fold_highlight(text: str) -> str:
    """NFKC, casefold, typographic quotes straight, all whitespace removed."""
    folded = unicodedata.normalize("NFKC", text).translate(_HIGHLIGHT_QUOTES).casefold()
    return _NO_SPACE.sub("", folded)


def highlight_targets(truth: FieldTruth) -> list[str]:
    """What the highlighted words must contain: the printed value; for a checkbox enum the
    label (or a synonym) of a checked option, for a bool its Yes/No; empty: not checkable."""
    if truth.type in ("enum", "enum_multi"):
        values = truth.value if isinstance(truth.value, list) else [truth.value]
        codes = {v.get("code") if isinstance(v, Mapping) else v for v in values}
        targets = [
            form
            for item in ENUMS.get(truth.key, ())
            if item.code in codes
            for form in (item.label, *item.synonyms)
        ]
        return [t for t in (fold_highlight(t) for t in targets) if t]
    if truth.type == "bool" and isinstance(truth.value, bool):
        return ["yes"] if truth.value else ["no"]
    if truth.printed:
        printed = fold_highlight(_MARKS.sub(" ", truth.printed))
        return [printed] if printed else []
    return []


def words_in(page: PageWords, bbox: Sequence[float]) -> str:
    """The folded text of the words whose centres fall in ``bbox`` (page fractions), in
    reading order (top, then left)."""
    if page.width <= 0 or page.height <= 0 or len(bbox) != 4:
        return ""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    inside = []
    for text, left, top, right, bottom in page.words:
        cx, cy = (left + right) / 2 / page.width, (top + bottom) / 2 / page.height
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            inside.append((round(top, 1), left, text))
    return "".join(fold_highlight(t) for _, _, t in sorted(inside))


def highlight_status(
    truth: FieldTruth,
    regions: Sequence[Mapping[str, Any]] | None,
    words: Sequence[PageWords] | None,
) -> str | None:
    """``correct`` / ``wrong_page`` / ``wrong_box`` / ``no_region`` for a correct prediction
    (module doc); ``None`` when not checkable (no regions reported, no truth page or printed
    value, no text layer)."""
    if regions is None or truth.page is None or words is None:
        return None
    targets = highlight_targets(truth)
    if not targets:
        return None
    if not regions:
        return "no_region"
    first = regions[0]
    page = first.get("page")
    if page != truth.page and page not in truth.also_on:
        return "wrong_page"
    if not isinstance(page, int) or not 0 < page <= len(words):
        return None
    inside = words_in(words[page - 1], first.get("bbox") or ())
    return "correct" if any(t in inside for t in targets) else "wrong_box"


def score_field(
    set_name: str,
    doc: DocumentTruth,
    truth: FieldTruth,
    item: Mapping[str, Any] | None,
    *,
    repeat: int,
    named: set[str],
    pages: Sequence[str] | None,
    words: Sequence[PageWords] | None = None,
) -> FieldResult:
    columns = Columns.from_json(item) if item is not None else None
    outcome, _ = _outcome(truth, columns)
    predicted = columns is not None and columns.has_value
    page = item.get("page") if item is not None and predicted else None
    grounded: bool | None = None
    if predicted and page is not None and columns is not None and pages is not None:
        text = pages[page - 1] if 0 < page <= len(pages) else ""
        if text.strip() and columns.value_text is not None:
            grounded = appears_in(columns.value_text, text)
    display_ok: bool | None = None
    if outcome == "correct" and columns is not None and truth.type is not None:
        expected = canonical_display(truth.type, truth.value, key=truth.key, printed=truth.printed)
        display_ok = display_matches(review_display(columns), expected)
    similarity: float | None = None
    if truth.status == "present" and truth.type == "text":
        text_value = columns.value_text if columns is not None and predicted else None
        similarity = anls(text_value, str(truth.value))
    confidence = item.get("confidence") if item is not None and predicted else None
    raw_source = str(item.get("source")) if item is not None and predicted else "none"
    source = SOURCE_NAMES.get(raw_source, raw_source)
    origin = str(item.get("origin") or source) if item is not None and predicted else "none"
    highlight = None
    if outcome == "correct" and item is not None:
        highlight = highlight_status(truth, item.get("regions"), words)
    return FieldResult(
        set=set_name,
        document=doc.document,
        key=truth.key,
        repeat=repeat,
        outcome=outcome,
        tier=tier_for(truth.key, truth.tier),
        type=truth.type or "untyped",
        source=source,
        section=truth.key.split(".", 1)[0],
        critical_flag=production_critical(truth.key),
        verified=truth.verified,
        extra=truth.extra,
        predicted=predicted,
        confidence=float(confidence) if confidence is not None else None,
        named=truth.key in named,
        page_status=_page_status(truth, page) if outcome == "correct" else None,
        grounded=grounded,
        display_ok=display_ok,
        anls=similarity,
        origin=origin,
        highlight=highlight,
    )


# --------------------------------------------------------------------------- aggregates


def _rate(num: int, den: int) -> float:
    return num / den if den else math.nan


def _clustered(results: Sequence[FieldResult], hit: Callable[[FieldResult], bool]) -> float:
    if not results:
        return math.nan
    values = [1.0 if hit(r) else 0.0 for r in results]
    return clustered_standard_error(values, [(r.set, r.document) for r in results])


def block(results: Sequence[FieldResult]) -> dict[str, Any]:
    """Counts and the four §8 rates (each with a document-clustered SE)."""
    counts = {name: 0 for name in OUTCOMES}
    for r in results:
        counts[r.outcome] += 1
    present = counts["correct"] + counts["wrong"] + counts["missed"]
    absent = counts["spurious"] + counts["correct_absent"]
    predicted = counts["correct"] + counts["wrong"] + counts["spurious"]
    present_rows = [r for r in results if r.outcome in ("correct", "wrong", "missed")]
    predicted_rows = [r for r in results if r.predicted]
    absent_rows = [r for r in results if r.outcome in ("spurious", "correct_absent")]
    documents = {(r.set, r.document) for r in results}
    return {
        "fields": len(results),
        "documents": len(documents),
        **counts,
        "present": present,
        "absent": absent,
        "predicted": predicted,
        "accuracy": _rate(counts["correct"], present),
        "accuracy_se": _clustered(present_rows, lambda r: r.outcome == "correct"),
        "wrong_value_rate": _rate(counts["wrong"] + counts["spurious"], predicted),
        "wrong_value_rate_se": _clustered(
            predicted_rows, lambda r: r.outcome in ("wrong", "spurious")
        ),
        "coverage": _rate(counts["correct"] + counts["wrong"], present),
        "coverage_se": _clustered(present_rows, lambda r: r.outcome != "missed"),
        "spurious_rate": _rate(counts["spurious"], absent),
        "spurious_rate_se": _clustered(absent_rows, lambda r: r.outcome == "spurious"),
    }


def _wilson(successes: int, n: int, documents: int) -> list[float] | None:
    if documents < 2 or n == 0:
        return None
    low, high = wilson_interval(successes, n)
    return [low, high]


def per_key(results: Sequence[FieldResult]) -> dict[str, dict[str, Any]]:
    """Per key: counts and accuracy; the unit is the document (one field per document per
    repeat), so a Wilson interval is given when two or more documents carry the key."""
    grouped: dict[str, list[FieldResult]] = defaultdict(list)
    for r in results:
        grouped[r.key].append(r)
    table: dict[str, dict[str, Any]] = {}
    for key in sorted(grouped):
        rows = grouped[key]
        stats = block(rows)
        stats["tier"] = rows[0].tier
        stats["type"] = rows[0].type
        stats["critical_flag"] = rows[0].critical_flag
        stats["accuracy_interval"] = _wilson(stats["correct"], stats["present"], stats["documents"])
        table[key] = stats
    return table


def _breakdown(results: Sequence[FieldResult], attribute: str) -> dict[str, Any]:
    grouped: dict[str, list[FieldResult]] = defaultdict(list)
    for r in results:
        value = getattr(r, attribute)
        name = f"{r.set}/{r.document}" if attribute == "document" else str(value).lower()
        grouped[name].append(r)
    return {name: block(grouped[name]) for name in sorted(grouped)}


def page_metrics(results: Sequence[FieldResult]) -> dict[str, Any]:
    """Page strict/lenient over correct predictions whose truth has a page and which cite
    one; grounding over predictions that cite a page with a text layer."""
    cited = [r for r in results if r.page_status in ("strict", "lenient", "wrong")]
    strict = sum(r.page_status == "strict" for r in cited)
    lenient = sum(r.page_status in ("strict", "lenient") for r in cited)
    no_page = sum(r.page_status == "no_page" for r in results)
    grounded = [r for r in results if r.grounded is not None]
    return {
        "page_cited": len(cited),
        "page_not_cited": no_page,
        "page_strict": strict,
        "page_lenient": lenient,
        "page_strict_rate": _rate(strict, len(cited)),
        "page_strict_se": _clustered(cited, lambda r: r.page_status == "strict"),
        "page_lenient_rate": _rate(lenient, len(cited)),
        "page_lenient_se": _clustered(cited, lambda r: r.page_status in ("strict", "lenient")),
        "grounding_checked": len(grounded),
        "grounding_unchecked": sum(
            r.predicted and r.grounded is None for r in results
        ),  # no page cited or no text layer
        "grounded": sum(bool(r.grounded) for r in grounded),
        "grounded_rate": _rate(sum(bool(r.grounded) for r in grounded), len(grounded)),
        "grounded_se": _clustered(grounded, lambda r: bool(r.grounded)),
    }


def display_metrics(results: Sequence[FieldResult]) -> dict[str, Any]:
    checked = [r for r in results if r.display_ok is not None]
    ok = sum(bool(r.display_ok) for r in checked)
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in checked:
        by_type[r.type][0] += bool(r.display_ok)
        by_type[r.type][1] += 1
    return {
        "checked": len(checked),
        "matches": ok,
        "rate": _rate(ok, len(checked)),
        "rate_se": _clustered(checked, lambda r: bool(r.display_ok)),
        "by_type": {t: {"matches": m, "checked": n} for t, (m, n) in sorted(by_type.items())},
    }


def _highlight_block(results: Sequence[FieldResult]) -> dict[str, Any]:
    checked = [r for r in results if r.highlight is not None]
    counts = {status: sum(r.highlight == status for r in checked) for status in HIGHLIGHT_STATUSES}
    return {
        "checked": len(checked),
        **counts,
        "rate": _rate(counts["correct"], len(checked)),
        "rate_se": _clustered(checked, lambda r: r.highlight == "correct"),
    }


def highlight_metrics(results: Sequence[FieldResult]) -> dict[str, Any]:
    """Highlight correct over the correct predictions it can check (module doc); ``n/a``
    when no prediction carried regions."""
    checked = [r for r in results if r.highlight is not None]
    by: dict[str, Any] = {}
    for attribute in HIGHLIGHT_BREAKDOWNS:
        grouped: dict[str, list[FieldResult]] = defaultdict(list)
        for r in checked:
            grouped[str(getattr(r, attribute)).lower()].append(r)
        by[attribute] = {name: _highlight_block(grouped[name]) for name in sorted(grouped)}
    return {"available": bool(checked), **_highlight_block(results), "by": by}


def _percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (``q`` in 0..100); NaN for no values."""
    if not values:
        return math.nan
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return float(ordered[rank - 1])


def usage_metrics(
    documents: Sequence[Mapping[str, Any]], prices: Mapping[str, float] | None = None
) -> dict[str, Any]:
    """Cost, tokens and latency per scored document (first repeat; module doc)."""
    prices = AZURE_USD_PER_1000_PAGES if prices is None else prices
    rows = []
    pages: dict[str, int] = defaultdict(int)
    unpriced: set[str] = set()
    for document in documents:
        ok = _ok_repeats(document)
        if not ok or ok[0].get("repeat", 1) != 1:
            continue
        first = ok[0]
        azure_usd = 0.0
        for model, count in (document.get("azure_pages") or {}).items():
            pages[model] += int(count)
            if model in prices:
                azure_usd += prices[model] * int(count) / 1000
            else:
                unpriced.add(model)
        rows.append(
            {
                "model_usd": float(first.get("cost_usd") or 0.0),
                "azure_usd": azure_usd,
                "tokens_input": int(first.get("tokens_input") or 0),
                "tokens_output": int(first.get("tokens_output") or 0),
                "calls": int(first.get("llm_calls") or 0),
                "seconds": float(first.get("seconds") or 0.0),
                "model_seconds": float(first.get("model_latency_ms") or 0) / 1000,
            }
        )
    n = len(rows)

    def total(name: str) -> float:
        return float(sum(r[name] for r in rows))

    def per(name: str) -> float:
        return total(name) / n if n else math.nan

    return {
        "documents": n,
        "model_usd": total("model_usd"),
        "model_usd_per_document": per("model_usd"),
        "azure_pages": dict(sorted(pages.items())),
        "azure_usd": total("azure_usd"),
        "azure_usd_per_document": per("azure_usd"),
        "azure_unpriced_models": sorted(unpriced),
        "usd_per_document": per("model_usd") + per("azure_usd") if n else math.nan,
        "tokens_input": int(total("tokens_input")),
        "tokens_output": int(total("tokens_output")),
        "tokens_input_per_document": per("tokens_input"),
        "tokens_output_per_document": per("tokens_output"),
        "llm_calls": int(total("calls")),
        "seconds_p50": _percentile([r["seconds"] for r in rows], 50),
        "seconds_p95": _percentile([r["seconds"] for r in rows], 95),
        "model_seconds_p50": _percentile([r["model_seconds"] for r in rows], 50),
        "model_seconds_p95": _percentile([r["model_seconds"] for r in rows], 95),
    }


def text_anls(results: Sequence[FieldResult]) -> dict[str, Any]:
    scored = [r.anls for r in results if r.anls is not None]
    return {"fields": len(scored), "mean_anls": mean(scored) if scored else math.nan}


def gate_at(results: Sequence[FieldResult], threshold: float) -> dict[str, Any]:
    errors = [r for r in results if r.error]
    predicted = [r for r in results if r.predicted]
    caught = sum(r.flagged(threshold) for r in errors)
    flagged = sum(r.flagged(threshold) for r in predicted)
    return {
        "errors": len(errors),
        "errors_flagged": caught,
        "error_recall": _rate(caught, len(errors)),
        "error_recall_se": _clustered(errors, lambda r: r.flagged(threshold)),
        "predicted": len(predicted),
        "flagged": flagged,
        "review_load": _rate(flagged, len(predicted)),
        "review_load_se": _clustered(predicted, lambda r: r.flagged(threshold)),
    }


def risk_coverage(results: Sequence[FieldResult]) -> list[dict[str, float]]:
    """For every threshold (each distinct confidence, plus 0 and above 1): the share of
    predicted values auto-accepted (not flagged) and the error rate among them."""
    predicted = [r for r in results if r.predicted]
    if not predicted:
        return []
    thresholds = sorted(
        {0.0, 1.000001} | {r.confidence for r in predicted if r.confidence is not None}
    )
    points = []
    for t in thresholds:
        accepted = [r for r in predicted if not r.flagged(t)]
        bad = sum(r.outcome in ("wrong", "spurious") for r in accepted)
        points.append(
            {
                "threshold": t,
                "coverage": len(accepted) / len(predicted),
                "risk": _rate(bad, len(accepted)),
                "accepted": len(accepted),
            }
        )
    return points


def document_gate(
    results: Sequence[FieldResult], needs_review: Mapping[tuple[str, str], bool]
) -> dict[str, Any]:
    """Did ``needs_review`` fire on every document with a critical-tier error?"""
    with_error = sorted({(r.set, r.document) for r in results if r.tier == "critical" and r.error})
    fired = [d for d in with_error if needs_review.get(d, False)]
    documents = {(r.set, r.document) for r in results}
    return {
        "documents": len(documents),
        "documents_needs_review": sum(needs_review.get(d, False) for d in documents),
        "documents_with_critical_error": len(with_error),
        "needs_review_on_critical_error": len(fired),
        "missed_documents": [f"{s}/{d}" for s, d in with_error if (s, d) not in fired],
        "rate": _rate(len(fired), len(with_error)),
        "interval": _wilson(len(fired), len(with_error), len(with_error)),
    }


def _issue_hits(expected: Mapping[str, Any], raised: Mapping[str, Any]) -> bool:
    if expected["rule"] != raised.get("rule"):
        return False
    wanted = set(expected.get("keys") or ())
    return not wanted or bool(wanted & set(raised.get("keys") or ()))


def issue_metrics(
    documents: Sequence[tuple[str, DocumentTruth, Sequence[Mapping[str, Any]]]],
) -> dict[str, Any]:
    """expected_issues recall and false alarms (raised issues that match no expected issue
    of the document), per rule. The scorer matches on ``rule`` and overlapping ``keys``."""
    per_rule: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "found": 0, "raised": 0, "false_alarms": 0}
    )
    expected_total = found_total = raised_total = false_total = 0
    for _, doc, raised in documents:
        expected = [issue.model_dump() for issue in doc.expected_issues]
        for issue in expected:
            hit = any(_issue_hits(issue, r) for r in raised)
            per_rule[issue["rule"]]["expected"] += 1
            per_rule[issue["rule"]]["found"] += hit
            expected_total += 1
            found_total += hit
        for r in raised:
            rule = str(r.get("rule"))
            alarm = not any(_issue_hits(issue, r) for issue in expected)
            per_rule[rule]["raised"] += 1
            per_rule[rule]["false_alarms"] += alarm
            raised_total += 1
            false_total += alarm
    return {
        "expected": expected_total,
        "found": found_total,
        "recall": _rate(found_total, expected_total),
        "raised": raised_total,
        "false_alarms": false_total,
        "by_rule": {rule: per_rule[rule] for rule in sorted(per_rule)},
    }


def repeat_metrics(results: Sequence[FieldResult], repeats: int) -> dict[str, Any]:
    """Accuracy per repeat and pass^k per field across repeats (fields scored in every
    repeat; success = correct or correct_absent)."""
    by_repeat: dict[int, list[FieldResult]] = defaultdict(list)
    runs: dict[tuple[str, str, str], list[bool]] = defaultdict(list)
    for r in results:
        by_repeat[r.repeat].append(r)
        runs[(r.set, r.document, r.key)].append(r.outcome in SUCCESS)
    complete = [v for v in runs.values() if len(v) == repeats]
    tiers: dict[str, list[list[bool]]] = defaultdict(list)
    tier_of = {(r.set, r.document, r.key): r.tier for r in results}
    for field_id, v in runs.items():
        if len(v) == repeats:
            tiers[tier_of[field_id]].append(v)
    return {
        "repeats": repeats,
        "accuracy_by_repeat": {
            str(n): block(rows)["accuracy"] for n, rows in sorted(by_repeat.items())
        },
        "fields": len(complete),
        "pass_hat_k": {
            str(k): pass_hat_k(complete, k) if complete else math.nan for k in range(1, repeats + 1)
        },
        "pass_hat_k_by_tier": {
            tier: {str(k): pass_hat_k(rows, k) for k in range(1, repeats + 1)}
            for tier, rows in sorted(tiers.items())
        },
    }


# --------------------------------------------------------------------------- the run


def _ok_repeats(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [r for r in document.get("repeats", ()) if r.get("status") == "ok"]


def score_run(
    truths: Mapping[str, TruthSet],
    predictions: Mapping[str, Any],
    *,
    page_texts: PageTexts | None = None,
    page_words: PageWordsFor | None = None,
) -> dict[str, Any]:
    """Score ``predictions`` (the ``predictions.json`` shape) against ``truths``."""
    by_doc = {(d["set"], d["document"]): d for d in predictions.get("documents", ())}
    results: list[FieldResult] = []
    failed: list[dict[str, str]] = []
    unscorable = 0
    unscored_predictions = 0
    needs_review: dict[tuple[str, str], bool] = {}
    issue_docs: list[tuple[str, DocumentTruth, Sequence[Mapping[str, Any]]]] = []
    repeats = 1
    for set_name, truth in truths.items():
        for doc in truth.documents:
            prediction = by_doc.get((set_name, doc.document))
            if prediction is None:
                continue
            ok = _ok_repeats(prediction)
            if not ok or ok[0].get("repeat", 1) != 1:
                failed.append(
                    {
                        "set": set_name,
                        "document": doc.document,
                        "status": str(prediction.get("status", "error")),
                    }
                )
                continue
            pages = page_texts(doc) if page_texts is not None else None
            words = page_words(doc) if page_words is not None else None
            repeats = max(repeats, len(prediction.get("repeats", ())))
            first = ok[0]
            needs_review[(set_name, doc.document)] = bool(first.get("needs_review"))
            issue_docs.append((set_name, doc, list(first.get("issues", ()))))
            truth_keys = {f.key for f in doc.fields}
            unscored_predictions += sum(k not in truth_keys for k in first.get("fields", {}))
            for rep in ok:
                fields: Mapping[str, Any] = rep.get("fields", {})
                named = named_keys(rep.get("issues", ()), rep.get("review_reasons", ()))
                for field_truth in doc.fields:
                    if field_truth.status == "unscorable":
                        unscorable += rep is first
                        continue
                    results.append(
                        score_field(
                            set_name,
                            doc,
                            field_truth,
                            fields.get(field_truth.key),
                            repeat=int(rep.get("repeat", 1)),
                            named=named,
                            pages=pages,
                            words=words,
                        )
                    )
    first_repeat = [r for r in results if r.repeat == 1]
    headline = [r for r in first_repeat if r.verified and not r.extra]
    return {
        "schema": SCHEMA,
        "run": predictions.get("run"),
        "documents": {
            "scored": len(needs_review),
            "failed": failed,
            "unscorable_fields": unscorable,
            "unscored_predictions": unscored_predictions,
        },
        "overall": block(headline),
        "all_fields": block(first_repeat),
        "unverified": block([r for r in first_repeat if not r.verified and not r.extra]),
        "extra": block([r for r in first_repeat if r.extra]),
        "by": {name: _breakdown(headline, name) for name in BREAKDOWNS},
        "keys": per_key(headline),
        "page": page_metrics(headline),
        "highlight": highlight_metrics(headline),
        "usage": usage_metrics(predictions.get("documents", ())),
        "display": display_metrics(headline),
        "text_anls": text_anls(headline),
        "review_gate": {
            "thresholds": {str(t): gate_at(headline, t) for t in GATE_THRESHOLDS},
            "risk_coverage": risk_coverage(headline),
            "documents": document_gate(headline, needs_review),
        },
        "issues": issue_metrics(issue_docs),
        "repeats": repeat_metrics([r for r in results if r.verified and not r.extra], repeats),
        "fields": [_field_row(r) for r in results],
    }


def _field_row(r: FieldResult) -> dict[str, Any]:
    """One field's outcome for score.json: keys and outcomes, no values."""
    row = asdict(r)
    row.pop("section")
    return row


# --------------------------------------------------------------------------- report


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return "-" if math.isnan(value) else f"{value:.3f}"
    return str(value)


def _interval(value: Any) -> str:
    return f"[{value[0]:.3f}, {value[1]:.3f}]" if value else "-"


_BLOCK_COLUMNS: Final = (
    "documents",
    "correct",
    "wrong",
    "missed",
    "spurious",
    "correct_absent",
    "accuracy",
    "accuracy_se",
    "wrong_value_rate",
    "coverage",
    "spurious_rate",
)


def _block_table(rows: Mapping[str, Mapping[str, Any]], first: str) -> list[str]:
    lines = [
        f"| {first} | " + " | ".join(_BLOCK_COLUMNS) + " |",
        "|---|" + "---|" * len(_BLOCK_COLUMNS),
    ]
    for name, stats in rows.items():
        lines.append(f"| {name} | " + " | ".join(_fmt(stats[c]) for c in _BLOCK_COLUMNS) + " |")
    return lines


def render_report(score: Mapping[str, Any], run: Mapping[str, Any] | None = None) -> str:
    """``report.md``: keys, outcomes and counts only (SPEC §8)."""
    out: list[str] = ["# Appraisal extraction evaluation", ""]
    if run:
        for name in ("run", "mode", "sets", "git_sha", "repeats", "systems", "models"):
            if name in run:
                out.append(f"- {name}: `{run[name]}`")
        system = run.get("system")
        if isinstance(system, Mapping):
            out.append(f"- system: `{system.get('label', system.get('name'))}`")
        out.append("")
    docs = score["documents"]
    out += [
        "## Documents",
        "",
        f"- scored: {docs['scored']}",
        f"- failed: {len(docs['failed'])}",
        f"- unscorable fields (excluded): {docs['unscorable_fields']}",
        "",
    ]
    for item in docs["failed"]:
        out.append(f"  - {item['set']}/{item['document']}: {item['status']}")
    if docs["failed"]:
        out.append("")
    out += ["## Overall", ""]
    out += _block_table(
        {
            "verified catalogue (headline)": score["overall"],
            "unverified": score["unverified"],
            "extra keys": score["extra"],
            "all scorable": score["all_fields"],
        },
        "fields",
    )
    out.append("")
    for name in BREAKDOWNS:
        out += [f"## By {name.replace('_', ' ')}", ""]
        out += _block_table(score["by"][name], name)
        out.append("")
    page = score["page"]
    display = score["display"]
    out += [
        "## Pages, grounding, display",
        "",
        "| metric | n | rate |",
        "|---|---|---|",
        f"| page strict | {page['page_cited']} | {_fmt(page['page_strict_rate'])} |",
        f"| page lenient | {page['page_cited']} | {_fmt(page['page_lenient_rate'])} |",
        f"| correct without a page | {page['page_not_cited']} | - |",
        f"| grounded (value_text on the cited page) | {page['grounding_checked']} "
        f"| {_fmt(page['grounded_rate'])} |",
        f"| display = canonical (correct values) | {display['checked']} "
        f"| {_fmt(display['rate'])} |",
        f"| text ANLS (mean) | {score['text_anls']['fields']} "
        f"| {_fmt(score['text_anls']['mean_anls'])} |",
    ]
    highlight = score.get("highlight")
    if highlight is not None:
        rate = _fmt(highlight["rate"]) if highlight["available"] else "n/a (no regions)"
        out.append(f"| highlight correct (correct values) | {highlight['checked']} | {rate} |")
    out.append("")
    if highlight is not None and highlight["available"]:
        out += [
            "## Highlight",
            "",
            "| breakdown | name | checked | correct | wrong page | wrong box | no region | rate |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for attribute, groups in highlight["by"].items():
            for name, h in groups.items():
                out.append(
                    f"| {attribute} | {name} | {h['checked']} | {h['correct']} "
                    f"| {h['wrong_page']} | {h['wrong_box']} | {h['no_region']} "
                    f"| {_fmt(h['rate'])} |"
                )
        out.append("")
    usage = score.get("usage")
    if usage is not None:
        out += [
            "## Cost and latency (per scored document, first repeat)",
            "",
            "| documents | model $ / doc | Azure DI $ / doc (est.) | total $ / doc "
            "| input tokens / doc | output tokens / doc | calls | wall s p50 | wall s p95 "
            "| model s p50 | model s p95 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
            f"| {usage['documents']} | {_fmt(usage['model_usd_per_document'])} "
            f"| {_fmt(usage['azure_usd_per_document'])} | {_fmt(usage['usd_per_document'])} "
            f"| {_fmt(usage['tokens_input_per_document'])} "
            f"| {_fmt(usage['tokens_output_per_document'])} | {usage['llm_calls']} "
            f"| {_fmt(usage['seconds_p50'])} | {_fmt(usage['seconds_p95'])} "
            f"| {_fmt(usage['model_seconds_p50'])} | {_fmt(usage['model_seconds_p95'])} |",
            "",
            f"Azure DI pages by model: `{usage['azure_pages']}`"
            + (
                f"; unpriced: `{usage['azure_unpriced_models']}`"
                if usage["azure_unpriced_models"]
                else ""
            )
            + ". A replay reports the recorded calls' tokens and dollars; its wall time is "
            "not a latency (the model seconds are the recorded calls' own, summed).",
            "",
        ]
    gate = score["review_gate"]
    out += [
        "## Review gate",
        "",
        "| threshold | errors | flagged errors | error recall | predicted | flagged "
        "| review load |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, g in gate["thresholds"].items():
        out.append(
            f"| {t} | {g['errors']} | {g['errors_flagged']} | {_fmt(g['error_recall'])} "
            f"| {g['predicted']} | {g['flagged']} | {_fmt(g['review_load'])} |"
        )
    d = gate["documents"]
    out += [
        "",
        f"needs_review fired on {d['needs_review_on_critical_error']} of "
        f"{d['documents_with_critical_error']} documents with a critical-tier error "
        f"(interval {_interval(d['interval'])}); {d['documents_needs_review']} of "
        f"{d['documents']} documents need review.",
        "",
        "Risk-coverage (threshold, coverage, risk):",
        "",
    ]
    curve = gate["risk_coverage"]
    step = max(1, len(curve) // 12)
    for point in curve[::step]:
        out.append(
            f"- {point['threshold']:.3f}: coverage {point['coverage']:.3f}, "
            f"risk {_fmt(point['risk'])}"
        )
    issues = score["issues"]
    out += [
        "",
        "## Issues",
        "",
        f"expected {issues['expected']}, found {issues['found']} "
        f"(recall {_fmt(issues['recall'])}); raised {issues['raised']}, "
        f"false alarms {issues['false_alarms']}.",
        "",
        "| rule | expected | found | raised | false alarms |",
        "|---|---|---|---|---|",
    ]
    for rule, c in issues["by_rule"].items():
        out.append(
            f"| {rule} | {c['expected']} | {c['found']} | {c['raised']} | {c['false_alarms']} |"
        )
    rep = score["repeats"]
    out += ["", "## Repeats", "", f"repeats: {rep['repeats']}, fields: {rep['fields']}", ""]
    for k, value in rep["pass_hat_k"].items():
        out.append(f"- pass^{k}: {_fmt(value)}")
    out += [
        "",
        "## Per key",
        "",
        "| key | tier | critical flag | docs | correct | wrong | missed | spurious "
        "| correct_absent | accuracy | Wilson 95% |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, s in score["keys"].items():
        out.append(
            f"| {key} | {s['tier']} | {'yes' if s['critical_flag'] else ''} | {s['documents']} "
            f"| {s['correct']} | {s['wrong']} | {s['missed']} | {s['spurious']} "
            f"| {s['correct_absent']} | {_fmt(s['accuracy'])} "
            f"| {_interval(s['accuracy_interval'])} |"
        )
    out.append("")
    return "\n".join(out)
