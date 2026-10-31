"""Response bodies of the revision's outputs: extraction fields, rooms, assets (v0.9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.api.schemas.evidence import RoomRefOut
from basis.api.schemas.timeline import ProvenanceOut
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
    pdf_document_id: uuid.UUID | None = Field(
        description=(
            "The document whose PDF `regions` are drawn on (render it with "
            "`GET .../documents/{pdf_document_id}/pdf`): `document_id` itself for a PDF or a "
            "UAD 3.6 ZIP, the companion PDF for a bare MISMO XML, null when there is no "
            "report PDF (no regions). Always the PDF of this field's own extraction revision."
        )
    )
    key: str = Field(description="`<section>.<field>`, the catalogue key.")
    section: str
    field: str
    label: str | None = Field(description="The caption printed on the form, when catalogued.")
    critical: bool = Field(description="The catalogue marks the field critical.")
    value_text: str | None
    value_num: Decimal | None
    value_date: date | None
    confidence: float | None
    source: str = Field(
        description="`azure`, `regex`, `table`, `llm`, `mismo_xml` (a UAD 3.6 XML) or `manual`."
    )
    page: int | None
    regions: list[FieldRegionOut] = Field(
        description=(
            "Where the value is printed, to highlight on the PDF (docs/api/FIELD_REGIONS.md): "
            "boxes on `page` as page fractions, origin top-left. Empty when the value was not "
            "located (then fall back to searching the page's text)."
        )
    )
    provenance: ProvenanceOut | None = Field(
        default=None,
        description="Who produced the value: the model, or the engineer's correction "
        "(docs/api/TIMELINE_AND_AUDIT.md).",
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
    provenance: ProvenanceOut | None = Field(
        default=None,
        description="Who put the photo in this room: the model's grouping, or an engineer's "
        "`photo_room` move.",
    )


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
    provenance: ProvenanceOut | None = Field(
        default=None,
        description="Who produced the row: the model, an engineer's addition, or an "
        "engineer's correction of its label, material, attachment or count.",
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
    provenance: ProvenanceOut | None = Field(
        default=None,
        description="Who produced the room: the model's grouping, or an engineer's rename, "
        "retype or floor correction.",
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
    method: str | None = Field(
        default=None,
        description="How the quantity was measured: `count`, `area` or `perimeter` (the "
        "takeoff inputs' `method`); null for a takeoff stored without it.",
    )
    inputs: dict[str, Any] | None = Field(
        default=None,
        description="The takeoff inputs exactly as stored when the asset was measured: the "
        "room area and its source, the appraisal fields, the cost index sources, an "
        "engineer's `asset_correction`, a concealed row's `label` and `system`. Keys vary by "
        "takeoff; show them, do not compute from them.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="The takeoff's stated assumptions (the takeoff inputs' `assumptions`).",
    )


class CostTrailStoreRowOut(ResponseModel):
    """The `cost_indices` store row a value was read from (docs/api/COST_INDICES.md)."""

    id: uuid.UUID | None = Field(description="The store row's id.")
    source: str = Field(description="The store source: `manual`, `rsmeans_2020_book`, ...")
    version: int = Field(description="The row's version (a revised value is a new version).")


class CostTrailIndexOut(ResponseModel):
    """One Historical Cost Index value exactly as used."""

    year: int = Field(description="The year the value is for.")
    value: Decimal = Field(description="The index (Jan. 1, 1993 = 100), as published.")
    as_of: date = Field(description="The date the value is for (July 1, or Jan. 1, 2020).")
    citation: str = Field(description="The book and page, or the store row's citation.")
    estimated: bool = Field(description="The publication marks the value estimated.")
    derived: str | None = Field(
        description="How the value was converted from another public series, when it was."
    )
    store: CostTrailStoreRowOut | None = Field(
        description="The store row it was read from; null when it came from the released book file."
    )


class CostTrailQuantityOut(ResponseModel):
    """The quantity priced, as measured."""

    value: Decimal = Field(description="The quantity priced.")
    unit: str = Field(description="Its unit (`EA`, `SF`, `LF`, `SY`).")
    formula: str | None = Field(description="How it was computed, in words.")
    method: str | None = Field(description="`count`, `area` or `perimeter`.")
    inputs: dict[str, Any] = Field(description="The takeoff inputs as written at pricing.")
    assumptions: list[str] = Field(description="The takeoff's assumptions.")


class CostTrailBaseOut(ResponseModel):
    """The base unit cost exactly as multiplied, and the cost row it came from."""

    unit_cost: Decimal = Field(
        description="Per takeoff unit, at the row's base year, before the year and location "
        "factors; not rounded (it may carry more than two decimals). This, not a quotient "
        "of the rounded amounts, is the base price."
    )
    unit: str = Field(description="The takeoff unit the base is per.")
    table: str = Field(
        description="The cost table: `TYPICAL_UNIT_COSTS` (the legacy placeholder rows) or "
        "the configured table's name."
    )
    row: str = Field(description="The row: the legacy component key or the table's label.")
    tier: str | None = Field(description="The legacy row's tier; null for a table row.")
    source: str | None = Field(
        description="The row's printed source (book, line, page) as the table records it; "
        "null for a legacy placeholder row, which has none."
    )
    base_year: int = Field(description="The year the row's costs are as of.")
    row_unit: str = Field(description="The unit the row is printed per.")
    conversion: str | None = Field(
        description="How the row's unit became the takeoff's (`x9 from per SF`, `x0.01 from "
        "per Sq.`); null when none was needed."
    )
    assumption: str | None = Field(description="The table row's stated assumption.")


class CostTrailYearOut(ResponseModel):
    """The time factor exactly as used."""

    cost_year: int = Field(description="The cost year priced to.")
    factor: Decimal = Field(description="`index.value / base.value`, to four decimals.")
    index: CostTrailIndexOut = Field(
        description="The cost year's index value (or the "
        "latest loaded one under the missing-year rule)."
    )
    base: CostTrailIndexOut = Field(description="The books' cost basis (Jan. 1, 2020).")
    assumption: str | None = Field(
        description="Set when no index for the cost year was loaded and the latest applied."
    )
    conversion: str | None = Field(description="Set when the index value was converted.")
    citation: str = Field(description="The factor's citation, in words.")
    from_: str = Field(
        alias="from",
        description="Where the cost year came from (the intake, the appraisal, the setting).",
    )


class CostTrailLocationOut(ResponseModel):
    """The location factor exactly as used, with the row(s) it came from."""

    factor: Decimal = Field(description="The location factor applied.")
    from_: str = Field(
        alias="from",
        description="Where it came from, in words (the setting when nothing else applied).",
    )
    lookup: Literal["zip", "state"] | None = Field(
        description="Looked up by ZIP (a city row) or by state (the mean of its cities); "
        "null for the setting."
    )
    zip: str | None = Field(description="The ZIP looked up, when the factor came from one.")
    lookup_from: str | None = Field(
        description="Where the ZIP or state came from: `intake` or the appraisal field."
    )
    state: str | None = Field(description="The state of the row(s).")
    city: str | None = Field(description="The city row's name; null for a state mean.")
    zip_prefixes: str | None = Field(description="The city row's ZIP prefixes as printed.")
    cities: int | None = Field(description="Rows averaged (1 for a city).")
    low: Decimal | None = Field(description="The lowest factor averaged.")
    high: Decimal | None = Field(description="The highest factor averaged.")
    pages: str | None = Field(description="The printed page(s).")
    table_year: int | None = Field(description="The location table's year.")
    origin: str | None = Field(
        description="The location table's source (`rsmeans_2020_book`, `manual`, ...)."
    )
    citation: str | None = Field(description="The factor's citation, in words.")
    store: CostTrailStoreRowOut | None = Field(
        description="The city row's store row; null for a state mean or the book file's row."
    )


class CostTrailBuildingOut(ResponseModel):
    """The building replacement cost new a residual was carved from."""

    amount: Decimal = Field(description="The building RCN at the cost year.")
    source: str = Field(description="`appraisal_cost_approach` or `square_foot_model`.")
    formula: str = Field(description="How it was computed, in words.")
    inputs: dict[str, Any] = Field(
        description="Its inputs, including the appraiser's cost data date and time "
        "adjustment when the appraisal's cost new was used."
    )
    assumptions: list[str] = Field(description="Its assumptions.")


class CostTrailStructureOut(ResponseModel):
    """A concealed row of the structure model."""

    label: str = Field(description="The concealed row's label.")
    system: str = Field(description="The building system.")
    component: str | None = Field(description="The RSMeans component it is carved from.")
    source: str = Field(description="The structure model's source for the row.")
    rcn: Decimal | None = Field(
        description="The residual RCN written as the total; null for a small system priced "
        "from the cost table."
    )
    building: CostTrailBuildingOut | None = Field(
        description="The building RCN of a residual; null for a small system."
    )


class CostTrailOut(ResponseModel):
    """How the cost row's amounts were reached, recorded once when the row was priced and
    never recomputed (docs/api/COST_INDICES.md, "Per-asset cost trail")."""

    version: int = Field(description="The trail's shape version (1).")
    kind: Literal["unit_price", "unpriced", "structure_residual"] = Field(
        description="`unit_price`: base x year factor x location factor x quantity; "
        "`unpriced`: no cost row (see `missing`); `structure_residual`: a concealed row whose "
        "total is the residual of the building RCN."
    )
    quantity: CostTrailQuantityOut
    base: CostTrailBaseOut | None = Field(description="Null when no cost row was used.")
    year: CostTrailYearOut | None = Field(
        description="Null for a residual of the appraiser's cost new (its own time "
        "adjustment is in `structure.building.inputs`)."
    )
    location: CostTrailLocationOut | None = Field(
        description="Null when no location factor applied (the appraiser's cost new)."
    )
    structure: CostTrailStructureOut | None = Field(
        description="Set for a concealed row of the structure model."
    )
    intake_version: int | None = Field(
        description="The study's intake version priced from; null without an intake."
    )
    property_class: str = Field(description="The property class priced under.")
    cost_year: int = Field(description="Equals the cost row's `cost_year`.")
    location_factor: Decimal | None = Field(description="Equals the cost row's factor.")
    unit_cost: Decimal | None = Field(description="Equals the cost row's `unit_cost`.")
    total: Decimal | None = Field(description="Equals the cost row's `total`.")
    rounding: str | None = Field(description="The rounding rule, in words.")
    priced_per: str | None = Field(
        description="For `unpriced`: the unit a cost table row prices the label in when the "
        "takeoff is in another unit."
    )
    missing: str | None = Field(description="For `unpriced`: why, as in `source_row`.")


class CostOut(ResponseModel):
    unit_cost: Decimal | None = Field(description="Null when no cost row priced it.")
    total: Decimal | None
    source_row: str | None
    location_factor: Decimal | None
    cost_year: int | None
    trail: CostTrailOut | None = Field(
        default=None,
        description="How `unit_cost` and `total` were reached: the quantity and its takeoff "
        "inputs, the base unit cost and its cost row, the year and location factors with "
        "the index values and store rows used, recorded when the row was priced. Show it as "
        "recorded: never derive a base price from the rounded amounts, and never "
        "substitute today's index. Null for a row priced before the trail was recorded "
        "(`trail_recorded` false): such a row has only `source_row`.",
    )
    trail_recorded: bool = Field(
        default=False,
        description="Whether the row was priced with a trail (false: a legacy row, `trail` null).",
    )


class ConventionOut(ResponseModel):
    """The firm convention an asset was classified from, as of its classification
    (docs/api/TIMELINE_AND_AUDIT.md, "Firm conventions")."""

    key: str = Field(
        description="The served convention's context key: `label|material|attachment|room|"
        "property_class|rules version`. At a coarser `matched_tier` it is the ruling's key, "
        "not the asset's own."
    )
    matched_tier: Literal["exact", "label_room", "label"] = Field(
        default="exact",
        description="How the asset's context met the convention's: `exact` (the same key), "
        "`label_room` (the same label and room, another material or attachment), `label` "
        "(the same label in another room). Assets classified before the hierarchy existed "
        "are `exact`.",
    )
    study_id: uuid.UUID = Field(description="The study whose approval seeded the convention.")
    reviewer_id: uuid.UUID | None = Field(
        default=None, description="The engineer who approved that study's assets."
    )
    approved_at: datetime = Field(description="When that approval was recorded.")
    approvals: int = Field(
        ge=1, description="How many approved studies at the firm gave the same answer."
    )
    rule_key: str | None = Field(
        default=None, description="The rules table's row for the same key; null when none."
    )
    agrees_with_rules: bool | None = Field(
        description="Whether the convention's bucket is the rules table's; null when the "
        "table has no row for the key. A convention that disagrees is still served: it is "
        "the firm's ruling."
    )
    citation: str | None = Field(
        default=None,
        description="`firm convention, uncited` when the convention carries no citation; "
        "null when it does (its citations are re-verified and counted in `citations`).",
    )


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
    review_codes: list[str] = Field(
        default_factory=list,
        description="The error codes behind `review_reasons` (docs/ERROR_CODES.md), in order.",
    )
    provenance: ProvenanceOut | None = Field(
        default=None,
        description="Who produced the classification: the model or rules, an engineer's "
        "`asset` correction, or an engineer-added component. The takeoff and cost share it.",
    )
    convention: ConventionOut | None = Field(
        default=None,
        description="For `method` `cache`: the firm convention it was classified from (who "
        "approved it, on which study, when, how many approvals, whether the rules table "
        "agrees); null otherwise.",
    )


class AssetsPageOut(ResponseModel):
    revision: int
    items: list[AssetOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )


UploadKindOut = Literal["pdf", "zip", "xml"]
CorpusStatus = Literal["ingested", "scanned", "none"]


class ExtractionIssueOut(ResponseModel):
    rule: str = Field(
        description=(
            "What was found. Reader issues: `xml_pdf_mismatch` (warning: an XML value the "
            "report PDF does not print where its caption is), `no_report_pdf` (warning: a bare "
            "XML with no report PDF), `azure_di_rejected` (Azure DI refused the PDF: text layer "
            "only), `sources_disagree`; validator rules such as `missing_critical`, "
            "`out_of_range`, `fallback_unconfirmed`."
        )
    )
    severity: Literal["error", "warning"]
    keys: list[str] = Field(description="The field keys it concerns (may be empty).")
    message: str


class StudyCorpusOut(ResponseModel):
    status: CorpusStatus = Field(
        description=(
            "`ingested`: the report PDF is searchable evidence; `scanned`: it has no text "
            "layer (an empty corpus); `none`: no study corpus (a bare XML with no report PDF), "
            "so classification cites the reference corpus only."
        )
    )
    corpus_version_id: uuid.UUID | None
    document_id: uuid.UUID | None = Field(
        description="The document the corpus passages came from (citations' `doc_id`)."
    )
    chunks: int


class ExtractionOut(ResponseModel):
    """The appraisal extraction of one revision: its source, report PDF and issues."""

    revision: int = Field(description="The extraction's revision (the one asked for).")
    extraction_id: uuid.UUID
    document_id: uuid.UUID = Field(description="The extraction source (the fields' document).")
    upload_kind: UploadKindOut = Field(
        description="`pdf` (legacy 1004 or UAD 3.6 PDF), `zip` (UAD 3.6 delivery), `xml` "
        "(bare MISMO 3.6 XML)."
    )
    pdf_document_id: uuid.UUID | None = Field(
        description=(
            "The document whose PDF this revision's regions are drawn on and whose pages the "
            "reviewer sees: `document_id` for `pdf`/`zip`, the companion PDF for `xml`, null "
            "with no report PDF. Pair fields only with this PDF; it never changes between "
            "revisions of one study."
        )
    )
    regions_available: bool = Field(
        description="At least one field has a highlight region (false: draw none; fall back "
        "to text search, or show that the XML has no PDF)."
    )
    form: str | None = Field(
        description="The printed form of a PDF: `urar_1004_2005`, `uad36_urar` or `unknown`; "
        "null for a UAD 3.6 XML or ZIP (values read from the XML)."
    )
    method: str
    needs_review: bool
    overall_confidence: float | None
    review_reasons: list[str] = Field(
        description="Why the review gate flagged it: `missing:<key>`, "
        "`low_confidence:<key>=<c><<t>`, or `<rule>[:<keys>]` for each issue."
    )
    review_codes: list[str] = Field(
        default_factory=list,
        description="The error codes behind `review_reasons` (docs/ERROR_CODES.md), in order.",
    )
    issues: list[ExtractionIssueOut]
    notes: list[str] = Field(description="Reader notes (informational, not stable text).")
    corpus: StudyCorpusOut
    created_at: datetime
