"""The IRS classification rules table: component label -> section, bucket, life, citations.

Migrated from ``agentic/agents/static_classification_rules.py`` (rows and aliases
unchanged unless noted below; the citations were re-cited, see the last paragraph). A hit
classifies a component without a model call; a miss falls through to the classification
cache and then the residual classifier (:mod:`basis.classify.router`).

Changes from the legacy table, each deliberate:

* ``flooring`` and ``floor`` no longer alias to ``carpet``. A generic floor label is
  resolved by its ``material`` (carpet, vinyl, laminate, tile, hardwood); without a
  material it is a miss, so the residual classifier (or the engineer) decides.
* The decision rule for a row's bucket (``RULES_VERSION`` ``2026-10-29.1``,
  ``CLASSIFICATION_CONVENTIONS.md`` section 4): an item the IRS guide names takes the
  guide's bucket, so the plumbing and lighting fixtures, ceiling fans, detectors,
  thermostats, cemented tile, restroom cabinetry and closet shelving that the ATG's
  residential rental matrix (Pub 5653 chapter 7.H, Exhibit A) puts in section 1250 are
  building rows, and furniture is a residential furnishing (57.0, 5 years). An item no
  document names, or where practitioners commonly differ, keeps its bucket and a
  ``contested`` note (what the documents say instead, with pages); a hit on it is
  returned with that note so the asset goes to the engineer (``needs_review``).
* The context dimension: a row can have ``variants``, each a ``When`` (rooms,
  attachments, property classes) and the rule that applies there; the first variant that
  matches the component's normalized room type, attachment and property class is the
  effective rule, else the row itself. Cabinets and counters in a bathroom are restroom
  cabinetry (building), furniture in an office is 00.11 (7 years), and a removable or
  freestanding mirror is 57.0 decor; the hit names the variant in ``RuleHit.context``.
* An ``attachment`` of ``structural`` contradicts every section 1245 row and makes the
  lookup a miss (conflicting evidence goes to review).
* A few aliases for labels the Stage 2 vision stub and the synthetic fixtures emit
  (``cabinetry``, ``tile_flooring``, ``decorative_lighting``, ``ceiling_fans``);
  ``decorative_lighting`` and ``track_lighting`` resolve to ``accent_lighting`` and
  ``lamp`` to ``furniture`` since 2026-10-29.

Rows added 2026-10-30 (``RULES_VERSION`` ``2026-10-30.1``, STRUCTURE_MODEL.md): the thirteen
labels of the structure model's concealed rows (``foundation``, ``structural_framing``,
``exterior_wall_assembly``, ``roofing_assembly``, ``interior_partition``, ``painting``,
``plumbing_rough_in``, ``hvac_distribution``, ``electrical_wiring``, ``fire_alarm_system``,
``site_utilities``: building; ``data_cabling``: 57.0, 5 years; ``access_control``: building,
contested because studies treat it as 5-year), each cited to the ATG residential matrix row
that names it.

``property_class`` (``residential_rental`` or ``nonresidential``) is part of the lookup and
of the cache key because the building buckets differ (27.5 vs 39 years): only the building
rows (``Rule.building``, section 1250 components such as bathroom accessories or a concrete
floor) differ by class, 27.5 years residential and 39 nonresidential, which
``tests/unit/domain/test_rules_table.py`` pins.

Rows added 2026-10-16 (``RULES_VERSION`` ``2026-10-16.1``, EVAL_REPORT section 19) for the
report rows that had no vocabulary label: ``bathroom_accessory`` and ``concrete_floor``
(building, supported by ATG Exhibit A), ``half_wall`` and ``window_guard`` (building,
contested: studies treat them as 5-year), ``shed`` (section 1245,
7-year, contested: no passage fixes its period) and ``concrete_pad`` (15-year land
improvement, contested: no passage names equipment pads). Each new excerpt was checked as in
``RULES_RECITATION.md`` (verbatim on the page and inside one production chunk).

The citations are ``(doc_id, page, excerpt)`` claims re-cited on 2026-10-15 against the
reference PDFs (they replace the legacy claims, which cited ATG pages 412-420 that do not
exist and paraphrased the documents). ``page`` is the 1-based PDF page; every excerpt is a
verbatim passage of that page (normalized as :func:`basis.retrieval.verify.normalize_text`
does) supporting the row's section and recovery period, or, for a contested row, the
closest relevant passage. Each judgment is recorded in
``docs/review_2026-10-15/RULES_RECITATION.md`` and the eval dataset ``rule_citations``
(``evals/run.py``) re-checks every claim against the corpus. They are verified at
classification time against the reference corpus
(:func:`basis.classify.router.resolve_rule_citations`); a claim that does not resolve to a
chunk whose text contains the excerpt is dropped and the asset is flagged
``citation_unverified`` (S3_SPEC section 0).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal, get_args

from basis.db.enums import Bucket

# Bump when any row, alias or matching rule changes: it is part of the cache key, so a
# cached classification made under older rules is never served.
RULES_VERSION: Final = "2026-10-30.1"

PropertyClass = Literal["residential_rental", "nonresidential"]
PROPERTY_CLASSES: Final[tuple[str, ...]] = get_args(PropertyClass)
Section = Literal["1245", "1250"]
MatchKind = Literal["direct", "alias", "material"]

ATG: Final = "IRS_IRS_COST_SEG_ATG__2024"
REV_PROC: Final = "IRS_REV_PROC_87_56"
PUB_946: Final = "IRS_IRS_PUB_946__2024"

RECOVERY_YEARS: Final[dict[Bucket, Decimal]] = {
    Bucket.Y5: Decimal("5"),
    Bucket.Y7: Decimal("7"),
    Bucket.Y15: Decimal("15"),
    Bucket.Y27_5: Decimal("27.5"),
    Bucket.Y39: Decimal("39"),
}

# Confidence of a rule hit (legacy values: 0.98 direct, 0.95 alias).
DIRECT_CONFIDENCE: Final = 0.98
ALIAS_CONFIDENCE: Final = 0.95
MATERIAL_CONFIDENCE: Final = 0.9

# Contested-row notes. "Exhibit A" is the ATG's residential rental property matrix
# (Pub 5653 chapter 7.H, PDF pages 297-322), the IRS guidance for the table's default
# property class. Page numbers are PDF pages of the corpus documents.


def _unsupported(finding: str) -> str:
    return (
        "No supporting text was found in the reference documents for the legacy "
        f"treatment; {finding}; needs engineer confirmation"
    )


_KITCHEN_COMPONENT: Final = _unsupported(
    "ATG Exhibit A (p.311) lists kitchen cabinets and counters as Kitchen Components, "
    "section 1250, 27.5 years (the only section 1245 cabinet passage is a non-residential "
    "case, Metro National, ATG p.100, with no recovery period)"
)
_DOORBELL: Final = _unsupported(
    "no document mentions doorbells; ATG Exhibit A (p.301) puts building electrical branch "
    "circuits that provide general building services under section 1250, 27.5 years"
)
_LAMINATE: Final = _unsupported(
    "no document mentions laminate flooring; ATG Exhibit A (p.307) treats a floor covering "
    "as section 1245 only when it is readily removable (strippable adhesive), and lists "
    "permanently affixed floor coverings as section 1250 (p.306)"
)
_GLUED_WOOD_FLOOR: Final = (
    "The documents' position is adopted: ATG Exhibit A (p.306) lists wood flooring affixed "
    "with permanent adhesive or nailed or screwed in place under Floor Coverings - "
    "Permanent, section 1250, 27.5 years (39 nonresidential); studies commonly treat glued "
    "wood flooring as 5-year property, so the bucket needs engineer confirmation"
)
# Rows added 2026-10-16 for report rows that had no label (EVAL_REPORT sections 13 and 19).
# Where a cost segregation study's treatment (the report rows of section 13) and the documents
# disagree, the row follows the documents (or, where no passage fixes the period, the study)
# and is contested, so a hit goes to the engineer.
_HALF_WALL: Final = (
    "ATG Exhibit A (p.321) puts non-load bearing interior partitions 'regardless of height' "
    "under Walls - Interior, section 1250, 27.5 years (39 nonresidential), and only "
    "partitions that can be readily removed or are intended to be moved under section "
    "1245, 5 years; studies that put interior half walls in 5-year rely on that, which "
    "holds only for a removable partition, so a half wall needs engineer confirmation"
)
_WINDOW_GUARD: Final = (
    "No document names window guards or bars; ATG Exhibit A lists steel grills and gates "
    "under Doors (p.300) and window and door locks under Security Systems - Building "
    "(p.318), both section 1250, 27.5 years; studies also treat door and window guards as "
    "5-year property, so the bucket needs engineer confirmation"
)
_SHED: Final = (
    "Storage sheds are section 1245 in the ATG's case table (p.102), with no recovery "
    "period; the 7-year treatment is property with no class life (Pub 946 p.29), while "
    "ATG p.297 says asset class 57.0, 5 years, applies to most "
    "section 1245 property used with residential rental property, and a shed on a permanent "
    "foundation may be a building; needs engineer confirmation"
)
_CONCRETE_PAD: Final = (
    "No document names exterior equipment pads; ATG Exhibit A puts foundations for land "
    "improvements (p.299) and a trash enclosure's concrete pad (p.320) in asset class 00.3, "
    "15 years, but equipment foundations within the "
    "building footprint in section 1250 (p.299), and a pad under building HVAC equipment is "
    "not addressed; needs engineer confirmation"
)
_ACCESS_CONTROL: Final = (
    "The documents' position is adopted: ATG Exhibit A puts door locks and the hardware of "
    "computerized lock systems under Doors (p.300) and window and door locks, card key and "
    "keyless entry systems under Security Systems - Building (p.318), section 1250, 27.5 years "
    "(39 nonresidential); studies (the design partner's report among them) treat access "
    "control as 5-year property, so the bucket needs engineer confirmation"
)
_ATTACHED_DECK: Final = (
    "ATG Exhibit A (p.300) lists only freestanding decks as asset class 00.3, 15 years; a "
    "deck attached to the residence is section 1250, 27.5 years, so the bucket needs "
    "engineer confirmation"
)


@dataclass(frozen=True, slots=True)
class RuleCitation:
    doc_id: str
    page: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class Rule:
    key: str
    category: str
    section: Section
    bucket: Bucket
    asset_class: str | None
    irs_note: str
    citations: tuple[RuleCitation, ...]
    contested: str | None = None
    # A building component (section 1250, residential rental 27.5 / nonresidential 39 years):
    # ``bucket`` is the residential one and :func:`lookup` returns 39 years for a
    # nonresidential property (ATG Exhibit A gives both: "RRP - 27.5 Years NRRP - 39 Years").
    building: bool = False
    # The context dimension: the first variant whose ``When`` matches the component's room,
    # attachment and property class is the effective rule; otherwise this row is.
    variants: tuple[Variant, ...] = ()

    @property
    def recovery_period(self) -> Decimal:
        return RECOVERY_YEARS[self.bucket]

    def bucket_for(self, property_class: PropertyClass) -> Bucket:
        """The bucket for ``property_class``: only building rows differ (27.5 vs 39)."""
        if self.building and property_class == "nonresidential":
            return Bucket.Y39
        return self.bucket

    def effective(
        self, *, room: str, attachment: str, property_class: PropertyClass
    ) -> tuple[Rule, str | None]:
        """``(rule, context)``: the first matching variant's rule and its context name, or
        this row and ``None``. ``room`` and ``attachment`` are normalized already."""
        for variant in self.variants:
            if variant.when.matches(room, attachment, property_class):
                return variant.rule, variant.when.describe(room, attachment, property_class)
        return self, None


@dataclass(frozen=True, slots=True)
class When:
    """The context a variant applies in. Every non-empty set must contain the component's
    value: its normalized room type, its normalized attachment, its property class. An
    unknown value (``""``) never matches a non-empty set, so it falls to the base row."""

    rooms: frozenset[str] = frozenset()
    attachments: frozenset[str] = frozenset()
    property_classes: frozenset[str] = frozenset()

    def matches(self, room: str, attachment: str, property_class: str) -> bool:
        return (
            (not self.rooms or room in self.rooms)
            and (not self.attachments or attachment in self.attachments)
            and (not self.property_classes or property_class in self.property_classes)
        )

    def describe(self, room: str, attachment: str, property_class: str) -> str:
        """The context that fired, e.g. ``"room=bathroom"`` or ``"attachment=removable"``."""
        parts = []
        if self.rooms:
            parts.append(f"room={room}")
        if self.attachments:
            parts.append(f"attachment={attachment}")
        if self.property_classes:
            parts.append(f"property_class={property_class}")
        return ",".join(parts)


@dataclass(frozen=True, slots=True)
class Variant:
    """A row's treatment in a context (``when``): ``rule`` replaces the base row there.
    ``rule.key`` is the base row's key, so the hit, the cache key and the provenance keep
    naming the row."""

    when: When
    rule: Rule


@dataclass(frozen=True, slots=True)
class RuleHit:
    """A rules-table classification for one component."""

    key: str
    category: str
    section: Section
    bucket: Bucket
    recovery_period: Decimal
    asset_class: str | None
    irs_note: str
    citations: tuple[RuleCitation, ...]
    confidence: float
    matched: str  # the normalized label (or label+material) that matched
    via: MatchKind
    property_class: PropertyClass
    contested: str | None = None
    # The variant that fired (``"room=bathroom"``, ``"attachment=removable"``), ``None`` for
    # the base row; ``key`` is the base row's key either way.
    context: str | None = None

    @property
    def irs_section(self) -> str:
        """The value stored in ``assets.irs_section``."""
        return self.section


def _c(doc_id: str, page: int, excerpt: str) -> RuleCitation:
    return RuleCitation(doc_id, page, excerpt)


def _p5(
    key: str,
    category: str,
    note: str,
    *cites: RuleCitation,
    contested: str | None = None,
    variants: tuple[Variant, ...] = (),
) -> Rule:
    return Rule(key, category, "1245", Bucket.Y5, "57.0", note, cites, contested, False, variants)


def _p7(
    key: str, category: str, note: str, *cites: RuleCitation, contested: str | None = None
) -> Rule:
    return Rule(key, category, "1245", Bucket.Y7, "00.11", note, cites, contested)


def _l15(
    key: str, category: str, note: str, *cites: RuleCitation, contested: str | None = None
) -> Rule:
    return Rule(key, category, "1250", Bucket.Y15, "00.3", note, cites, contested)


def _bldg(
    key: str,
    category: str,
    note: str,
    *cites: RuleCitation,
    contested: str | None = None,
    variants: tuple[Variant, ...] = (),
) -> Rule:
    return Rule(key, category, "1250", Bucket.Y27_5, None, note, cites, contested, True, variants)


def _when(
    *,
    rooms: tuple[str, ...] = (),
    attachments: tuple[str, ...] = (),
    property_classes: tuple[str, ...] = (),
) -> When:
    return When(frozenset(rooms), frozenset(attachments), frozenset(property_classes))


# Citations shared by several rows (each verified verbatim on its page).
_RRP_57_0: Final = _c(
    ATG,
    297,
    "Asset Class 57.0 - Distributive Trades and Services applies to most of the § 1245 "
    "property used with RRP, which is recovered over 5 years.",
)
_KITCHEN_APPLIANCES: Final = _c(
    ATG, 311, "Kitchen - Stove/Oven, Refrigerator, Icemaker, Microwave, Dishwasher, etc."
)
_LAUNDRY_APPLIANCES: Final = _c(
    ATG, 304, "appliances qualifying as § 1245 property (washer/dryer, refrigerator, range/ovens)"
)
_REMOVABLE_FLOORING: Final = _c(
    ATG,
    307,
    "All vinyl composition tile (VCT), sheet vinyl, and carpeting will be treated as not "
    "permanently attached and not intended to be permanent.",
)
_WINDOW_TREATMENTS: Final = _c(
    ATG,
    322,
    "Window treatments such as drapes, curtains, louver, blinds, post construction tinting "
    "and interior decorative theme décor which are readily removable.",
)
_KITCHEN_COMPONENTS: Final = _c(
    ATG, 311, "Kitchen cabinets, counters, sink, includes plumbing costs."
)
_BATHROOM_PLUMBING: Final = _c(
    ATG, 315, "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)"
)
_RESTAURANT_DECOR: Final = _c(
    ATG, 191, "Decorative mobile props such as playground equipment, potted plants, hanging mirrors"
)
_OFFICE_FURNITURE_00_11: Final = _c(
    REV_PROC, 4, "Includes such assets as desks, files, safes, and communications equipment"
)
_RENTAL_OFFICE_FURNITURE: Final = _c(
    ATG,
    307,
    "Includes desk, chair, credenza, file cabinet, table, or other furniture such as workstations.",
)
_LAND_IMPROVEMENTS_00_3: Final = _c(REV_PROC, 7, "bridges, fences, landscaping, shubbery")
# Cited for the context dimension (re-citation of 2026-10-29, RULES_RECITATION.md).
_RESTROOM_CABINETRY: Final = _c(ATG, 298, "Restroom cabinetry, counters, sinks.")
_RESTROOM_COMPONENTS: Final = _c(
    ATG,
    317,
    "Cabinet, counter, sink, faucet, mirror attached with permanent adhesive, toilet, towel "
    "rack, toilet paper holder, flush mounted medicine cabinet, tub, shower, vent fan, etc.",
)
_KITCHEN_PLUMBING: Final = _c(
    ATG,
    311,
    "All piping, drains, faucets, garbage disposal and waste piping, sinks and other "
    "components of a kitchen plumbing system",
)
_ACCENT_LIGHTING: Final = _c(
    ATG,
    313,
    "Light fixtures, such as neon, track lighting, or grow lights which are decorative in "
    "nature and not necessary for the operation or maintenance of the building.",
)
_RESIDENTIAL_FURNISHINGS: Final = _c(
    ATG,
    308,
    "Includes all furniture and furnishings typically found in a furnished house, condo, "
    "apartment, etc.",
)
_RESIDENTIAL_FURNISHINGS_EXAMPLES: Final = _c(
    ATG, 308, "For example, beds, chairs, sofas, and tables."
)

# Variants shared by several rows.
_EXTERIOR_WALL_ELEMENTS: Final = _c(
    ATG,
    321,
    "posts, beams, girders, curtain walls, tilt up panels, studs, framing, sheetrock, "
    "insulation, windows, doors, exterior façade, brick, masonry, etc.",
)
_ROOF_ELEMENTS: Final = _c(
    ATG,
    317,
    "including but not limited to joists, rafters, deck, shingles, vapor barrier, skylights, "
    "trusses, girders, and gutters.",
)
_IN_BATHROOM: Final = _when(rooms=("bathroom",))
_IN_OFFICE: Final = _when(rooms=("office",))


def _office_furniture(key: str, category: str, note: str) -> Variant:
    """Furniture serving a rental office: asset class 00.11, 7 years (ATG p.307)."""
    return Variant(
        _IN_OFFICE,
        _p7(key, category, note, _RENTAL_OFFICE_FURNITURE, _OFFICE_FURNITURE_00_11),
    )


def _restroom_cabinetry(key: str, category: str, note: str) -> Variant:
    """Cabinetry and counters in a bathroom: a restroom component (ATG pp.298, 317)."""
    return Variant(
        _IN_BATHROOM, _bldg(key, category, note, _RESTROOM_CABINETRY, _RESTROOM_COMPONENTS)
    )


_RULES: Final[tuple[Rule, ...]] = (
    # ---------------------------------------------------- section 1245, 5-year (57.0)
    _p5(
        "carpet",
        "Carpet",
        "Carpeting in residential rental property is Section 1245 personal property with "
        "5-year recovery per Rev Proc 87-56 Asset Class 57.0",
        _REMOVABLE_FLOORING,
        _RRP_57_0,
    ),
    _p5(
        "accent_lighting",
        "Accent lighting",
        "Accent, decorative or special light fixtures not needed to light the building (track, "
        "neon, landscape lighting) are Section 1245, Asset Class 57.0, 5-year; decorative "
        "lighting that is the primary light source is a building component (light_fixture)",
        _ACCENT_LIGHTING,
        _c(ATG, 313, "Lighting that highlights only the landscaping or building exterior"),
        _RRP_57_0,
    ),
    _p5(
        "kitchen_cabinet",
        "Kitchen cabinets",
        "Kitchen cabinets in residential rental are Section 1245 personal property",
        _KITCHEN_COMPONENTS,
        _c(ATG, 100, "Cabinets and hardware (§ 1245)"),
        contested=_KITCHEN_COMPONENT,
        variants=(
            _restroom_cabinetry(
                "kitchen_cabinet",
                "Restroom cabinetry",
                "Cabinets in a bathroom are restroom cabinetry, Section 1250 building "
                "components (ATG Exhibit A, Cabinetry - Restroom and Restroom Components)",
            ),
        ),
    ),
    _p5(
        "countertop",
        "Countertops",
        "Countertops (non-structural) are Section 1245 personal property",
        _KITCHEN_COMPONENTS,
        contested=_KITCHEN_COMPONENT,
        variants=(
            _restroom_cabinetry(
                "countertop",
                "Restroom counters",
                "Counters in a bathroom are restroom components, Section 1250 building "
                "components (ATG Exhibit A, Cabinetry - Restroom and Restroom Components)",
            ),
        ),
    ),
    _p5(
        "appliance",
        "Appliances",
        "Kitchen appliances are Section 1245 personal property",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "refrigerator",
        "Refrigerator",
        "Refrigerators are Section 1245 personal property with 5-year recovery",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "dishwasher",
        "Dishwasher",
        "Dishwashers are Section 1245 personal property with 5-year recovery",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "stove",
        "Stove / range",
        "Stoves/ranges are Section 1245 personal property with 5-year recovery",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "oven",
        "Oven",
        "Ovens are Section 1245 personal property with 5-year recovery",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "microwave",
        "Microwave",
        "Microwaves are Section 1245 personal property with 5-year recovery",
        _KITCHEN_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "washer",
        "Clothes washer",
        "Washing machines are Section 1245 personal property with 5-year recovery",
        _LAUNDRY_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "dryer",
        "Clothes dryer",
        "Dryers are Section 1245 personal property with 5-year recovery",
        _LAUNDRY_APPLIANCES,
        _RRP_57_0,
    ),
    _p5(
        "blinds",
        "Window blinds",
        "Window blinds are Section 1245 personal property with 5-year recovery",
        _WINDOW_TREATMENTS,
    ),
    _p5(
        "window_treatment",
        "Window treatments",
        "Window treatments are Section 1245 personal property with 5-year recovery",
        _WINDOW_TREATMENTS,
    ),
    _p5(
        "doorbell",
        "Doorbell",
        "Doorbells are Section 1245 personal property with 5-year recovery",
        _c(
            ATG,
            301,
            "used in the operation or maintenance of the building or to provide general "
            "building services",
        ),
        contested=_DOORBELL,
    ),
    _p5(
        "vinyl_flooring",
        "Vinyl flooring",
        "Vinyl flooring in residential is Section 1245 with 5-year recovery",
        _REMOVABLE_FLOORING,
    ),
    _p5(
        "laminate_flooring",
        "Laminate flooring",
        "Laminate flooring in residential is Section 1245 with 5-year recovery",
        _c(ATG, 307, "Floor covering that is installed by means of strippable adhesives"),
        contested=_LAMINATE,
    ),
    # ------------------ section 1245, 5-year (57.0); 7-year (00.11) in a rental office
    _p5(
        "furniture",
        "Furniture",
        "Residential furnishings (beds, chairs, sofas, tables) are Section 1245 personal "
        "property, Asset Class 57.0, 5-year; furniture serving a rental office is Asset Class "
        "00.11, 7-year",
        _RESIDENTIAL_FURNISHINGS,
        _RESIDENTIAL_FURNISHINGS_EXAMPLES,
        _RRP_57_0,
        variants=(
            _office_furniture(
                "furniture",
                "Office furniture",
                "Office furniture and fixtures are 7-year property under Asset Class 00.11",
            ),
        ),
    ),
    _p5(
        "desk",
        "Desk",
        "A desk furnishing a dwelling is a residential furnishing, Section 1245, Asset Class "
        "57.0, 5-year; an office desk is Asset Class 00.11, 7-year",
        _RESIDENTIAL_FURNISHINGS,
        _RRP_57_0,
        variants=(
            _office_furniture(
                "desk", "Office desk", "Desks are 7-year property under Asset Class 00.11"
            ),
        ),
    ),
    _p5(
        "chair",
        "Chair",
        "Chairs are residential furnishings, Section 1245, Asset Class 57.0, 5-year; an "
        "office chair is Asset Class 00.11, 7-year",
        _RESIDENTIAL_FURNISHINGS_EXAMPLES,
        _RRP_57_0,
        variants=(
            _office_furniture(
                "chair",
                "Office chair",
                "Chairs (office) are 7-year property under Asset Class 00.11",
            ),
        ),
    ),
    _p5(
        "filing_cabinet",
        "Filing cabinet",
        "A filing cabinet furnishing a dwelling is a residential furnishing, Section 1245, "
        "Asset Class 57.0, 5-year; an office one is Asset Class 00.11, 7-year",
        _RESIDENTIAL_FURNISHINGS,
        _RRP_57_0,
        variants=(
            _office_furniture(
                "filing_cabinet",
                "Office filing cabinet",
                "Filing cabinets are 7-year property under Asset Class 00.11",
            ),
        ),
    ),
    # ---------------------------------------------------- land improvements, 15-year (00.3)
    _l15(
        "parking_lot",
        "Parking lot",
        "Parking lots are Section 1250 land improvements with 15-year recovery",
        _c(
            ATG,
            314,
            "Grade level surface parking area usually constructed of asphalt, brick, concrete, "
            "stone, or similar material.",
        ),
    ),
    _l15(
        "sidewalk",
        "Sidewalks",
        "Sidewalks are Section 1250 land improvements with 15-year recovery",
        _c(
            REV_PROC,
            7,
            "Examples of such assets might include sidewalks, roads, canals, waterways, "
            "drainage facilities",
        ),
        _c(
            ATG,
            318,
            "Sidewalks and curbs are usually constructed of concrete, asphalt, stone, or "
            "similar material.",
        ),
    ),
    _l15(
        "landscaping",
        "Landscaping",
        "Landscaping is a Section 1250 land improvement with 15-year recovery",
        _LAND_IMPROVEMENTS_00_3,
        _c(
            ATG,
            312,
            "The costs of landscaping that would be destroyed upon the replacement of the "
            "depreciable asset, such as a building, are depreciable land improvements.",
        ),
    ),
    _l15(
        "fence",
        "Fencing",
        "Fences are Section 1250 land improvements with 15-year recovery",
        _LAND_IMPROVEMENTS_00_3,
        _c(ATG, 305, "Examples include fences; gates, canals; waterways; drainage facilities"),
    ),
    _l15(
        "retaining_wall",
        "Retaining wall",
        "Retaining walls are Section 1250 land improvements with 15-year recovery",
        _c(ATG, 305, "sewers (not including municipal sewers in Class 51); retaining walls"),
    ),
    _l15(
        "outdoor_lighting",
        "Site lighting",
        "Site lighting is a Section 1250 land improvement with 15-year recovery",
        _c(
            ATG,
            313,
            "Pole-mounted or freestanding outdoor lighting system to illuminate sidewalks, "
            "parking, or recreation areas.",
        ),
    ),
    _l15(
        "driveway",
        "Driveway",
        "Driveways are Section 1250 land improvements with 15-year recovery",
        _c(
            ATG,
            317,
            "Grade level driveways, roads, and base areas usually constructed of asphalt, "
            "brick, concrete, stone, or similar material.",
        ),
    ),
    _l15(
        "signage",
        "Exterior signage",
        "Exterior signage is a Section 1250 land improvement with 15-year recovery",
        _c(ATG, 319, "Includes exterior signs used to direct traffic and parking."),
    ),
    _l15(
        "pool",
        "Swimming pool",
        "Swimming pools are Section 1250 land improvements with 15-year recovery",
        _c(ATG, 298, "outdoor, swimming pools/spas and associated equipment"),
    ),
    _l15(
        "patio",
        "Patio",
        "Patios (exterior) are Section 1250 land improvements with 15-year recovery",
        _c(ATG, 314, "A ground level, paved outdoor area adjoining a residence or other building"),
    ),
    _l15(
        "deck",
        "Deck",
        "Decks (exterior) are Section 1250 land improvements with 15-year recovery",
        _c(ATG, 300, "Inherently permanent decks and gazebos not attached to buildings."),
        contested=_ATTACHED_DECK,
    ),
    _l15(
        "sprinkler_system",
        "Irrigation system",
        "Irrigation/sprinkler systems are Section 1250 land improvements",
        _c(ATG, 305, "holding, settling, or detention ponds; and irrigation systems"),
    ),
    _l15(
        "concrete_pad",
        "Exterior equipment concrete pad",
        "Exterior concrete pads are Section 1250 land improvements, 15-year (00.3)",
        _c(
            ATG,
            299,
            "Foundations and footings for signs, light poles, and other land improvements "
            "(except buildings).",
        ),
        _c(
            ATG,
            320,
            "Enclosures for waste receptacles, typically constructed on a concrete pad with its "
            "posts set in the concrete.",
        ),
        contested=_CONCRETE_PAD,
    ),
    # ------------------------------------------- section 1245, no class life, 7-year
    Rule(
        "shed",
        "Movable shed",
        "1245",
        Bucket.Y7,
        None,
        "A movable storage shed is Section 1245 property; with no class life it is 7-year",
        (
            _c(ATG, 102, "Storage sheds (§ 1245)"),
            _c(PUB_946, 29, "does not have a class life and has not been designated by law"),
        ),
        _SHED,
    ),
    # ------------------------- building components, section 1250, 27.5 / 39 years
    _bldg(
        "bathroom_accessory",
        "Bathroom accessories",
        "Restroom accessories (towel bars, paper holders, grab bars) are Section 1250 building "
        "components",
        _c(
            ATG,
            316,
            "Also, includes paper towel dispensers, electric hand dryers, towel racks or "
            "holders, cup dispensers, purse shelves, toilet paper holders",
        ),
        _RESTROOM_COMPONENTS,
    ),
    _bldg(
        "concrete_floor",
        "Concrete floor (sealed)",
        "Concrete floors, with treatments made a permanent part of them, are Section 1250 "
        "building components",
        _c(ATG, 307, "Includes concrete slabs and other floor systems."),
        _c(
            ATG,
            307,
            "Floors include special treatments applied to, or otherwise made a permanent part, "
            "of the floor.",
        ),
    ),
    _bldg(
        "half_wall",
        "Interior half wall",
        "Non-load bearing interior partitions of any height are Section 1250 building components",
        _c(
            ATG,
            321,
            "partitions regardless of height (typically constructed of studs and sheetrock or "
            "other materials) that divide or create rooms or provide traffic control.",
        ),
        _c(
            ATG,
            321,
            "Interior walls where the partition can be 1) readily removed and remain in "
            "substantially the same condition after removal as before",
        ),
        contested=_HALF_WALL,
    ),
    _bldg(
        "window_guard",
        "Door and window guards",
        "Security grilles and guards on doors and windows are Section 1250 building components",
        _c(ATG, 300, "roll-up or sliding wire mesh or steel grills and gates"),
        _c(
            ATG,
            318,
            "Examples include window and door locks; card key access systems; keyless entry "
            "systems",
        ),
        contested=_WINDOW_GUARD,
    ),
    # ------------------------------------------- named by ATG Exhibit A (2026-10-29)
    _bldg(
        "toilet",
        "Toilet",
        "Toilets are bathroom plumbing fixtures, Section 1250 building components (ATG Exhibit "
        "A, Plumbing - Building and Restroom Components)",
        _BATHROOM_PLUMBING,
        _RESTROOM_COMPONENTS,
    ),
    _bldg(
        "bathtub",
        "Bathtub",
        "Bathtubs are bathroom plumbing fixtures, Section 1250 building components (ATG "
        "Exhibit A, Plumbing - Building and Restroom Components)",
        _BATHROOM_PLUMBING,
        _RESTROOM_COMPONENTS,
    ),
    _bldg(
        "shower",
        "Shower",
        "Showers are bathroom plumbing fixtures, Section 1250 building components (ATG "
        "Exhibit A, Plumbing - Building and Restroom Components)",
        _BATHROOM_PLUMBING,
        _RESTROOM_COMPONENTS,
    ),
    _bldg(
        "sink",
        "Sink",
        "Bathroom and kitchen sinks are plumbing fixtures, Section 1250 building components "
        "(ATG Exhibit A, Plumbing - Building and Kitchen Components)",
        _BATHROOM_PLUMBING,
        _KITCHEN_COMPONENTS,
    ),
    _bldg(
        "water_heater",
        "Water heater",
        "Water heaters are components of the building plumbing system, Section 1250 building "
        "components (ATG Exhibit A, Plumbing - Building)",
        _c(
            ATG,
            315,
            "all other components of a building plumbing system (water or gas) not "
            "specifically identified elsewhere",
        ),
    ),
    _bldg(
        "garbage_disposal",
        "Garbage disposal",
        "Garbage disposals are part of the kitchen plumbing and general kitchen wiring, "
        "Section 1250 building components (ATG Exhibit A, Kitchen - Plumbing and Kitchen - "
        "Electrical)",
        _KITCHEN_PLUMBING,
        _c(ATG, 311, "All general wiring/outlets/switches in kitchen including garbage disposal."),
    ),
    _bldg(
        "light_fixture",
        "Light fixtures",
        "Light fixtures that provide the building's artificial illumination, decorative or "
        "not, are Section 1250 building components (ATG Exhibit A, Light Fixtures - Interior - "
        "Building); accent fixtures are accent_lighting",
        _c(
            ATG,
            313,
            "Includes lighting such as recessed and lay-in lighting, night lighting, as well as "
            "decorative lighting fixtures that provide substantially all the artificial "
            "illumination in the building.",
        ),
    ),
    _bldg(
        "ceiling_fan",
        "Ceiling fan",
        "Ceiling fans, typically with the room's primary light, are Section 1250 building "
        "components (ATG Exhibit A, Ceiling Fans)",
        _c(ATG, 298, "Ceiling fan typically with attached lights."),
        _c(
            ATG,
            298,
            "Lighting typically is the primary light source (artificial illumination) in the room.",
        ),
    ),
    _bldg(
        "smoke_detector",
        "Smoke detector",
        "Fire alarms and CO2 detectors are Section 1250 building components (ATG Exhibit A, "
        "Fire Alarm and CO2 Detectors)",
        _c(
            ATG,
            305,
            "Battery powered or hard-wired fire alarms and CO2 detectors, generally mounted to "
            "ceilings or walls by screws.",
        ),
    ),
    _bldg(
        "thermostat",
        "Thermostat",
        "Thermostats are part of the energy management and HVAC systems, Section 1250 "
        "building components (ATG Exhibit A, Energy Management Systems and HVAC - Residence)",
        _c(ATG, 304, "demand controllers, thermostats, computer controls, outside air economizers"),
        _c(
            ATG,
            310,
            "Includes all components of a central heating, ventilating and air conditioning "
            "system.",
        ),
    ),
    _bldg(
        "tile",
        "Tile",
        "Tile cemented, mudded or grouted in place is a permanent floor covering, Section 1250 "
        "building component (ATG Exhibit A, Floor Coverings - Permanent)",
        _c(
            ATG,
            306,
            "ceramic or quarry tile, marble, paving brick, and other coverings cemented, "
            "mudded, or grouted to the floor",
        ),
    ),
    _bldg(
        "hardwood_floor",
        "Hardwood flooring",
        "Wood flooring affixed with permanent adhesive or nailed or screwed in place is a "
        "permanent floor covering, Section 1250 building component (ATG Exhibit A, Floor "
        "Coverings - Permanent)",
        _c(
            ATG,
            306,
            "Includes floor covering that is affixed with permanent adhesive or nailed or "
            "screwed in place.",
        ),
        _c(ATG, 306, "epoxy or sealers; and wood flooring."),
        contested=_GLUED_WOOD_FLOOR,
    ),
    _bldg(
        "bathroom_vanity",
        "Bathroom vanity",
        "Bathroom vanities are restroom cabinetry, counters and sinks, Section 1250 building "
        "components (ATG Exhibit A, Cabinetry - Restroom and Restroom Components)",
        _RESTROOM_CABINETRY,
        _RESTROOM_COMPONENTS,
    ),
    _bldg(
        "closet_shelving",
        "Closet shelving",
        "Built-in shelving and rods in closets are finish carpentry, Section 1250 building "
        "components (ATG Exhibit A, Finish Carpentry); a freestanding shelf unit is a "
        "furnishing, Asset Class 57.0, 5-year",
        _c(
            ATG,
            305,
            "Built-in bookshelves, shelving and rods in bedroom, hallway, and linen closets, "
            "pantries, etc.",
        ),
        variants=(
            Variant(
                _when(attachments=("freestanding",)),
                _p5(
                    "closet_shelving",
                    "Shelving (freestanding)",
                    "A freestanding shelf unit is a residential furnishing, Section 1245 "
                    "personal property, Asset Class 57.0, 5-year",
                    _RESIDENTIAL_FURNISHINGS,
                    _RRP_57_0,
                ),
            ),
        ),
    ),
    _bldg(
        "mirror",
        "Mirror",
        "A mirror attached with permanent adhesive is a restroom component, Section 1250 "
        "building component (ATG Exhibit A, Restroom Components); a removable or "
        "freestanding mirror is decor, Asset Class 57.0, 5-year",
        _c(ATG, 317, "mirror attached with permanent adhesive"),
        variants=(
            Variant(
                _when(attachments=("removable", "freestanding")),
                _p5(
                    "mirror",
                    "Mirror (removable decor)",
                    "A hanging or freestanding mirror is readily removable decor, Section 1245 "
                    "personal property, Asset Class 57.0, 5-year",
                    _RESTAURANT_DECOR,
                    _WINDOW_TREATMENTS,
                    _RRP_57_0,
                ),
            ),
        ),
    ),
    # ------------------------------------------- the structure model's concealed rows
    # (RULES_VERSION 2026-10-30.1, docs/review_2026-10-15/STRUCTURE_MODEL.md sections 3.5
    # and 3.6): the building systems photographs cannot show, each where ATG Exhibit A
    # names it. Every excerpt is verbatim on its page as pdfplumber reads it.
    _bldg(
        "foundation",
        "Foundations",
        "Footings and foundations necessary for the construction of the building, with their "
        "excavation and backfill, are Section 1250 building components (ATG Exhibit A, Concrete "
        "Footings and Foundations - Building)",
        _c(
            ATG,
            299,
            "Includes excavation and backfill for building footings and foundations, but does "
            "not include site grading costs.",
        ),
        _c(
            ATG, 299, "formwork, reinforcement, concrete block, and pre-cast or cast-in-place work."
        ),
    ),
    _bldg(
        "structural_framing",
        "Structural framing",
        "The exterior wall, floor and roof framing (studs, posts, beams, girders, joists, "
        "rafters, trusses) is Section 1250 building structure (ATG Exhibit A, Walls - Exterior "
        "and Roof)",
        _EXTERIOR_WALL_ELEMENTS,
        _ROOF_ELEMENTS,
    ),
    _bldg(
        "exterior_wall_assembly",
        "Exterior wall assembly",
        "Sheathing, housewrap and insulation of the exterior walls are Section 1250 building "
        "components (ATG Exhibit A, Walls - Exterior)",
        _EXTERIOR_WALL_ELEMENTS,
    ),
    _bldg(
        "roofing_assembly",
        "Roofing assembly",
        "Roof deck, underlayment, vapor barrier and flashings are Section 1250 building "
        "components (ATG Exhibit A, Roof)",
        _ROOF_ELEMENTS,
    ),
    _bldg(
        "interior_partition",
        "Interior framing and partitions",
        "Load bearing and non-load bearing interior partitions with their rough carpentry and "
        "gypsum board are Section 1250 building components (ATG Exhibit A, Walls - Interior)",
        _c(
            ATG,
            321,
            "load bearing partitions regardless of height (typically constructed of studs and "
            "sheetrock or other materials) that divide or create rooms or provide traffic "
            "control.",
        ),
        _c(
            ATG,
            321,
            "Includes rough carpentry and plaster, dry wall or gypsum board, and other finishes.",
        ),
    ),
    _bldg(
        "painting",
        "Painting",
        "Interior and exterior paint is a permanent wall covering, Section 1250 (ATG Exhibit A, "
        "Wall Coverings - Permanent)",
        _c(ATG, 320, "paint; ceramic or quarry tile, marble, stone, brick, and other"),
    ),
    _bldg(
        "plumbing_rough_in",
        "Plumbing rough-in",
        "Supply, drain, waste and vent piping are components of the building plumbing system, "
        "Section 1250 (ATG Exhibit A, Plumbing - Building)",
        _c(
            ATG,
            315,
            "all other components of a building plumbing system (water or gas) not "
            "specifically identified elsewhere",
        ),
    ),
    _bldg(
        "hvac_distribution",
        "HVAC distribution",
        "The heating plant and its ductwork or piping are components of the central HVAC "
        "system, Section 1250 (ATG Exhibit A, HVAC - Residence)",
        _c(
            ATG,
            310,
            "air conditioning system. HVAC systems that are primarily installed for the "
            "temperature, humidity, and ventilation of the residence and its occupants are "
            "building components.",
        ),
    ),
    _bldg(
        "electrical_wiring",
        "Electrical wiring and service",
        "The service entrance, panels, feeder and branch circuit wiring of general "
        "applicability are Section 1250 (ATG Exhibit A, Electrical Branch Circuits - Building; "
        "Electrical - Primary and Secondary EDS - Building)",
        _c(
            ATG,
            301,
            "Branch circuits are the electrical connections between a distribution or subpanel "
            "and the end-use equipment. This includes wire and conduit, junction boxes, wall "
            "switches, and outlets.",
        ),
        _c(
            ATG,
            303,
            "May include feeder circuits, power service entrance equipment, transformers, and "
            "conduit.",
        ),
    ),
    _bldg(
        "fire_alarm_system",
        "Fire protection and alarm system",
        "Smoke and heat detection devices, alarms and their control panels are Section 1250 "
        "(ATG Exhibit A, Fire Protection and Alarm Systems)",
        _c(
            ATG,
            305,
            "computer controls, sprinkler heads, piping or plumbing, pumps, visual and audible "
            "alarms, alarm control panels, heat and smoke detection devices, fire escapes, "
            "fire doors",
        ),
    ),
    _p5(
        "data_cabling",
        "Communication and data cabling",
        "Telephone, internet, cable and Wi-Fi cabling that serves equipment rather than the "
        "building is Section 1245 personal property, Asset Class 57.0, 5-year (ATG Exhibit A, "
        "Data Cable and Wiring)",
        _c(ATG, 300, "internet, cable TV, satellite TV,"),
        _RRP_57_0,
    ),
    _bldg(
        "access_control",
        "Access control (door locks)",
        "Door locks and keyed or keyless entry hardware are door hardware and building security "
        "equipment, Section 1250 (ATG Exhibit A, Doors; Security Systems - Building); studies "
        "commonly treat access control as 5-year",
        _c(
            ATG,
            300,
            "Includes computerized door locks, encoders, computers, and other associated "
            "hardware of the computerized lock system.",
        ),
        _c(
            ATG,
            318,
            "Examples include window and door locks; card key access systems; keyless entry "
            "systems;",
        ),
        contested=_ACCESS_CONTROL,
    ),
    _bldg(
        "site_utilities",
        "Site utilities",
        "Water, sanitary sewer, gas and electrical services from the property line to the "
        "building are Section 1250 (ATG Exhibit A, Site Utilities)",
        _c(
            ATG,
            319,
            "are used to distribute utility services from the property line to the building. "
            "Includes water, sanitary sewer, gas, and electrical services.",
        ),
    ),
)

RULES: Final[dict[str, Rule]] = {rule.key: rule for rule in _RULES}

ALIASES: Final[dict[str, str]] = {
    # carpet
    "carpeting": "carpet",
    "floor_carpet": "carpet",
    "wall_to_wall_carpet": "carpet",
    "area_rug": "carpet",
    # light fixtures
    "lighting": "light_fixture",
    "light": "light_fixture",
    "ceiling_light": "light_fixture",
    "pendant_light": "light_fixture",
    "recessed_light": "light_fixture",
    "chandelier": "light_fixture",
    "sconce": "light_fixture",
    # accent lighting (ATG p.313 Light Fixtures - Accent/Decorative/Special)
    "track_lighting": "accent_lighting",
    "decorative_lighting": "accent_lighting",
    "neon_lighting": "accent_lighting",
    "landscape_lighting": "accent_lighting",
    # cabinets
    "cabinet": "kitchen_cabinet",
    "kitchen_cabinets": "kitchen_cabinet",
    "base_cabinet": "kitchen_cabinet",
    "wall_cabinet": "kitchen_cabinet",
    "upper_cabinet": "kitchen_cabinet",
    "cabinetry": "kitchen_cabinet",
    # countertops
    "counter": "countertop",
    "counter_top": "countertop",
    "kitchen_counter": "countertop",
    "granite_countertop": "countertop",
    "quartz_countertop": "countertop",
    "marble_countertop": "countertop",
    "laminate_counter": "countertop",
    # appliances
    "range": "stove",
    "cooking_range": "stove",
    "gas_stove": "stove",
    "electric_stove": "stove",
    "range_hood": "appliance",
    "hood_vent": "appliance",
    "kitchen_appliance": "appliance",
    "washing_machine": "washer",
    "clothes_dryer": "dryer",
    # flooring (a generic floor label is resolved by material, see MATERIAL_RULES)
    "wood_floor": "hardwood_floor",
    "hardwood": "hardwood_floor",
    "vinyl": "vinyl_flooring",
    "laminate": "laminate_flooring",
    "floor_tile": "tile",
    "ceramic_tile": "tile",
    "porcelain_tile": "tile",
    "tile_flooring": "tile",
    # bathroom
    "vanity": "bathroom_vanity",
    "bath_vanity": "bathroom_vanity",
    "tub": "bathtub",
    "bath": "bathtub",
    "commode": "toilet",
    "shower_stall": "shower",
    "shower_enclosure": "shower",
    "bathroom_mirror": "mirror",
    # window treatments
    "curtains": "window_treatment",
    "drapes": "window_treatment",
    "shades": "blinds",
    "window_blinds": "blinds",
    "shutters": "blinds",
    "window_covering": "window_treatment",
    # fans
    "fan": "ceiling_fan",
    "overhead_fan": "ceiling_fan",
    # safety
    "fire_alarm": "smoke_detector",
    "fire_detector": "smoke_detector",
    "carbon_monoxide_detector": "smoke_detector",
    "co_detector": "smoke_detector",
    # land improvements
    "parking": "parking_lot",
    "parking_area": "parking_lot",
    "asphalt": "parking_lot",
    "concrete_parking": "parking_lot",
    "walkway": "sidewalk",
    "path": "sidewalk",
    "footpath": "sidewalk",
    "yard": "landscaping",
    "lawn": "landscaping",
    "garden": "landscaping",
    "plants": "landscaping",
    "trees": "landscaping",
    "shrubs": "landscaping",
    "fencing": "fence",
    "privacy_fence": "fence",
    "chain_link_fence": "fence",
    "gate": "fence",
    "exterior_lighting": "outdoor_lighting",
    "parking_lot_lighting": "outdoor_lighting",
    "site_lighting": "outdoor_lighting",
    "swimming_pool": "pool",
    "inground_pool": "pool",
    "above_ground_pool": "pool",
    "hot_tub": "pool",
    "spa": "pool",
    "outdoor_deck": "deck",
    "wood_deck": "deck",
    "composite_deck": "deck",
    "irrigation": "sprinkler_system",
    "sprinklers": "sprinkler_system",
    "drip_system": "sprinkler_system",
    # furniture
    "office_furniture": "furniture",
    "table": "furniture",
    "sofa": "furniture",
    "couch": "furniture",
    "bed": "furniture",
    "office_chair": "chair",
    "lamp": "furniture",  # a lamp is a furnishing (ATG p.308), not a light fixture
    "conference_table": "furniture",
    "bookcase": "furniture",
    "shelf": "closet_shelving",
    "shelves": "closet_shelving",
    "storage_shelf": "closet_shelving",
    # labels added 2026-10-16 (EVAL_REPORT section 19)
    "bath_accessory": "bathroom_accessory",
    "restroom_accessory": "bathroom_accessory",
    "towel_bar": "bathroom_accessory",
    "towel_ring": "bathroom_accessory",
    "toilet_paper_holder": "bathroom_accessory",
    "grab_bar": "bathroom_accessory",
    "shower_curtain_rod": "bathroom_accessory",
    "storage_shed": "shed",
    "garden_shed": "shed",
    "equipment_pad": "concrete_pad",
    "condenser_pad": "concrete_pad",
    "sealed_concrete_floor": "concrete_floor",
    "garage_floor": "concrete_floor",
    "pony_wall": "half_wall",
    "knee_wall": "half_wall",
    "window_bars": "window_guard",
    "security_bars": "window_guard",
    "security_grille": "window_guard",
    "door_guard": "window_guard",
}

# Generic labels that say "this is a floor covering" without saying which one.
GENERIC_FLOOR_LABELS: Final = frozenset({"flooring", "floor", "floor_covering", "floors"})
MATERIAL_RULES: Final[dict[str, str]] = {
    "carpet": "carpet",
    "carpeting": "carpet",
    "vinyl": "vinyl_flooring",
    "luxury_vinyl": "vinyl_flooring",
    "lvp": "vinyl_flooring",
    "laminate": "laminate_flooring",
    "tile": "tile",
    "ceramic": "tile",
    "ceramic_tile": "tile",
    "porcelain": "tile",
    "hardwood": "hardwood_floor",
    "wood": "hardwood_floor",
    "concrete": "concrete_floor",
    "sealed_concrete": "concrete_floor",
}

# An attachment that says the component is part of the building's structure; it
# contradicts every section 1245 row.
STRUCTURAL_ATTACHMENTS: Final = frozenset({"structural"})

_SEPARATORS: Final = re.compile(r"[\s\-/]+")
_NON_WORD: Final = re.compile(r"[^a-z0-9_.]")
_REPEATED: Final = re.compile(r"_+")


def normalize_label(value: str | None) -> str:
    """``"  Kitchen-Cabinet "`` -> ``"kitchen_cabinet"``; ``None`` -> ``""``.

    Lowercase, NFKC, spaces/hyphens/slashes to ``_``, other punctuation dropped, repeated
    underscores collapsed. The same normalization builds the classification cache key.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).lower().strip()
    text = _SEPARATORS.sub("_", text)
    text = _NON_WORD.sub("", text)
    return _REPEATED.sub("_", text).strip("_")


