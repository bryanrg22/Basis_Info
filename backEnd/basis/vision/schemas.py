"""The structured output of one photo analysis (``PhotoAnalysis``) and its vocabularies.

The model is asked for exactly this shape through strict JSON-schema output, so the room
type, component labels, materials and attachments are enums: the model cannot invent a
label, and an object outside the vocabulary is ``other`` with a free-text ``note``.

Vocabulary choices:

* ``RoomType`` covers the residential spaces in URAR appraisals and the common commercial
  ones from the legacy prompt (office, lobby, hallway, storage, parking), plus three
  non-room answers: ``exterior`` (outside the building), ``mechanical`` (equipment rooms
  and outdoor equipment such as an AC condenser) and ``ambiguous`` (the model cannot tell;
  scored as an abstention by the eval, BUILD_PLAN 13.2). ``other`` is a room that is
  clearly none of the listed types. Values outside the enum that reach
  :func:`normalize_room_type` (correction input, older raw payloads) map to ``other``.
* ``ComponentLabel`` (77 labels + ``other``) is the union of the legacy static
  classification rule keys (``agentic/agents/static_classification_rules.py``), the
  synthetic fixture vocabulary (``tests/fixtures/synthetic/photos/labels.json``) and the
  building systems a cost segregation study separates (HVAC, electrical, fire safety,
  site work), plus six labels for report rows the engineer writes that had none
  (EVAL_REPORT sections 13 and 19): ``concrete_floor`` (a sealed or bare concrete floor),
  ``half_wall`` (a partial-height interior wall), ``bathroom_accessory`` (towel bars,
  paper holders, grab bars, shower rods), ``window_guard`` (bars, grilles or guards over a
  window or door), ``shed`` (a detached shed) and ``concrete_pad`` (an exterior pad that
  equipment stands on). Labels are snake_case nouns so the classification rules can key on
  them.
* Confidence is the model's self-report in [0, 1]; it is recorded, not trusted: a photo
  is ``needs_review`` when the room is ``ambiguous`` or its confidence is below the
  review threshold (``basis.vision.analyze``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RoomType(StrEnum):
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    BEDROOM = "bedroom"
    LIVING_ROOM = "living_room"
    DINING_ROOM = "dining_room"
    FAMILY_ROOM = "family_room"
    LAUNDRY = "laundry"
    HALLWAY = "hallway"
    STAIRWAY = "stairway"
    CLOSET = "closet"
    BASEMENT = "basement"
    ATTIC = "attic"
    GARAGE = "garage"
    OFFICE = "office"
    LOBBY = "lobby"
    STORAGE = "storage"
    PARKING = "parking"
    EXTERIOR = "exterior"
    MECHANICAL = "mechanical"
    OTHER = "other"
    AMBIGUOUS = "ambiguous"


# The labels the eval and the fixtures use directly; everything else is accepted too.
ROOM_TYPES: Final = tuple(t.value for t in RoomType)


class ComponentLabel(StrEnum):
    # Interior finishes
    CARPET = "carpet"
    HARDWOOD_FLOOR = "hardwood_floor"
    VINYL_FLOORING = "vinyl_flooring"
    LAMINATE_FLOORING = "laminate_flooring"
    TILE = "tile"
    WALL_FINISH = "wall_finish"
    CEILING_FINISH = "ceiling_finish"
    BASEBOARD = "baseboard"
    CROWN_MOLDING = "crown_molding"
    WAINSCOTING = "wainscoting"
    CONCRETE_FLOOR = "concrete_floor"
    HALF_WALL = "half_wall"
    INTERIOR_DOOR = "interior_door"
    EXTERIOR_DOOR = "exterior_door"
    WINDOW = "window"
    BLINDS = "blinds"
    WINDOW_TREATMENT = "window_treatment"
    STAIRS = "stairs"
    RAILING = "railing"
    FIREPLACE = "fireplace"
    CLOSET_SHELVING = "closet_shelving"
    BUILT_IN_SHELVING = "built_in_shelving"
    MIRROR = "mirror"
    # Kitchen and appliances
    KITCHEN_CABINET = "kitchen_cabinet"
    COUNTERTOP = "countertop"
    SINK = "sink"
    APPLIANCE = "appliance"
    REFRIGERATOR = "refrigerator"
    DISHWASHER = "dishwasher"
    STOVE = "stove"
    OVEN = "oven"
    MICROWAVE = "microwave"
    RANGE_HOOD = "range_hood"
    GARBAGE_DISPOSAL = "garbage_disposal"
    WASHER = "washer"
    DRYER = "dryer"
    # Bathroom
    BATHROOM_VANITY = "bathroom_vanity"
    TOILET = "toilet"
    BATHTUB = "bathtub"
    SHOWER = "shower"
    BATHROOM_ACCESSORY = "bathroom_accessory"
    # Lighting and electrical
    LIGHT_FIXTURE = "light_fixture"
    CEILING_FAN = "ceiling_fan"
    OUTDOOR_LIGHTING = "outdoor_lighting"
    ELECTRICAL_PANEL = "electrical_panel"
    ELECTRICAL_OUTLET = "electrical_outlet"
    # HVAC and plumbing
    HVAC_UNIT = "hvac_unit"
    AC_CONDENSER = "ac_condenser"
    HVAC_VENT = "hvac_vent"
    THERMOSTAT = "thermostat"
    WATER_HEATER = "water_heater"
    # Fire, safety, security
    SMOKE_DETECTOR = "smoke_detector"
    SPRINKLER_SYSTEM = "sprinkler_system"
    SECURITY_SYSTEM = "security_system"
    DOORBELL = "doorbell"
    WINDOW_GUARD = "window_guard"
    # Furniture and movable property
    FURNITURE = "furniture"
    DESK = "desk"
    CHAIR = "chair"
    FILING_CABINET = "filing_cabinet"
    # Exterior and site
    GARAGE_DOOR = "garage_door"
    ROOF = "roof"
    SIDING = "siding"
    GUTTER = "gutter"
    DECK = "deck"
    PATIO = "patio"
    PORCH = "porch"
    POOL = "pool"
    FENCE = "fence"
    RETAINING_WALL = "retaining_wall"
    DRIVEWAY = "driveway"
    SIDEWALK = "sidewalk"
    PARKING_LOT = "parking_lot"
    LANDSCAPING = "landscaping"
    SIGNAGE = "signage"
    SHED = "shed"
    CONCRETE_PAD = "concrete_pad"
    OTHER = "other"


COMPONENT_LABELS: Final = tuple(c.value for c in ComponentLabel)

# Caps on one analysis (the JSON schema the model fills and the merged decomposed answer).
MAX_COMPONENTS: Final = 60
MAX_DESCRIPTION_CHARS: Final = 500
MAX_NOTE_CHARS: Final = 200


class Material(StrEnum):
    WOOD = "wood"
    METAL = "metal"
    STEEL = "steel"
    ALUMINUM = "aluminum"
    VINYL = "vinyl"
    LAMINATE = "laminate"
    CERAMIC = "ceramic"
    PORCELAIN = "porcelain"
    STONE = "stone"
    GRANITE = "granite"
    QUARTZ = "quartz"
    CONCRETE = "concrete"
    ASPHALT = "asphalt"
    BRICK = "brick"
    GLASS = "glass"
    FABRIC = "fabric"
    PLASTIC = "plastic"
    DRYWALL = "drywall"
    FIBERGLASS = "fiberglass"
    COMPOSITE = "composite"
    # EVAL_REPORT section 16.5: the second look's answers the report rows depend on
    # (Wall Coverings; a painted block or brick wall), stored as themselves.
    WALLPAPER = "wallpaper"
    PAINTED_MASONRY = "painted_masonry"
    OTHER = "other"
    UNKNOWN = "unknown"


class Attachment(StrEnum):
    """How the component is attached: drives the 1245 vs 1250 distinction downstream."""

    PERMANENT = "permanent"  # built in; removal damages the building
    REMOVABLE = "removable"  # fastened but removable without damage
    FREESTANDING = "freestanding"  # movable, not attached
    UNKNOWN = "unknown"


class Component(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: ComponentLabel
    note: str | None = Field(
        default=None,
        description="Free text; required when label is 'other'",
        max_length=MAX_NOTE_CHARS,
    )
    count: int = Field(ge=1, le=500, description="How many of this item are visible")
    material: Material
    attachment: Attachment
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _unknown_label_is_other(cls, data: Any) -> Any:
        """An out-of-vocabulary label becomes ``other`` and keeps the text as the note; an
        over-long note is cut to the limit (strict JSON schemas cannot carry ``maxLength``,
        so a talkative model must not fail the whole photo over a note)."""
        if isinstance(data, dict):
            label = data.get("label")
            if isinstance(label, str) and label not in COMPONENT_LABELS:
                key = label.strip().lower().replace("-", "_").replace(" ", "_")
                data = dict(data)
                if key in COMPONENT_LABELS:
                    data["label"] = key
                else:
                    data["label"] = ComponentLabel.OTHER.value
                    data["note"] = data.get("note") or label[:200]
            note = data.get("note")
            if isinstance(note, str) and len(note) > MAX_NOTE_CHARS:
                data = dict(data)
                data["note"] = note[: MAX_NOTE_CHARS - 1] + "…"
        return data


def _room_type_before(value: Any) -> Any:
    return normalize_room_type(value) if isinstance(value, str) else value


class PhotoAnalysis(BaseModel):
    """One photo: the room it shows, a floor hint, and the visible components."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    room_type: RoomType
    room_confidence: float = Field(ge=0.0, le=1.0)
    floor: int | None = Field(
        default=None,
        ge=-3,
        le=200,
        description="Floor if visible cues show it (1 = ground, 0 or negative = below grade)",
    )
    room_ordinal: int = Field(
        default=1,
        ge=1,
        le=50,
        description="Which room of this type on this floor, only when the photo shows it "
        "(for example a door sign 'Bedroom 2'); otherwise 1",
    )
    components: list[Component] = Field(default_factory=list, max_length=MAX_COMPONENTS)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_CHARS)
    # Set by basis.vision.analyze, never by the model: the analysis could not be trusted.
    needs_review: bool = False
    review_reason: str | None = None

    _room = field_validator("room_type", mode="before")(_room_type_before)


