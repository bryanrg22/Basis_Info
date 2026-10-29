"""Where each extracted field is printed: the boxes the review page highlights.

The review page used to search the PDF's text layer for the value, so a value printed more
than once ("Montrose" as the city, the county, the neighborhood name and in its description;
"Purchase Transaction" as the checked option and in the contract sentence) lit up every
occurrence. :func:`with_regions` gives each field the box of its own occurrence, from the
geometry Azure DI returned (:mod:`basis.extraction.geometry`), on the field's cited page only:

1. a checkbox field (:data:`~basis.extraction.field_mappings.CHECKBOX_KEYS`): per option of
   the value, the checked box whose label (:mod:`basis.extraction.marks`) names it, the one
   nearest the field's caption (the "Public" box of the Water row, not the Gas row's); the
   region is the box with its label words when they share its line; under a column header,
   the header's words and then the box;
2. a value an Azure DI key-value pair produced: the pair's value box;
3. otherwise the value's words on the page (whole words first), the occurrence nearest the
   field's caption (a whole-word caption: "City" is not the end of "Electricity"; the
   catalogue labels, then :data:`HIGHLIGHT_ANCHORS`), else the first occurrence;
4. otherwise no region: never a box on another page. The page falls back to its own text
   search for these.

Regions never change a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from basis.extraction.azure_di import AzureDiResult
from basis.extraction.field_mappings import CHECKBOX_KEYS, FIELD_BY_KEY, MULTI_CHECKBOX_KEYS
from basis.extraction.geometry import (
    Box,
    Layout,
    Page,
    find_all,
    has_geometry,
    layout_of,
    locate,
    union,
)
from basis.extraction.marks import LabelledMark, fold_label, label_marks, option_checked, same_line
from basis.extraction.results import ExtractedField, Region

_SPLIT: Final = re.compile(r"\s*[,;]\s*")

# Captions printed next to a value that the catalogue's labels (which map Azure DI pairs and
# the regex pass, so they must stay as they are) do not name: the 1004's sentence forms and
# the UAD 3.6 form's captions, taken from the truth quotes of the appraisal sweep. Used only
# to choose which occurrence of a value to highlight.
HIGHLIGHT_ANCHORS: Final[dict[str, tuple[str, ...]]] = {
    "reconciliation.final_market_value": ("Opinion of Market Value", "subject of this report is"),
    "reconciliation.indicated_value_sales_comparison": (
        "Sales Comparison Approach",
        "Indicated Value",
    ),
    "listing_and_contract.seller_concessions": (
        "Total Sales Concessions",
        "Known Sales Concessions",
        "report the total dollar amount",
    ),
    "listing_and_contract.data_source": ("Data Source(s)",),
    "subject.city": ("Physical Address",),
    "subject.zip": ("Physical Address",),
    "improvements.quality": ("Overall Quality", "Quality of Construction"),
    "improvements.garage_cars": ("Garage", "# of Cars"),
    "improvements.fireplaces": ("Total Number", "Fireplace(s) #"),
    "improvements.gla_sqft": ("Finished Above Grade", "Gross Living Area"),
    "improvements.roof_surface": ("Roof Surface", "Roof"),
    "improvements.bathrooms": ("Total Bathrooms", "Bath(s)"),
}


def _distance(a: Box, b: Box) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return float((ax - bx) ** 2 + (ay - by) ** 2)


def caption_box(page: Page | None, key: str) -> Box | None:
    """The first whole-word occurrence of the key's caption (an alias, then a
    :data:`HIGHLIGHT_ANCHORS` phrase) on ``page``."""
    spec = FIELD_BY_KEY.get(key)
    if page is None or spec is None:
        return None
    for label in (*spec.labels, *HIGHLIGHT_ANCHORS.get(key, ())):
        box = locate(page, label, whole_words=True)
        if box is not None:
            return box
    return None


def _mark_boxes(item: LabelledMark) -> list[Box]:
    """The box with its label words on its line; under a column header, the header's words
    and then the box (both are drawn; the header is what reads as the option)."""
    words = [w.box for w in item.words if same_line(w.box, item.mark.box)]
    if words or not item.words:
        return [union([item.mark.box, *words])]
    return [union([w.box for w in item.words]), item.mark.box]


def checkbox_regions(item: ExtractedField, page: Page) -> tuple[Region, ...]:
    """Rule 1: the checked box of each option, nearest the caption; empty when an option has
    no checked box on the page (then the value is located as text)."""
    text = item.value_text or item.raw_text or ""
    options = [o for o in _SPLIT.split(text.strip()) if o]
    if not options:
        return ()
    caption = caption_box(page, item.key)
    checked = [m for m in label_marks(page) if m.mark.selected and m.label]
    if item.key in MULTI_CHECKBOX_KEYS and len(options) == 1:
        # Several checked options read without separators ("Refrigerator Range/Oven
        # Dishwasher"): every checked label printed in the value, as whole words.
        value = f" {fold_label(options[0])} "
        named = [m for m in checked if f" {fold_label(m.label)} " in value]
        if len(named) > 1:
            options = [m.label for m in named]
    boxes: list[Box] = []
    for option in options:
        named = [m for m in checked if option_checked(option, [fold_label(m.label)])]
        if not named:
            return ()
        best = (
            named[0]
            if caption is None
            else min(named, key=lambda m: _distance(m.mark.box, caption))
        )
        for box in _mark_boxes(best):
            if box not in boxes:
                boxes.append(box)
    if len(boxes) > 1 and all(same_line(b, boxes[0]) for b in boxes):
        boxes = [union(boxes)]  # several options of one row: one box over them
    return tuple(Region(page=page.number, bbox=box) for box in boxes)


def text_region(item: ExtractedField, page: Page) -> tuple[Region, ...]:
    """Rule 3: the value's words (whole words first: "2" is not inside "2244"), the
    occurrence nearest the caption; without a caption on the page, the first in reading
    order (measured: refusing these instead lost 64 correct boxes to avoid 4 wrong ones, and
    an empty region sends the page back to its every-match text search)."""
    near = caption_box(page, item.key)
    for text in (item.raw_text, item.value_text):
        if not text:
            continue
        for whole_words in (True, False):
            found = find_all(page, text, whole_words=whole_words)
            if not found:
                continue
            if near is None:
                return (Region(page=page.number, bbox=found[0]),)
            box = min(found, key=lambda b: _distance(b, near))
            return (Region(page=page.number, bbox=box),)
    return ()


def field_regions(item: ExtractedField, layout: Layout) -> tuple[Region, ...]:
    """The field's regions (module doc), on its cited page only."""
    page = layout.page(item.page)
    if page is None or item.is_empty:
        return ()
    if item.key in CHECKBOX_KEYS:
        found = checkbox_regions(item, page)
        if found:
            return found
    own = tuple(r for r in item.regions if r.page == item.page)
    if item.source == "azure" and own:
        return own
    return text_region(item, page)


def with_regions(
    fields: Mapping[str, ExtractedField], azure: AzureDiResult | None
) -> dict[str, ExtractedField]:
    """``fields`` with their regions; unchanged when the analysis kept no geometry (a result
    stored before it did, or no Azure DI)."""
    if azure is None or not has_geometry(azure):
        return dict(fields)
    layout = layout_of(azure)
    return {
        key: item.model_copy(update={"regions": field_regions(item, layout)})
        for key, item in fields.items()
    }


def region_json(regions: Sequence[Region]) -> list[dict[str, Any]]:
    """The stored / API form: ``[{"page": n, "bbox": [x0, y0, x1, y1]}]``, 4 decimals."""
    return [{"page": r.page, "bbox": [round(v, 4) for v in r.bbox]} for r in regions]
