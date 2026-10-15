"""The IRS classification rules table: component label -> section, bucket, life, citations.

Migrated from ``agentic/agents/static_classification_rules.py`` (rows, aliases and
citations unchanged unless noted below). A hit classifies a component without a model
call; a miss falls through to the classification cache and then the residual classifier
(:mod:`basis.classify.router`).

Changes from the legacy table, each deliberate:

* ``flooring`` and ``floor`` no longer alias to ``carpet``. A generic floor label is
  resolved by its ``material`` (carpet, vinyl, laminate, tile, hardwood); without a
  material it is a miss, so the residual classifier (or the engineer) decides.
* Rows whose legacy treatment conflicts with the text of Treas. Reg. section
  1.48-1(e)(2) ("plumbing and plumbing fixtures, such as sinks and bathtubs", "electric
  wiring and lighting fixtures", floors "and any permanent coverings therefor such as
  paneling or tiling" are structural components) keep their legacy bucket but carry a
  ``contested`` note; a hit on them is returned with that note so the asset goes to the
  engineer (``needs_review``) instead of the tax position changing silently.
* An ``attachment`` of ``structural`` contradicts every section 1245 row and makes the
  lookup a miss (conflicting evidence goes to review).
* A few aliases for labels the Stage 2 vision stub and the synthetic fixtures emit
  (``cabinetry``, ``tile_flooring``, ``decorative_lighting``, ``ceiling_fans``).

``property_class`` (``residential_rental`` or ``nonresidential``) is part of the lookup and
of the cache key because the building buckets differ (27.5 vs 39 years); no current row
differs by class, which ``tests/unit/domain/test_rules_table.py`` pins.

The citations are ``(doc_id, page, excerpt)`` claims copied from the legacy table (two
excerpts, ``Countertops`` and ``Appliances``, were lengthened: an excerpt shorter than
``basis.classify.verify.MIN_EXCERPT_CHARS`` can never verify). They
are verified at classification time against the reference corpus
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
RULES_VERSION: Final = "2026-10-15.1"

PropertyClass = Literal["residential_rental", "nonresidential"]
PROPERTY_CLASSES: Final[tuple[str, ...]] = get_args(PropertyClass)
Section = Literal["1245", "1250"]
MatchKind = Literal["direct", "alias", "material"]

ATG: Final = "IRS_IRS_COST_SEG_ATG__2024"
PUB946: Final = "IRS_IRS_PUB_946__2024"
REV_PROC: Final = "IRS_REV_PROC_87_56"

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

_PLUMBING_FIXTURE: Final = (
    "Treas. Reg. 1.48-1(e)(2) lists 'plumbing and plumbing fixtures, such as sinks and "
    "bathtubs' as structural components (section 1250); the legacy 5-year treatment needs "
    "engineer confirmation"
)
_LIGHTING_FIXTURE: Final = (
    "Treas. Reg. 1.48-1(e)(2) lists 'electric wiring and lighting fixtures' as structural "
    "components; only decorative fixtures are commonly treated as section 1245, so a "
    "generic light needs engineer confirmation"
)
_PERMANENT_COVERING: Final = (
    "Treas. Reg. 1.48-1(e)(2) treats floors and 'any permanent coverings therefor such as "
    "paneling or tiling' as structural components; the legacy 5-year treatment needs "
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

    @property
    def recovery_period(self) -> Decimal:
        return RECOVERY_YEARS[self.bucket]


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

    @property
    def irs_section(self) -> str:
        """The value stored in ``assets.irs_section``."""
        return self.section


def _c(doc_id: str, page: int, excerpt: str) -> RuleCitation:
    return RuleCitation(doc_id, page, excerpt)


def _p5(
    key: str, category: str, note: str, *cites: RuleCitation, contested: str | None = None
) -> Rule:
    return Rule(key, category, "1245", Bucket.Y5, "57.0", note, cites, contested)


def _p7(key: str, category: str, note: str, *cites: RuleCitation) -> Rule:
    return Rule(key, category, "1245", Bucket.Y7, "00.11", note, cites)


def _l15(key: str, category: str, note: str, *cites: RuleCitation) -> Rule:
    return Rule(key, category, "1250", Bucket.Y15, "00.3", note, cites)


_RULES: Final[tuple[Rule, ...]] = (
    # ---------------------------------------------------- section 1245, 5-year (57.0)
    _p5(
        "carpet",
        "Carpet",
        "Carpeting in residential rental property is Section 1245 personal property with "
        "5-year recovery per Rev Proc 87-56 Asset Class 57.0",
        _c(REV_PROC, 12, "Asset class 57.0 includes carpeting"),
        _c(ATG, 415, "Carpeting - 5-year recovery"),
    ),
    _p5(
        "light_fixture",
        "Light fixtures",
        "Decorative light fixtures are Section 1245 personal property, 5-year recovery",
        _c(ATG, 418, "Decorative lighting fixtures"),
        contested=_LIGHTING_FIXTURE,
    ),
    _p5(
        "kitchen_cabinet",
        "Kitchen cabinets",
        "Kitchen cabinets in residential rental are Section 1245 personal property",
        _c(ATG, 412, "Cabinets - kitchen"),
    ),
    _p5(
        "countertop",
        "Countertops",
        "Countertops (non-structural) are Section 1245 personal property",
        _c(ATG, 412, "Countertops (non-structural)"),
    ),
    _p5(
        "appliance",
        "Appliances",
        "Kitchen appliances are Section 1245 personal property",
        _c(PUB946, 42, "Appliances - residential rental"),
    ),
    _p5(
        "refrigerator",
        "Refrigerator",
        "Refrigerators are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - refrigerators"),
    ),
    _p5(
        "dishwasher",
        "Dishwasher",
        "Dishwashers are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - dishwashers"),
    ),
    _p5(
        "stove",
        "Stove / range",
        "Stoves/ranges are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - stoves"),
    ),
    _p5(
        "oven",
        "Oven",
        "Ovens are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - ovens"),
    ),
    _p5(
        "microwave",
        "Microwave",
        "Microwaves are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - microwaves"),
    ),
    _p5(
        "washer",
        "Clothes washer",
        "Washing machines are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - washers"),
    ),
    _p5(
        "dryer",
        "Clothes dryer",
        "Dryers are Section 1245 personal property with 5-year recovery",
        _c(PUB946, 42, "Appliances - dryers"),
    ),
    _p5(
        "garbage_disposal",
        "Garbage disposal",
        "Garbage disposals are Section 1245 personal property with 5-year recovery",
        _c(ATG, 412, "Garbage disposal units"),
    ),
    _p5(
        "ceiling_fan",
        "Ceiling fan",
        "Ceiling fans are Section 1245 personal property with 5-year recovery",
        _c(ATG, 418, "Ceiling fans"),
    ),
    _p5(
        "blinds",
        "Window blinds",
        "Window blinds are Section 1245 personal property with 5-year recovery",
        _c(ATG, 420, "Window treatments - blinds"),
    ),
    _p5(
        "window_treatment",
        "Window treatments",
        "Window treatments are Section 1245 personal property with 5-year recovery",
        _c(ATG, 420, "Window treatments"),
    ),
    _p5(
        "bathroom_vanity",
        "Bathroom vanity",
        "Bathroom vanities are Section 1245 personal property with 5-year recovery",
        _c(ATG, 412, "Vanities - bathroom"),
    ),
    _p5(
        "toilet",
        "Toilet",
        "Toilets are Section 1245 personal property with 5-year recovery",
        _c(ATG, 414, "Plumbing fixtures - toilets"),
        contested=_PLUMBING_FIXTURE,
    ),
    _p5(
        "sink",
        "Sink",
        "Sinks are Section 1245 personal property with 5-year recovery",
        _c(ATG, 414, "Plumbing fixtures - sinks"),
        contested=_PLUMBING_FIXTURE,
    ),
    _p5(
        "bathtub",
        "Bathtub",
        "Bathtubs are Section 1245 personal property with 5-year recovery",
        _c(ATG, 414, "Plumbing fixtures - bathtubs"),
        contested=_PLUMBING_FIXTURE,
    ),
    _p5(
        "shower",
        "Shower",
        "Shower units are Section 1245 personal property with 5-year recovery",
        _c(ATG, 414, "Plumbing fixtures - showers"),
        contested=_PLUMBING_FIXTURE,
    ),
    _p5(
        "mirror",
        "Mirror",
        "Mirrors (decorative) are Section 1245 personal property with 5-year recovery",
        _c(ATG, 420, "Mirrors - decorative"),
    ),
    _p5(
        "smoke_detector",
        "Smoke detector",
        "Smoke detectors are Section 1245 personal property with 5-year recovery",
        _c(ATG, 416, "Safety equipment - smoke detectors"),
    ),
    _p5(
        "thermostat",
        "Thermostat",
        "Thermostats are Section 1245 personal property with 5-year recovery",
        _c(ATG, 416, "HVAC controls - thermostats"),
    ),
    _p5(
        "doorbell",
        "Doorbell",
        "Doorbells are Section 1245 personal property with 5-year recovery",
        _c(ATG, 418, "Electrical accessories - doorbells"),
    ),
    _p5(
        "tile",
        "Tile",
        "Floor/wall tile in residential is Section 1245 with 5-year recovery",
        _c(ATG, 415, "Floor coverings - tile"),
        contested=_PERMANENT_COVERING,
    ),
    _p5(
        "hardwood_floor",
        "Hardwood flooring",
        "Hardwood flooring in residential is Section 1245 with 5-year recovery",
        _c(ATG, 415, "Floor coverings - hardwood"),
        contested=_PERMANENT_COVERING,
    ),
    _p5(
        "vinyl_flooring",
        "Vinyl flooring",
        "Vinyl flooring in residential is Section 1245 with 5-year recovery",
        _c(ATG, 415, "Floor coverings - vinyl"),
    ),
    _p5(
        "laminate_flooring",
        "Laminate flooring",
        "Laminate flooring in residential is Section 1245 with 5-year recovery",
        _c(ATG, 415, "Floor coverings - laminate"),
    ),
    _p5(
        "closet_shelving",
        "Closet shelving",
        "Closet shelving/organizers are Section 1245 personal property",
        _c(ATG, 412, "Shelving - closet"),
    ),
    _p5(
        "water_heater",
        "Water heater",
        "Water heaters serving single units are Section 1245 with 5-year recovery",
        _c(ATG, 414, "Water heaters - unit-specific"),
    ),
    # ---------------------------------------------------- section 1245, 7-year (00.11)
    _p7(
        "furniture",
        "Furniture",
        "Office furniture and fixtures are 7-year property under Asset Class 00.11",
        _c(REV_PROC, 8, "Asset class 00.11 - Office furniture"),
    ),
    _p7(
        "desk",
        "Desk",
        "Desks are 7-year property under Asset Class 00.11",
        _c(REV_PROC, 8, "Asset class 00.11 - desks"),
    ),
    _p7(
        "chair",
        "Chair",
        "Chairs (office) are 7-year property under Asset Class 00.11",
        _c(REV_PROC, 8, "Asset class 00.11 - chairs"),
    ),
    _p7(
        "filing_cabinet",
        "Filing cabinet",
        "Filing cabinets are 7-year property under Asset Class 00.11",
        _c(REV_PROC, 8, "Asset class 00.11 - filing cabinets"),
    ),
    # ---------------------------------------------------- land improvements, 15-year (00.3)
    _l15(
        "parking_lot",
        "Parking lot",
        "Parking lots are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - parking lots"),
    ),
    _l15(
        "sidewalk",
        "Sidewalks",
        "Sidewalks are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - sidewalks"),
    ),
    _l15(
        "landscaping",
        "Landscaping",
        "Landscaping is a Section 1250 land improvement with 15-year recovery",
        _c(PUB946, 38, "Land improvements - landscaping"),
    ),
    _l15(
        "fence",
        "Fencing",
        "Fences are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - fences"),
    ),
    _l15(
        "retaining_wall",
        "Retaining wall",
        "Retaining walls are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - retaining walls"),
    ),
    _l15(
        "outdoor_lighting",
        "Site lighting",
        "Site lighting is a Section 1250 land improvement with 15-year recovery",
        _c(ATG, 420, "Site improvements - outdoor lighting"),
    ),
    _l15(
        "driveway",
        "Driveway",
        "Driveways are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - driveways"),
    ),
    _l15(
        "signage",
        "Exterior signage",
        "Exterior signage is a Section 1250 land improvement with 15-year recovery",
        _c(ATG, 420, "Site improvements - signage"),
    ),
    _l15(
        "pool",
        "Swimming pool",
        "Swimming pools are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - swimming pools"),
    ),
    _l15(
        "patio",
        "Patio",
        "Patios (exterior) are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - patios"),
    ),
    _l15(
        "deck",
        "Deck",
        "Decks (exterior) are Section 1250 land improvements with 15-year recovery",
        _c(PUB946, 38, "Land improvements - decks"),
    ),
    _l15(
        "sprinkler_system",
        "Irrigation system",
        "Irrigation/sprinkler systems are Section 1250 land improvements",
        _c(PUB946, 38, "Land improvements - irrigation systems"),
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
    "track_lighting": "light_fixture",
    "lamp": "light_fixture",
    "decorative_lighting": "light_fixture",
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
    "conference_table": "furniture",
    "bookcase": "furniture",
    "shelf": "closet_shelving",
    "shelves": "closet_shelving",
    "storage_shelf": "closet_shelving",
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
) -> RuleHit | None:
    """The rules-table classification of a component, or ``None`` (fall through).

    ``None`` for an empty or unknown label, a generic floor label without a known
    material, or a ``structural`` attachment on a section 1245 row.
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
    rule = RULES[key]
    if normalize_label(attachment) in STRUCTURAL_ATTACHMENTS and rule.section == "1245":
        return None
    confidence = {
        "direct": DIRECT_CONFIDENCE,
        "alias": ALIAS_CONFIDENCE,
        "material": MATERIAL_CONFIDENCE,
    }[via]
    return RuleHit(
        key=rule.key,
        category=rule.category,
        section=rule.section,
        bucket=rule.bucket,
        recovery_period=rule.recovery_period,
        asset_class=rule.asset_class,
        irs_note=rule.irs_note,
        citations=rule.citations,
        confidence=confidence,
        matched=name if via != "material" else f"{name}+{normalize_label(material)}",
        via=via,
        property_class=pclass,
        contested=rule.contested,
    )