def canonical_key(label: str) -> tuple[str, MatchKind] | None:
    """The rule key a normalized label resolves to, directly or through an alias.

    A label that does not match is retried without a trailing ``s`` (``ceiling_fans``).
    """
    for candidate in (label, label[:-1] if label.endswith("s") else None):
        if not candidate:
            continue
        if candidate in RULES:
            return candidate, ("direct" if candidate == label else "alias")
        alias = ALIASES.get(candidate)
        if alias is not None:
            return alias, "alias"
    return None


# Room type aliases (``basis.domain.rooms`` re-exports these; they live here because
# ``rooms`` imports ``normalize_label`` from this module and the lookup needs the room).
ROOM_ALIASES: Final[dict[str, str]] = {
    "bath": "bathroom",
    "restroom": "bathroom",
    "powder_room": "bathroom",
    "hall": "hallway",
    "corridor": "hallway",
    "mechanical": "mechanical_room",
    "utility_room": "mechanical_room",
    "closet": "storage",
    "outdoor": "exterior",
    "exterior_front": "exterior",
    "exterior_rear": "exterior",
    "parking_lot": "parking",
}


def normalize_room_type(room_type: str | None) -> str:
    """``"Restroom"`` -> ``"bathroom"``: :func:`normalize_label`, then ``ROOM_ALIASES``."""
    name = normalize_label(room_type)
    return ROOM_ALIASES.get(name, name)