class ModelPhotoAnalysis(BaseModel):
    """The part of :class:`PhotoAnalysis` the model fills (what the JSON schema exposes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    room_type: RoomType
    room_confidence: float = Field(ge=0.0, le=1.0)
    floor: int | None = Field(ge=-3, le=200)
    room_ordinal: int = Field(ge=1, le=50)
    components: list[Component] = Field(max_length=MAX_COMPONENTS)
    description: str = Field(max_length=MAX_DESCRIPTION_CHARS)

    _room = field_validator("room_type", mode="before")(_room_type_before)

    @field_validator("description", mode="before")
    @classmethod
    def _cut_description(cls, value: Any) -> Any:
        """Same reason as the component note: an over-long description is cut, not fatal."""
        if isinstance(value, str) and len(value) > MAX_DESCRIPTION_CHARS:
            return value[: MAX_DESCRIPTION_CHARS - 1] + "…"
        return value

    def to_analysis(self) -> PhotoAnalysis:
        return PhotoAnalysis(**self.model_dump())


def normalize_room_type(value: str | None) -> RoomType:
    """A room type from free text: known values pass, synonyms map, the rest is ``other``."""
    if value is None:
        return RoomType.AMBIGUOUS
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    if key in ROOM_TYPES:
        return RoomType(key)
    synonyms = {
        "mechanical_room": RoomType.MECHANICAL,
        "utility": RoomType.MECHANICAL,
        "utility_room": RoomType.MECHANICAL,
        "bath": RoomType.BATHROOM,
        "restroom": RoomType.BATHROOM,
        "living": RoomType.LIVING_ROOM,
        "lounge": RoomType.LIVING_ROOM,
        "dining": RoomType.DINING_ROOM,
        "den": RoomType.FAMILY_ROOM,
        "outdoor": RoomType.EXTERIOR,
        "outside": RoomType.EXTERIOR,
        "unknown": RoomType.AMBIGUOUS,
        "unclear": RoomType.AMBIGUOUS,
    }
    return synonyms.get(key, RoomType.OTHER)


def ambiguous_analysis(reason: str) -> PhotoAnalysis:
    """What a photo gets when no usable analysis exists: ambiguous, flagged for review."""
    return PhotoAnalysis(
        room_type=RoomType.AMBIGUOUS,
        room_confidence=0.0,
        components=[],
        needs_review=True,
        review_reason=reason,
    )
