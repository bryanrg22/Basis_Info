"""Response bodies of the revision's outputs: extraction fields, rooms, assets (v0.9)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.api.schemas.evidence import RoomRefOut
from basis.db.enums import Bucket


class FieldRegionOut(ResponseModel):
    page: int = Field(ge=1, description="The 1-based PDF page the box is on.")
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description=(
            "`[x0, y0, x1, y1]` as fractions of the page's width and height (0..1), origin "
            "top-left: multiply by the rendered page's size in pixels to draw it."
        ),
    )


class ExtractionFieldOut(ResponseModel):
    id: uuid.UUID = Field(
        description="The field's id (the `entity_id` of an `extraction_field` correction)."
    )
    extraction_id: uuid.UUID
    document_id: uuid.UUID = Field(description="The appraisal document it was read from.")
    key: str = Field(description="`<section>.<field>`, the catalogue key.")
    section: str
    field: str
    label: str | None = Field(description="The caption printed on the form, when catalogued.")
    critical: bool = Field(description="The catalogue marks the field critical.")
    value_text: str | None
    value_num: Decimal | None
    value_date: date | None
    confidence: float | None
    source: str = Field(description="`azure`, `regex`, `table`, `llm` or `manual`.")
    page: int | None
    regions: list[FieldRegionOut] = Field(
        description=(
            "Where the value is printed, to highlight on the PDF (docs/api/FIELD_REGIONS.md): "
            "boxes on `page` as page fractions, origin top-left. Empty when the value was not "
            "located (then fall back to searching the page's text)."
        )
    )


class ExtractionFieldsPageOut(ResponseModel):
    revision: int
    items: list[ExtractionFieldOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )


class RoomPhotoOut(ResponseModel):
    document_id: uuid.UUID
    filename: str


class InventoryItemOut(ResponseModel):
    component_id: uuid.UUID = Field(description="The `entity_id` of a `component` correction.")
    label: str
    count: int
    material: str | None
    attachment: str | None
    confidence: float | None
    document_id: uuid.UUID | None = Field(
        description="The photo a per-photo row was found in; null for a reconciled row."
    )
    seen_in: list[uuid.UUID] = Field(
        description="The photos a reconciled object was seen in (empty for a per-photo row)."
    )
    added: bool = Field(
        default=False,
        description="An engineer added it to the room at a rooms review (a "
        "`room_component` correction); its asset is built whatever the room's inventory.",
    )


class SuggestedCountOut(ResponseModel):
    label: str
    inventory_count: int = Field(
        description="The label's count in the room's inventory now (the rows its assets are "
        "built from)."
    )
    photo_count: int = Field(
        description="The most counted instances of the label in one photo of the room: "
        "SAM 3's counted instances plus the engineers' manual ones."
    )
    manual_count: int = Field(description="Of `photo_count`, the manual instances.")
    document_id: uuid.UUID = Field(description="The photo with `photo_count` instances.")
    suggested: int = Field(
        description="max(`inventory_count`, `photo_count`): a room has at least as many as "
        "one photo shows. A suggestion for the engineer to confirm with a `component` "
        "(`count`) or `room_component` correction; nothing changes by itself."
    )


class RoomOut(RoomRefOut):
    source: str = Field(description="`vision` (grouped by the pipeline) or `manual`.")
    group_key: str | None = Field(
        description="The file-name room id the room groups (null: grouped by the model's "
        "room identity)."
    )
    photos: list[RoomPhotoOut] = Field(description="The room's photos, in file-name order.")
    inventory_source: str = Field(
        description="`room_inventory`: one reconciled row per object of the room; "
        "`per_photo`: the per-photo rows (no reconciliation for this room)."
    )
    needs_review: bool = Field(description="The room step flagged the room's inventory.")
    inventory: list[InventoryItemOut]
    suggested_counts: list[SuggestedCountOut] = Field(
        default_factory=list,
        description="Per label with counted instances on the room's photos, by label.",
    )


class RoomsPageOut(ResponseModel):
    revision: int
    items: list[RoomOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )


class TakeoffOut(ResponseModel):
    quantity: Decimal
    unit: str
    formula: str | None


class CostOut(ResponseModel):
    unit_cost: Decimal | None = Field(description="Null when no cost row priced it.")
    total: Decimal | None
    source_row: str | None
    location_factor: Decimal | None
    cost_year: int | None


class AssetOut(ResponseModel):
    id: uuid.UUID = Field(description="The `entity_id` of an `asset` correction.")
    component_id: uuid.UUID | None
    label: str | None = Field(description="The component's label.")
    count: int | None = Field(description="The component's count.")
    room: RoomRefOut | None
    category: str
    bucket: Bucket
    recovery_period: Decimal
    irs_section: str | None
    method: str
    confidence: float | None
    needs_review: bool
    review_reasons: list[str] = Field(description="Why it needs review, one reason per item.")
    takeoff: TakeoffOut | None
    cost: CostOut | None
    citations: int
    verified_citations: int


class AssetsPageOut(ResponseModel):
    revision: int
    items: list[AssetOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )
