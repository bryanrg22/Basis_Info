"""Which way of placing a field's highlight box is most accurate: Basis's rule or Azure's own
field boxes (the open comparison of EVAL_REPORT section 33.6).

The review page draws, for every extracted field, a box over where its value is printed
(``docs/api/FIELD_REGIONS.md``). Today the box comes from :mod:`basis.extraction.regions`:
the checked box for a checkbox field, else the Azure DI key-value pair's value box when that
pair read the value, else the value's words on the cited page nearest the field's caption
(and, for the grounded reading, else the quote's words). Section 33.6 measured that rule
alone (99.3 % of correctly read values boxed on the value) and compared it with nothing.
This module scores it side by side with the alternatives, on the same fields, with the same
test, at no cost (the recorded Azure DI analyses and the recorded extraction runs; no model
and no Azure call):

``today``
    Basis's rule (:func:`basis.extraction.regions.field_regions`; for a grounded reading,
    else :func:`basis.extraction.grounded.quote_region`) as the input run stored it
    (``--today stored``, the default: the production code wrote those boxes), or
    recomputed at the current commit from the value, page, source and quote the run stored,
    with Azure's pair value and box for an Azure-read value (``--today recomputed``, for a
    run whose stored boxes are not the production rule's). A prediction file does not keep
    the reader's raw text for a model-read value (the pipeline searches the raw text before
    the cleaned value), so the recomputation can miss a box production found; both are
    scored (``regression``) and ``agreement`` counts the fields where they differ. The
    time is the recomputation's either way.
``azure_pair``
    Azure DI's own key-value pair for the field: the value box of the pair whose caption maps
    to the field's key, by the pipeline's own caption mapping
    (:func:`basis.extraction.sections.azure_candidates`, or
    :func:`basis.extraction.uad36_pdf.azure_candidates` on a UAD 3.6 form, detected as the
    pipeline does). No box when Azure paired nothing with that caption, or paired a key with
    no value (Azure returns no region for an empty value).
``text_search``
    The quote's words (the grounded reading's verbatim quote; the value itself when the
    reader gave no quote) by plain text search on the cited page, the first occurrence in
    reading order (:func:`basis.extraction.geometry.locate`): no caption, no checkbox, no
    whole-word preference.
``azure_else_today`` / ``today_else_azure``
    The two fallbacks: Azure's box where it exists, else Basis's rule; and the reverse.
``quote_value``
    Basis's rule checked against the reader's quote (the grounded reading quotes the words
    it read each value from: the quote says which printing of the value it read): Basis's
    box when it lies inside the first occurrence of the quote's words on the cited page;
    else the value's words (whole words first) inside the quote; else Basis's box. A
    checkbox field, a value without a quote, or a quote not found keeps Basis's box. On the
    pipeline reading (no quotes) it is Basis's rule.
``azure_1004`` / ``azure_1004_narrowed``
    Azure's own field boxes from its form model for the legacy 1004
    (``prebuilt-mortgage.us.1004``, recorded for EVAL_REPORT 33.5; production does not run
    it): the ``boundingRegions`` of the document field mapped to the key
    (:data:`evals.appraisal.systems.mortgage.FIELD_MAP`) when the field has a value, as Azure
    returns it; and narrowed, as the 33.5 ``azure_1004`` system draws it (a checkbox group
    or an address part narrowed to the value's words inside the field's box). About 50 keys,
    legacy forms only (the model reads no UAD 3.6 form).
``checked_pair``
    The combination the vendors' own definition suggests (a value's location is the box of
    the words it was read from): Azure's pair box when Azure's own words inside that box
    hold the extracted value (the pair read this value), else Basis's rule. The check reads
    Azure's words, never the scorer's text layer; a pair on another page than the field's
    cited page is not taken (the API draws a field's boxes on its own page only).
``checked_pair_words``
    The same, but the box drawn is the value's own words inside Azure's pair box (Textract's
    VALUE block is the union of its child WORD blocks; Azure's value polygon can be wider
    than the words, a "$" or a blank cell with them).

**What "the box is right" means.** The document-AI products report a field's location as
the box of the words the field's value was read from (Azure DI ``boundingRegions`` on each
key-value element, Google Document AI ``formFields[].fieldValue.boundingPoly``, Amazon
Textract ``KEY_VALUE_SET`` VALUE blocks over their child ``WORD`` blocks); the literature
scores a predicted location by IoU with a ground-truth box when one exists, or by whether
the box holds the answer's words. The truth files here carry a page and the printed value,
no box, so the test is the words-in-the-box one, the same for every method:

* **right** (``correct``): the first box is on the truth page (or an ``also_on`` page) and
  the PDF text-layer words whose centres fall inside it contain the printed value
  (:func:`evals.appraisal.score.highlight_status`, the 33.6 test, unchanged);
* **exactly the value** (``strict``): right, and the words inside the box are the printed
  value and nothing else (a box over a whole line holding the value is right but not exact);
* ``wrong_page``, ``wrong_box``, ``no_region`` otherwise; a page with no text layer (a scan)
  is not checkable and is left out, as in 33.6.

The population is the same for every method: the input run's correctly read headline fields
(verified catalogue keys, first repeat: the scorer's own highlight population) whose truth
has a page with a text layer and a printed value to look for. Coverage counts the fields a
method places a box on at all; Azure's pair coverage is also reported over every printed
headline field, read right or not.

Usage::

    python -m evals.highlight_placement --run <appraisal run directory> [--all | --documents a,b]

``--run`` names a directory written by ``evals.appraisal.run`` (``predictions.json``,
``run.json``). By default the 16-document subset of section 33.5 is scored; ``--all``
scores every document the run holds (the 33.6 regression). The run directory
(``--out``, default ``appraisal/runs/<stamp>-highlight_placement-<scope>``) holds one case per
(document, field, method) in ``results.json``, the comparison table in ``comparison.md``,
every number in ``run.json`` and the manifest (:mod:`evals.record`, component
``highlight_placement``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Literal, cast

from basis.extraction import sections, uad36_pdf
from basis.extraction.azure_di import AzureDiResult, from_sdk
from basis.extraction.field_mappings import CHECKBOX_KEYS
from basis.extraction.forms import detect_form
from basis.extraction.geometry import (
    MARK_TOKEN,
    Box,
    Layout,
    has_geometry,
    layout_of,
    locate,
    region,
    union,
)
from basis.extraction.grounded import quote_region
from basis.extraction.regions import field_regions, region_json
from basis.extraction.results import ExtractedField, Region, Source
from evals import record
from evals.appraisal.recordings import AZURE_1004_RAW, AZURE_RAW, rebuild_sdk_result, recording_dir
from evals.appraisal.score import (
    _MARKS,
    AZURE_USD_PER_1000_PAGES,
    PageWords,
    fold_highlight,
    highlight_status,
    highlight_targets,
    number_target,
    score_run,
    words_inside,
)
from evals.appraisal.systems import mortgage
from evals.appraisal.systems.base import MORTGAGE_1004_MODEL_ID
from evals.appraisal.systems.layout import from_raw
from evals.appraisal.truth import (
    SETS,
    DocumentTruth,
    FieldTruth,
    TruthSet,
    appraisal_dir,
)

COMPONENT: Final = "highlight_placement"
SUMMARY_SCHEMA: Final = 1

# The 16-document subset of EVAL_REPORT 33.5 (both clients, 6 GSE UAD 3.6 samples, 8
# generated 1004s of which 2 are scans), as ``set/document``.
SUBSET_16: Final[tuple[str, ...]] = (
    "client/client_a",
    "client_uad36/client_a_uad36",
    "gse_d1/Condo1",
    "gse_d1/Coop1",
    "gse_d1/MH1",
    "gse_d1/SF1",
    "gse_d1/SF3",
    "gse_d1/TwoToFour1",
    "synthetic_1004/syn1004_001",
    "synthetic_1004/syn1004_004",
    "synthetic_1004/syn1004_008",
    "synthetic_1004/syn1004_012",
    "synthetic_1004/syn1004_016",
    "synthetic_1004/syn1004_020",
    "synthetic_1004/syn1004_scan_01",
    "synthetic_1004/syn1004_scan_03",
)

TODAY: Final = "today"
AZURE_PAIR: Final = "azure_pair"
TEXT_SEARCH: Final = "text_search"
AZURE_ELSE_TODAY: Final = "azure_else_today"
TODAY_ELSE_AZURE: Final = "today_else_azure"
CHECKED_PAIR: Final = "checked_pair"
CHECKED_PAIR_WORDS: Final = "checked_pair_words"
QUOTE_VALUE: Final = "quote_value"
AZURE_1004: Final = "azure_1004"
AZURE_1004_NARROWED: Final = "azure_1004_narrowed"
METHODS: Final[tuple[tuple[str, str], ...]] = (
    (
        TODAY,
        "Basis's rule · The checked box; else Azure's pair box when that pair read the value; "
        "else the value's words nearest the caption; else the quote's words (production)",
    ),
    (AZURE_PAIR, "Azure's pair box · Azure's own key-value pair for the field's caption"),
    (
        TEXT_SEARCH,
        "Plain text search · The quote's (else the value's) words on the cited page, first "
        "occurrence",
    ),
    (AZURE_ELSE_TODAY, "Azure first · Azure's pair box where it exists, else Basis's rule"),
    (TODAY_ELSE_AZURE, "Basis first · Basis's rule where it places a box, else Azure's pair box"),
    (
        CHECKED_PAIR,
        "Checked pair · Azure's pair box when Azure's words in it hold the value, else "
        "Basis's rule",
    ),
    (
        CHECKED_PAIR_WORDS,
        "Checked pair, value's words · The same, the box drawn over the value's words inside "
        "Azure's pair box",
    ),
    (
        QUOTE_VALUE,
        "Quote-checked · Basis's box when it lies in the reader's quote, else the value's "
        "words inside the quote",
    ),
    (
        AZURE_1004,
        "Azure's 1004 model box · The field's own box from Azure's prebuilt 1004 form model",
    ),
    (
        AZURE_1004_NARROWED,
        "Azure's 1004 model box, narrowed · The same, a checkbox group or address narrowed to "
        "the value's words in it",
    ),
)
STATUSES: Final = ("correct", "wrong_box", "wrong_page", "no_region")
_NUMBER_EDGES: Final = "$=()+:,%"
_SOURCES: Final = frozenset({"azure", "regex", "table", "llm", "manual"})

Reader = Literal["pipeline", "grounded"]
TodayFrom = Literal["stored", "recomputed"]
RegionsJson = list[dict[str, Any]]
LayoutFor = Callable[[DocumentTruth], AzureDiResult | None]
FormFor = Callable[[DocumentTruth], Mapping[str, Any] | None]
WordsFor = Callable[[DocumentTruth], Sequence[PageWords] | None]
TextsFor = Callable[[DocumentTruth], Sequence[str] | None]


# --------------------------------------------------------------------------- the inputs


@dataclass(frozen=True)
class AzurePair:
    """Azure DI's key-value pair mapped to a catalogue key: the pair's value text and box."""

    raw_text: str
    regions: RegionsJson


@dataclass(frozen=True)
class Placement:
    """One correctly read field as the extraction stored it, with what the methods read."""

    key: str
    value_text: str | None
    value_num: str | None
    value_date: str | None
    source: str
    page: int | None
    quote: str | None = None
    stored: RegionsJson = field(default_factory=list)  # the boxes the input run stored
    pair: AzurePair | None = None  # Azure's pair for the key (None: Azure paired nothing)
    form_1004: RegionsJson = field(default_factory=list)  # Azure's 1004 model field box
    form_1004_narrowed: RegionsJson = field(default_factory=list)

    @property
    def pair_regions(self) -> RegionsJson:
        return list(self.pair.regions) if self.pair is not None else []


@dataclass
class Row:
    """One (document, field) of the population, scored by every method."""

    set: str
    document: str
    key: str
    source: str
    tier: str
    kind: str = "value"  # checkbox (a checked option's label) or value (printed text)
    by_method: dict[str, dict[str, Any]] = field(default_factory=dict)
    stored: dict[str, Any] = field(default_factory=dict)  # the run's stored boxes, scored
    recomputed: dict[str, Any] = field(default_factory=dict)  # Basis's rule recomputed


def azure_pairs(result: AzureDiResult, *, uad36: bool) -> dict[str, AzurePair]:
    """Azure's pair per catalogue key, by the pipeline's own caption mapping (the pair the
    pipeline would take for the key); only pairs with a value box."""
    if uad36:
        candidates = uad36_pdf.azure_candidates(result)
    else:
        candidates, _ = sections.azure_candidates(result)
    return {
        c.key: AzurePair(c.raw_text, [region(r.page, r.bbox) for r in c.regions])
        for c in candidates
        if c.source == "azure" and c.regions
    }


def azure_1004_regions(raw: Mapping[str, Any]) -> dict[str, tuple[RegionsJson, RegionsJson]]:
    """Per catalogue key, the 1004 model field's own box and the narrowed box (module doc);
    only fields with a value and a box."""
    fields = mortgage.document_fields(raw)
    if not fields:
        return {}
    geometry = from_raw(raw)
    out: dict[str, tuple[RegionsJson, RegionsJson]] = {}
    for path, key, how in mortgage.FIELD_MAP:
        node = mortgage.field_at(fields, path)
        if node is None or key in out:
            continue
        value = mortgage.read_value(node, fields, how)
        found = mortgage._bounding(node, geometry) if value else None
        if value is None or found is None:
            continue
        out[key] = ([region(*found)], mortgage.field_region(node, value, how, geometry))
    return out


def is_uad36(result: AzureDiResult, texts: Sequence[str] | None) -> bool:
    """The form by its page footers, as the pipeline detects it (PDF text and Azure's)."""
    return detect_form(texts or (), result.page_texts()).is_uad36


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _date(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def recompute_today(item: Placement, layout: Layout, reader: Reader) -> RegionsJson:
    """Basis's rule at the current commit (module doc): what the pipeline (or the grounded
    reading) gives this value on this layout."""
    source = cast(Source, item.source if item.source in _SOURCES else "llm")
    azure_read = source == "azure" and item.pair is not None
    extracted = ExtractedField(
        key=item.key,
        raw_text=item.pair.raw_text if azure_read and item.pair is not None else None,
        value_text=item.value_text,
        value_num=_decimal(item.value_num),
        value_date=_date(item.value_date),
        confidence=1.0,
        source=source,
        page=item.page,
        regions=tuple(Region(page=int(r["page"]), bbox=tuple(r["bbox"])) for r in item.pair_regions)
        if azure_read
        else (),
    )
    boxes = region_json(field_regions(extracted, layout))
    if boxes or reader != "grounded" or not item.quote or item.page is None:
        return boxes
    return quote_region(layout, item.key, item.page, item.quote, item.value_text or "")


def pair_value_words(item: Placement, layout: Layout) -> RegionsJson:
    """The extracted value's words (Azure's) inside the pair's value box; empty when they
    do not hold the value, or when the pair is on another page than the field's cited page
    (the API's contract: a field's boxes are on its own page, ``FIELD_REGIONS.md``)."""
    regions = item.pair_regions
    if not regions or not item.value_text:
        return []
    first = regions[0]
    if item.page is not None and int(first["page"]) != item.page:
        return []
    page = layout.page(int(first["page"]))
    if page is None:
        return []
    within = cast(Box, tuple(float(v) for v in first["bbox"]))
    box = locate(page, item.value_text, within=within)
    return [region(page.number, box)] if box is not None else []


def quote_checked(item: Placement, layout: Layout, today: RegionsJson) -> RegionsJson:
    """Basis's boxes checked against the quote (module doc, ``quote_value``)."""
    page = layout.page(item.page)
    if page is None or item.key in CHECKBOX_KEYS or not item.quote or not item.value_text:
        return list(today)
    quote = locate(page, item.quote)
    if quote is None:
        return list(today)
    if today and today[0].get("page") == page.number and _centred(today[0]["bbox"], quote):
        return list(today)
    for whole_words in (True, False):  # "3" is not inside "370" (Basis's rule's order)
        box = locate(page, item.value_text, within=quote, whole_words=whole_words)
        if box is not None:
            return [region(page.number, box)]
    return list(today)


def pair_holds_value(item: Placement, layout: Layout) -> bool:
    """Azure's own words inside the pair's value box hold the extracted value."""
    return bool(pair_value_words(item, layout))


def place(method: str, item: Placement, layout: Layout, today: RegionsJson) -> RegionsJson:
    """The boxes ``method`` gives ``item`` (module doc); ``today`` is Basis's rule's."""
    if method == TODAY:
        return list(today)
    if method == AZURE_PAIR:
        return item.pair_regions
    if method == TEXT_SEARCH:
        page = layout.page(item.page)
        if page is None or item.page is None:
            return []
        box = locate(page, item.quote or item.value_text)
        return [region(item.page, box)] if box is not None else []
    if method == AZURE_ELSE_TODAY:
        return item.pair_regions or list(today)
    if method == TODAY_ELSE_AZURE:
        return list(today) or item.pair_regions
    if method == CHECKED_PAIR:
        return item.pair_regions if pair_holds_value(item, layout) else list(today)
    if method == CHECKED_PAIR_WORDS:
        return pair_value_words(item, layout) or list(today)
    if method == QUOTE_VALUE:
        return quote_checked(item, layout, today)
    if method == AZURE_1004:
        return list(item.form_1004)
    if method == AZURE_1004_NARROWED:
        return list(item.form_1004_narrowed)
    raise ValueError(f"unknown method {method!r}")


# --------------------------------------------------------------------------- the test


def checkable(truth: FieldTruth, words: Sequence[PageWords] | None) -> bool:
    """The truth gives a page with a text layer and a printed value to look for."""
    if truth.status != "present" or truth.page is None or words is None:
        return False
    if not 0 < truth.page <= len(words) or not words[truth.page - 1].words:
        return False
    return bool(highlight_targets(truth)) or number_target(truth) is not None


def exact(truth: FieldTruth, regions: RegionsJson, words: Sequence[PageWords]) -> bool:
    """The words inside the first box are the printed value and nothing else (a number may
    keep the "$" or "%" printed with it, which the form prints, not the value)."""
    if not regions:
        return False
    first = regions[0]
    page = first.get("page")
    if not isinstance(page, int) or not 0 < page <= len(words):
        return False
    inside = words_inside(words[page - 1], first.get("bbox") or ())
    if not inside:
        return False
    if "".join(inside) in highlight_targets(truth):
        return True
    number = number_target(truth)
    if number is None:
        return False
    kept = [w.strip(_NUMBER_EDGES) for w in inside]
    return "".join(w for w in kept if w) == number


def box_words(regions: RegionsJson, words: Sequence[PageWords]) -> int | None:
    """How many text-layer words the first box holds (``None``: no box, or no text layer)."""
    if not regions:
        return None
    first = regions[0]
    page = first.get("page")
    if not isinstance(page, int) or not 0 < page <= len(words) or not words[page - 1].words:
        return None
    return len(words_inside(words[page - 1], first.get("bbox") or ()))


def box_area(regions: RegionsJson) -> float | None:
    """The first box's area as a share of the page."""
    if not regions:
        return None
    x0, y0, x1, y1 = (float(v) for v in regions[0]["bbox"])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


# --------------------------------------------------------------------------- the box truth
#
# The words test cannot tell two occurrences of one value on a page apart (a city name
# printed again as the county or the neighborhood): a box on the wrong one holds the
# value's words too. The truth's ``quote`` (the printed context of the value, on every
# present field) gives a box truth: the quote's words located once on the truth page's text
# layer (``quote_box``), and the printed value's words inside it (``value_box``). Derived
# from the truth's text, never from any method's boxes; a quote found zero or several times
# gives no box truth.


@dataclass(frozen=True)
class BoxTruth:
    quote_box: Box | None  # the truth quote's words on the page, found exactly once
    value_box: Box | None  # the printed value's words inside it, found exactly once


def find_runs(page: PageWords, text: str, *, within: Box | None = None) -> list[Box]:
    """Every run of consecutive text-layer words whose folded characters contain the
    folded ``text`` (selection-mark tokens dropped), as page fractions; ``within``: only
    words centred in that box (slack 0.005). The words are in pdfplumber's own order (lines
    clustered with its y tolerance, then left to right): measured on the 41 appraisals'
    present fields, it finds the truth quote exactly once for 3,712 of 4,009, against 1,258
    for a plain sort by top then left (a caption and its value differ in top by a fraction
    of a point) and 3,605 for lines clustered by half a word's height."""
    target = fold_highlight(MARK_TOKEN.sub(" ", text))
    if not target or page.width <= 0 or page.height <= 0:
        return []
    ordered = page.words
    kept = [
        (fold_highlight(t), (lf / page.width, tp / page.height, rt / page.width, bt / page.height))
        for t, lf, tp, rt, bt in ordered
    ]
    kept = [(t, b) for t, b in kept if t and (within is None or _centred(b, within))]
    owner: list[int] = []
    for index, (folded, _) in enumerate(kept):
        owner.extend([index] * len(folded))
    haystack = "".join(t for t, _ in kept)
    found: list[Box] = []
    start = haystack.find(target)
    while start >= 0:
        first, last = owner[start], owner[start + len(target) - 1]
        found.append(union([b for _, b in kept[first : last + 1]]))
        start = haystack.find(target, start + 1)
    return list(dict.fromkeys(found))


def _centred(box: Sequence[float], within: Sequence[float], slack: float = 0.005) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (
        within[0] - slack <= cx <= within[2] + slack
        and within[1] - slack <= cy <= within[3] + slack
    )


def box_truth(truth: FieldTruth, words: Sequence[PageWords]) -> BoxTruth:
    """The quote's box and the value's box on the truth page (module comment above)."""
    if truth.page is None or not 0 < truth.page <= len(words) or not truth.quote:
        return BoxTruth(None, None)
    page = words[truth.page - 1]
    quotes = find_runs(page, truth.quote)
    if len(quotes) != 1:
        return BoxTruth(None, None)
    printed = truth.printed or ""
    if truth.key in CHECKBOX_KEYS:  # an "X" mark printed in a checkbox value (the scorer's)
        printed = _MARKS.sub(" ", printed)
    values = find_runs(page, printed, within=quotes[0]) if printed.strip() else []
    return BoxTruth(quotes[0], values[0] if len(values) == 1 else None)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two boxes."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    total = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / total if total > 0 else 0.0


def score_placement(
    truth: FieldTruth,
    regions: RegionsJson,
    words: Sequence[PageWords],
    boxes: BoxTruth | None = None,
) -> dict[str, Any]:
    """One method's boxes for one field: the words test (``status``, ``strict``), and with a
    box truth, ``occurrence`` and ``iou``:

    * ``occurrence``: the words test passes and a box on the truth page covers the value's
      own occurrence: it contains the centre of the value's box inside the truth quote
      (``value_box``), or, when the value is not found once inside the quote, its centre
      lies inside the quote's box. A box the words test accepted on an ``also_on`` page
      (the value printed again where the truth allows it) passes: the quote locates the
      truth page only;
    * ``iou``: the first box against ``value_box``; 0 when there is no box or the first box
      is on another page (an ``also_on`` page included)."""
    status = highlight_status(truth, regions, words) or "unchecked"
    boxes = boxes or BoxTruth(None, None)
    on_page = [r for r in regions if r.get("page") == truth.page]
    occurrence: bool | None = None
    if boxes.quote_box is not None:
        if status != "correct":
            occurrence = False
        elif not on_page:
            occurrence = True  # accepted on an also_on page (docstring)
        elif boxes.value_box is not None:
            value = boxes.value_box
            occurrence = any(_centred(value, r["bbox"], slack=0.002) for r in on_page)
        else:
            quote = boxes.quote_box
            occurrence = any(_centred(r["bbox"], quote) for r in on_page)
    overlap: float | None = None
    if boxes.value_box is not None:
        first = regions[0] if regions else None
        overlap = (
            iou(first["bbox"], boxes.value_box)
            if first is not None and first.get("page") == truth.page
            else 0.0
        )
    return {
        "status": status,
        "placed": bool(regions),
        "strict": status == "correct" and exact(truth, regions, words),
        "words": box_words(regions, words),
        "area": box_area(regions),
        "occurrence": occurrence,
        "iou": round(overlap, 4) if overlap is not None else None,
    }


# --------------------------------------------------------------------------- the comparison


@dataclass
class Comparison:
    rows: list[Row]
    reader: Reader
    today_from: TodayFrom
    seconds: dict[str, float]  # placement time per method, summed over the rows
    agreement: dict[str, int]  # the run's stored boxes against today's rule recomputed
    pair_coverage: dict[str, int]  # Azure's pair box over every printed headline field
    scorer_highlight: dict[str, Any]  # the scorer's own highlight block on the same input
    documents: list[str]
    skipped: list[dict[str, str]]
    pages_1004: list[int] = field(default_factory=list)  # pages of each 1004 analysis found


def _outcomes(score: Mapping[str, Any]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    """The scorer's headline field rows (verified catalogue keys, first repeat)."""
    return {
        (str(r["set"]), str(r["document"]), str(r["key"])): r
        for r in score.get("fields", ())
        if int(r.get("repeat", 1)) == 1 and r.get("verified") and not r.get("extra")
    }


def _first_ok_repeat(prediction: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for rep in prediction.get("repeats", ()):
        if rep.get("status") == "ok" and int(rep.get("repeat", 1)) == 1:
            return dict(rep)
    return None


def reader_of(predictions: Mapping[str, Any]) -> Reader:
    """``grounded`` when the run kept the reader's verbatim quotes, else ``pipeline``."""
    for doc in predictions.get("documents", ()):
        for rep in doc.get("repeats", ())[:1]:
            if any(f.get("quote") for f in (rep.get("fields") or {}).values()):
                return "grounded"
    return "pipeline"


def compare(
    truths: Mapping[str, TruthSet],
    predictions: Mapping[str, Any],
    *,
    layouts: LayoutFor,
    words_for: WordsFor,
    texts_for: TextsFor,
    documents: Sequence[str] | None = None,
    reader: Reader | None = None,
    today_from: TodayFrom = "stored",
    forms_1004: FormFor | None = None,
) -> Comparison:
    """Score every method on the population (module doc). ``documents``: ``set/document``
    names to keep (``None``: every document the predictions hold); ``reader``: default
    :func:`reader_of`; ``today_from``: Basis's rule as stored or recomputed (module doc);
    ``forms_1004``: the recorded 1004 model analysis of a document (``None``: no boxes)."""
    wanted = set(documents) if documents is not None else None
    chosen = [
        d
        for d in predictions.get("documents", ())
        if wanted is None or f"{d['set']}/{d['document']}" in wanted
    ]
    reader = reader or reader_of(predictions)
    scored = score_run(
        truths,
        {**predictions, "documents": chosen},
        page_texts=texts_for,
        page_words=words_for,
    )
    outcomes = _outcomes(scored)
    rows: list[Row] = []
    seconds = {name: 0.0 for name, _ in METHODS}
    agreement = {"rows": 0, "equal": 0, "differ": 0}
    coverage = {"printed": 0, "with_pair_box": 0, "with_1004_box": 0}
    pages_1004: list[int] = []
    skipped: list[dict[str, str]] = []
    seen: list[str] = []
    for prediction in chosen:
        set_name, name = str(prediction["set"]), str(prediction["document"])
        label = f"{set_name}/{name}"
        truth_set = truths.get(set_name)
        doc = (
            next((d for d in truth_set.documents if d.document == name), None)
            if truth_set
            else None
        )
        rep = _first_ok_repeat(prediction)
        if doc is None or rep is None:
            skipped.append({"document": label, "reason": "no truth" if doc is None else "failed"})
            continue
        result = layouts(doc)
        if result is None or not has_geometry(result):
            skipped.append({"document": label, "reason": "no Azure DI geometry"})
            continue
        words = words_for(doc)
        if words is None:
            skipped.append({"document": label, "reason": "no PDF"})
            continue
        seen.append(label)
        layout = layout_of(result)
        t0 = time.perf_counter()
        pairs = azure_pairs(result, uad36=is_uad36(result, texts_for(doc)))
        pair_seconds = time.perf_counter() - t0
        raw_1004 = forms_1004(doc) if forms_1004 is not None else None
        t0 = time.perf_counter()
        boxes_1004 = azure_1004_regions(raw_1004) if raw_1004 is not None else {}
        form_seconds = time.perf_counter() - t0
        if raw_1004 is not None:
            pages_1004.append(len(raw_1004.get("pages") or ()))
        fields: Mapping[str, Any] = rep.get("fields", {})
        for truth in doc.fields:
            headline = outcomes.get((set_name, name, truth.key))
            if headline is not None and truth.status == "present" and truth.page is not None:
                coverage["printed"] += 1
                coverage["with_pair_box"] += truth.key in pairs
                coverage["with_1004_box"] += truth.key in boxes_1004
            if not checkable(truth, words):
                continue
            pred = fields.get(truth.key)
            if headline is None or headline.get("outcome") != "correct" or pred is None:
                continue
            item = Placement(
                key=truth.key,
                value_text=pred.get("value_text"),
                value_num=pred.get("value_num"),
                value_date=pred.get("value_date"),
                source=str(pred.get("source") or "llm"),
                page=pred.get("page"),
                quote=pred.get("quote"),
                stored=list(pred.get("regions") or []),
                pair=pairs.get(truth.key),
                form_1004=boxes_1004.get(truth.key, ([], []))[0],
                form_1004_narrowed=boxes_1004.get(truth.key, ([], []))[1],
            )
            row = Row(
                set=set_name,
                document=name,
                key=truth.key,
                source=item.source,
                tier=str(headline.get("tier") or ""),
                kind="checkbox" if truth.key in CHECKBOX_KEYS else "value",
            )
            boxes = box_truth(truth, words)
            t0 = time.perf_counter()
            recomputed = recompute_today(item, layout, reader)
            today_seconds = time.perf_counter() - t0
            seconds[TODAY] += today_seconds
            agreement["rows"] += 1
            agreement["equal" if recomputed == item.stored else "differ"] += 1
            today = item.stored if today_from == "stored" else recomputed
            for method, _ in METHODS:
                t0 = time.perf_counter()
                regions = place(method, item, layout, today)
                spent = time.perf_counter() - t0
                if method in (
                    AZURE_ELSE_TODAY,
                    TODAY_ELSE_AZURE,
                    CHECKED_PAIR,
                    CHECKED_PAIR_WORDS,
                    QUOTE_VALUE,
                ):
                    spent += today_seconds  # a combination pays for Basis's rule too
                if method != TODAY:
                    seconds[method] += spent
                row.by_method[method] = score_placement(truth, regions, words, boxes)
            row.stored = score_placement(truth, item.stored, words, boxes)
            row.recomputed = score_placement(truth, recomputed, words, boxes)
            rows.append(row)
        # The pair mapping is the cost of every method that reads Azure's box.
        for method in (
            AZURE_PAIR,
            AZURE_ELSE_TODAY,
            TODAY_ELSE_AZURE,
            CHECKED_PAIR,
            CHECKED_PAIR_WORDS,
        ):
            seconds[method] += pair_seconds
        for method in (AZURE_1004, AZURE_1004_NARROWED):
            seconds[method] += form_seconds
    return Comparison(
        rows=rows,
        reader=reader,
        today_from=today_from,
        seconds=seconds,
        agreement=agreement,
        pair_coverage=coverage,
        scorer_highlight={k: v for k, v in (scored.get("highlight") or {}).items() if k != "by"},
        documents=seen,
        skipped=skipped,
        pages_1004=pages_1004,
    )


# --------------------------------------------------------------------------- the numbers


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))
    return round(ordered[index], 6)


def block(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The counts and rates of one method over a list of field scores."""
    counts = {status: sum(s["status"] == status for s in scores) for status in STATUSES}
    counts["unchecked"] = sum(s["status"] == "unchecked" for s in scores)
    placed = sum(bool(s["placed"]) for s in scores)
    correct = counts["correct"]
    strict = sum(bool(s["strict"]) for s in scores)
    words = [float(s["words"]) for s in scores if s["words"] is not None]
    areas = [float(s["area"]) for s in scores if s["area"] is not None]
    occurrences = [bool(s["occurrence"]) for s in scores if s.get("occurrence") is not None]
    combined = sum(
        bool(s["occurrence"]) if s.get("occurrence") is not None else s["status"] == "correct"
        for s in scores
    )
    overlaps = [float(s["iou"]) for s in scores if s.get("iou") is not None]
    return {
        "fields": len(scores),
        "placed": placed,
        "coverage": _rate(placed, len(scores)),
        **counts,
        "right_among_placed": _rate(correct, placed),
        "right_among_all": _rate(correct, len(scores)),
        "strict": strict,
        "strict_among_all": _rate(strict, len(scores)),
        "words_median": _median(words),
        "words_p95": _percentile(words, 0.95),
        "area_median": _median(areas),
        "area_p95": _percentile(areas, 0.95),
        "right_combined": combined,
        "right_combined_rate": _rate(combined, len(scores)),
        "occurrence_fields": len(occurrences),
        "occurrence_right": sum(occurrences),
        "occurrence_rate": _rate(sum(occurrences), len(occurrences)),
        "iou_fields": len(overlaps),
        "iou50": sum(v >= 0.5 for v in overlaps),
        "iou50_rate": _rate(sum(v >= 0.5 for v in overlaps), len(overlaps)),
        "iou_mean": round(statistics.fmean(overlaps), 4) if overlaps else None,
    }


def method_block(rows: Sequence[Row], method: str) -> dict[str, Any]:
    return block([r.by_method[method] for r in rows if method in r.by_method])


def _group(rows: Sequence[Row], attribute: str) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {}
    for row in rows:
        groups.setdefault(str(getattr(row, attribute)), []).append(row)
    return dict(sorted(groups.items()))


def summarize(comparison: Comparison) -> dict[str, Any]:
    """Every number of the run (``run.json``): per method overall, by set, by the reader
    the value came from and by tier; the regression block; the agreement check."""
    rows = comparison.rows
    methods = {
        name: {
            **method_block(rows, name),
            "seconds": round(comparison.seconds.get(name, 0.0), 4),
        }
        for name, _ in METHODS
    }
    by = {
        attribute: {
            group: {name: method_block(members, name) for name, _ in METHODS}
            for group, members in _group(rows, attribute).items()
        }
        for attribute in ("set", "source", "tier", "kind")
    }
    stored = block([r.stored for r in rows if r.stored])
    recomputed = block([r.recomputed for r in rows if r.recomputed])
    scorer = comparison.scorer_highlight
    return {
        "schema": SUMMARY_SCHEMA,
        "reader": comparison.reader,
        "today_from": comparison.today_from,
        "documents": comparison.documents,
        "skipped": comparison.skipped,
        "fields": len(rows),
        "methods": methods,
        "by": by,
        "regression": {
            "stored": stored,
            "recomputed": recomputed,
            "scorer_highlight": scorer,
            "scorer_agrees_with_stored": (
                scorer.get("checked") == stored["fields"]
                and scorer.get("correct") == stored["correct"]
            ),
        },
        "agreement": comparison.agreement,
        "pair_coverage": {
            **comparison.pair_coverage,
            "rate": _rate(
                comparison.pair_coverage["with_pair_box"], comparison.pair_coverage["printed"]
            ),
            "rate_1004": _rate(
                comparison.pair_coverage["with_1004_box"], comparison.pair_coverage["printed"]
            ),
        },
        "form_1004": {
            "documents": len(comparison.pages_1004),
            "pages": sum(comparison.pages_1004),
            # What running the model in production would add per appraisal (it is not run
            # today), at the list price the harness prices Azure DI pages with.
            "usd_per_appraisal": round(
                sum(comparison.pages_1004)
                * AZURE_USD_PER_1000_PAGES[MORTGAGE_1004_MODEL_ID]
                / 1000
                / len(comparison.pages_1004),
                4,
            )
            if comparison.pages_1004
            else None,
        },
    }


def pick(summary: Mapping[str, Any]) -> str:
    """The method with the most right boxes by the strictest test each field allows
    (``right_combined``: on the value's own occurrence where the truth quote locates it,
    else the words test); ties by the words test alone, then exact boxes, then time (cost
    ties at $0, except the 1004 model's methods, which would add spend)."""
    methods: Mapping[str, Mapping[str, Any]] = summary["methods"]
    return max(
        methods,
        key=lambda name: (
            methods[name].get("right_combined", methods[name]["correct"]),
            methods[name]["correct"],
            methods[name]["strict"],
            -float(methods[name]["seconds"]),
        ),
    )


# --------------------------------------------------------------------------- the table


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def render_comparison(summary: Mapping[str, Any], *, population: str = "") -> str:
    """The comparison table in the EVAL_REPORT house style (counts only, no values)."""
    n = int(summary["fields"])
    docs = len(summary["documents"])
    reading = population or f"the {summary.get('reader', 'pipeline')} reading"
    out = [
        "**How to read this table.** When the review page highlights where a field's value "
        "is printed, which way of placing the box is most accurate?",
        "",
        "- **Models**: the exact model id, its provider, the reasoning effort and the "
        'temperature as sent. "No AI model" means the method is a rule over Azure\'s recorded '
        "page reading (words, checkboxes and key-value pairs), replayed from the recordings: "
        "no model call and no Azure call were made.",
        f"- **Boxes placed (of {n:,})**: Fields the method puts a box on at all, of the "
        f"{n:,} correctly read fields of {reading} on {docs} appraisals whose page has a text "
        "layer (coverage; the review page falls back to its own text search for the rest).",
        "- **Right among placed** (higher is better): Of the boxes placed, the share whose "
        "first box is on the field's page and holds the printed value's words.",
        "- **Right among all fields** (higher is better): The same count over every field of "
        "the population, a field with no box counting as not right: the number the "
        "engineer experiences.",
        "- **Exactly the value** (higher is better): Right boxes whose words are the value "
        "and nothing else (a tight box, not a whole line), as a share of all fields.",
        "- **Wrong box / wrong page** (lower is better): Boxes placed that hold other words, "
        "or lie on another page than the truth's.",
        "- **On the value's own occurrence** (higher is better): The stricter, geometric test, "
        "over the fields whose truth quote (the value's printed context) is found exactly once "
        "on the page's text layer: the box holds the value and a box is centred inside the "
        "quote's words, so a box on another printing of the same value (a city name printed "
        "again as the county) fails it. No box counts as a miss.",
        "- **IoU ≥ 0.5 with the value's words** (higher is better): The literature's "
        "localization test: the first box's intersection over union with the box of the "
        "printed value's words inside the truth quote, at least 0.5, over the fields where "
        "that value box is found exactly once (no box, or another page, is IoU 0).",
        "- **Median box size (words)**: Text-layer words inside the first box, median over "
        "the boxes placed; 1 is one word.",
        "- **Placement time (s)** (lower is better): Wall time of the placement rule alone "
        "over the population (the recorded page reading already loaded), summed; a "
        "combination pays for both parts.",
        "- **Cost per appraisal** (lower is better): Model and Azure spend per appraisal; "
        "every method reuses the Azure DI analysis the extraction already paid for.",
        '- **Tokens (input / output)**: Tokens the run used; "None" means no model was called.',
        "",
        "| Placement method | Models (provider, effort, temperature) | Boxes placed "
        f"(of {n:,}) | Right among placed ↑ | Right among all fields ↑ | Exactly the value ↑ "
        "| Wrong box ↓ | Wrong page ↓ | On the value's own occurrence ↑ | IoU ≥ 0.5 with the "
        "value's words ↑ | Median box size (words) | Placement time (s) ↓ | "
        "Cost per appraisal ↓ | Tokens (input / output) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    best = pick(summary)
    models = (
        "No AI model; Azure's recorded page reading: `prebuilt-layout (Azure Document "
        "Intelligence)` with the key-value pairs add-on (Azure), replayed"
    )
    models_1004 = (
        "No AI model; Azure's recorded form reading: `prebuilt-mortgage.us.1004 (Azure "
        "Document Intelligence)` (Azure), replayed"
    )
    form = summary.get("form_1004") or {}
    form_cost = (
        f"${form['usd_per_appraisal']:.3f} more (Azure's 1004 model analysis, which production "
        "does not run; recorded here)"
        if form.get("usd_per_appraisal") is not None
        else "n/a (no 1004 analysis recorded)"
    )
    for name, description in METHODS:
        m = summary["methods"][name]
        title = f"**{description}**" if name == best else description
        cost = (
            form_cost
            if name in (AZURE_1004, AZURE_1004_NARROWED)
            else "$0 (the extraction's Azure DI analysis, reused)"
        )
        model = models_1004 if name in (AZURE_1004, AZURE_1004_NARROWED) else models
        out.append(
            f"| {title} | {model} | {_fmt(m['placed'])} ({_fmt(m['coverage'])}) | "
            f"{_fmt(m['right_among_placed'])} ({_fmt(m['correct'])} / {_fmt(m['placed'])}) | "
            f"{_fmt(m['right_among_all'])} ({_fmt(m['correct'])} / {n:,}) | "
            f"{_fmt(m['strict_among_all'])} ({_fmt(m['strict'])}) | {_fmt(m['wrong_box'])} | "
            f"{_fmt(m['wrong_page'])} | {_fmt(m['occurrence_rate'])} "
            f"({_fmt(m['occurrence_right'])} / {_fmt(m['occurrence_fields'])}) | "
            f"{_fmt(m['iou50_rate'])} ({_fmt(m['iou50'])} / {_fmt(m['iou_fields'])}) | "
            f"{_fmt(m['words_median'])} | {m['seconds']:.3f} | "
            f"{cost} | None |"
        )
    pair = summary["pair_coverage"]
    agreement = summary["agreement"]
    out += [
        "",
        f"Azure paired a value box with {_fmt(pair['with_pair_box'])} of the "
        f"{_fmt(pair['printed'])} printed headline fields ({_fmt(pair['rate'])}), read right "
        f"or not; Azure's 1004 model a field box with {_fmt(pair.get('with_1004_box'))} "
        f"({_fmt(pair.get('rate_1004'))}). Basis's rule is taken as "
        f"{summary.get('today_from', 'stored')}; recomputed at "
        f"this commit it equals the stored box on {_fmt(agreement['equal'])} of "
        f"{_fmt(agreement['rows'])} fields.",
    ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- the run


def default_layouts(appraisal: Path) -> LayoutFor:
    """The recorded Azure DI analysis of a document (``recordings/<sha>/azure_di.json``)."""

    def load(doc: DocumentTruth) -> AzureDiResult | None:
        path = recording_dir(appraisal, doc.sha256) / AZURE_RAW
        if not path.is_file():
            return None
        return from_sdk(rebuild_sdk_result(json.loads(path.read_text(encoding="utf-8"))))

    return load


def default_forms_1004(appraisal: Path) -> FormFor:
    """The recorded 1004 model analysis (``recordings/<sha>/azure_1004.json``), raw."""

    def load(doc: DocumentTruth) -> Mapping[str, Any] | None:
        path = recording_dir(appraisal, doc.sha256) / AZURE_1004_RAW
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, Mapping) else None

    return load


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _source_run(run_dir: Path) -> dict[str, Any]:
    """What the input run was: its id, system, mode, commit and start (``run.json``)."""
    out: dict[str, Any] = {"directory": str(run_dir)}
    path = run_dir / "run.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        system = data.get("system") if isinstance(data.get("system"), Mapping) else {}
        out.update(
            run=data.get("run"),
            mode=data.get("mode"),
            system=system.get("label") or system.get("name"),
            git_sha=data.get("git_sha"),
            started=data.get("started"),
            models=data.get("models"),
        )
    return out


def truth_overrides(items: Sequence[str]) -> dict[str, Path]:
    """``SET=PATH`` options as a mapping; an unknown set or a missing file is an error."""
    out: dict[str, Path] = {}
    for item in items:
        name, sep, path = item.partition("=")
        if not sep or name not in SETS or not Path(path).is_file():
            raise SystemExit(f"--truth {item!r}: expected SET=PATH, SET one of {SETS}")
        out[name] = Path(path)
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--run", type=Path, required=True, help="an evals.appraisal.run directory")
    parser.add_argument("--all", action="store_true", help="every document of the run")
    parser.add_argument("--documents", default="", help="comma-separated set/document names")
    parser.add_argument("--reader", choices=("pipeline", "grounded"), default=None)
    parser.add_argument("--today", choices=("stored", "recomputed"), default="stored")
    parser.add_argument(
        "--truth",
        action="append",
        default=[],
        metavar="SET=PATH",
        help="score a set against another truth file (the truth as an older run saw it)",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None, help="default BASIS_EVAL_DATA_DIR")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    appraisal = appraisal_dir(args.data_dir)
    predictions_path = Path(args.run) / "predictions.json"
    if not predictions_path.is_file():
        print(f"no predictions.json in {args.run}", file=sys.stderr)
        return 2
    if args.documents:
        documents: Sequence[str] | None = tuple(
            d.strip() for d in str(args.documents).split(",") if d.strip()
        )
    else:
        documents = None if args.all else SUBSET_16
    scope = "all" if args.all else ("documents" if args.documents else "subset16")
    out = Path(args.out) if args.out else appraisal / "runs" / f"{_stamp()}-{COMPONENT}-{scope}"
    run = record.EvalRun.begin(out, harness=__name__, args=args, component=COMPONENT)
    source = _source_run(Path(args.run))
    run.input("predictions", predictions_path)
    run.note(source_run=source, scope=scope, methods=dict(METHODS))
    overrides = truth_overrides(args.truth)
    truths: dict[str, TruthSet] = {}
    for name in SETS:
        path = overrides.get(name, appraisal / "truth" / f"{name}.json")
        if path.is_file():
            truths[name] = TruthSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
            run.dataset(path, name=name)
    if overrides:
        run.note(truth_overrides={k: str(v) for k, v in overrides.items()})
    predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
    from evals.appraisal.run import page_texts_for, page_words_for

    comparison = compare(
        truths,
        predictions,
        layouts=default_layouts(appraisal),
        words_for=page_words_for(appraisal),
        texts_for=page_texts_for(appraisal),
        documents=documents,
        reader=args.reader,
        today_from=args.today,
        forms_1004=default_forms_1004(appraisal),
    )
    for doc in comparison.documents:
        set_name, name = doc.split("/", 1)
        entry = next(d for d in truths[set_name].documents if d.document == name)
        run.input(f"azure_di/{doc}", recording_dir(appraisal, entry.sha256) / AZURE_RAW)
        form_path = recording_dir(appraisal, entry.sha256) / AZURE_1004_RAW
        if form_path.is_file():
            run.input(f"azure_1004/{doc}", form_path)
    for row in comparison.rows:
        for method, score in row.by_method.items():
            run.case(
                f"{row.set}/{row.document}/{row.key}#{method}",
                record.PASSED if score["status"] == "correct" else record.FAILED,
                kind="highlight",
                detail={"method": method, **score, "source": row.source, "tier": row.tier},
            )
    summary = summarize(comparison)
    summary["source_run"] = source
    summary["pick"] = pick(summary)
    (out / "comparison.md").write_text(render_comparison(summary), encoding="utf-8")
    run.finish(summary)
    m = summary["methods"]
    print(
        f"{out}: {summary['fields']} fields on {len(comparison.documents)} documents "
        f"({comparison.reader} reading)"
    )
    for name, _ in METHODS:
        print(
            f"  {name:17} placed {m[name]['placed']:5}  right {m[name]['correct']:5} "
            f"({m[name]['right_among_all']})  exact {m[name]['strict']:5}  "
            f"wrong_box {m[name]['wrong_box']:3}  wrong_page {m[name]['wrong_page']:3}  "
            f"words {m[name]['words_median']}  {m[name]['seconds']} s"
        )
    regression = summary["regression"]
    print(
        f"  stored: {regression['stored']['correct']} / {regression['stored']['fields']}; "
        f"recomputed: {regression['recomputed']['correct']} / "
        f"{regression['recomputed']['fields']}; "
        f"scorer {regression['scorer_highlight'].get('correct')} / "
        f"{regression['scorer_highlight'].get('checked')}; agreement {summary['agreement']}"
    )
    print(f"  pick: {summary['pick']}; skipped {comparison.skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