def check_property_class(property_class: str) -> PropertyClass:
    if property_class not in PROPERTY_CLASSES:
        raise ValueError(
            f"unknown property_class {property_class!r}; expected one of {PROPERTY_CLASSES}"
        )
    return property_class  # type: ignore[return-value]


def lookup(
    label: str | None,
    material: str | None = None,
    attachment: str | None = None,
    property_class: str = "residential_rental",
    room_type: str | None = None,
) -> RuleHit | None:
    """The rules-table classification of a component, or ``None`` (fall through).

    ``None`` for an empty or unknown label, a generic floor label without a known
    material, or a ``structural`` attachment on a section 1245 row. The row's first
    variant whose context matches (``room_type`` normalized by :func:`normalize_room_type`,
    the normalized ``attachment``, the property class) is the effective rule; the hit keeps
    the row's ``key`` and names the variant in ``context``.
    """
    pclass = check_property_class(property_class)
    name = normalize_label(label)
    if not name:
        return None
    resolved: tuple[str, MatchKind] | None
    if name in GENERIC_FLOOR_LABELS:
        key = MATERIAL_RULES.get(normalize_label(material))
        resolved = None if key is None else (key, "material")
    else:
        resolved = canonical_key(name)
    if resolved is None:
        return None
    key, via = resolved
    attached = normalize_label(attachment)
    rule, context = RULES[key].effective(
        room=normalize_room_type(room_type), attachment=attached, property_class=pclass
    )
    if attached in STRUCTURAL_ATTACHMENTS and rule.section == "1245":
        return None
    confidence = {
        "direct": DIRECT_CONFIDENCE,
        "alias": ALIAS_CONFIDENCE,
        "material": MATERIAL_CONFIDENCE,
    }[via]
    return RuleHit(
        key=key,
        category=rule.category,
        section=rule.section,
        bucket=rule.bucket_for(pclass),
        recovery_period=RECOVERY_YEARS[rule.bucket_for(pclass)],
        asset_class=rule.asset_class,
        irs_note=rule.irs_note,
        citations=rule.citations,
        confidence=confidence,
        matched=name if via != "material" else f"{name}+{normalize_label(material)}",
        via=via,
        property_class=pclass,
        contested=rule.contested,
        context=context,
    )


def table_hits(property_class: str = "residential_rental") -> list[RuleHit]:
    """A hit for every row and every variant, in table order: each row looked up by its key,
    then each of its variants in a context the variant matches (the smallest value of each
    of its sets). For checks over every citation (the ``rule_citations`` eval, the
    real-corpus test). ``ValueError`` when a variant is unreachable (an earlier one shadows
    it)."""
    hits: list[RuleHit] = []
    for key, rule in RULES.items():
        for variant in (None, *rule.variants):
            when = variant.when if variant is not None else When()
            room = min(when.rooms, default="")
            attachment = min(when.attachments, default="")
            pclass = check_property_class(min(when.property_classes, default=property_class))
            effective, _ = rule.effective(room=room, attachment=attachment, property_class=pclass)
            hit = lookup(
                key,
                attachment=attachment or None,
                property_class=pclass,
                room_type=room or None,
            )
            if hit is None or (variant is not None and effective is not variant.rule):
                raise ValueError(f"rule {key!r}: variant {when} is not reachable by lookup")
            hits.append(hit)
    return hits


def hit_name(hit: RuleHit) -> str:
    """``"kitchen_cabinet"`` for a row's hit, ``"kitchen_cabinet[room=bathroom]"`` for a
    variant's."""
    return hit.key if hit.context is None else f"{hit.key}[{hit.context}]"
