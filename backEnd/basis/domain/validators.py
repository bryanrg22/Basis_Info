"""Classification validators: section/bucket consistency, recovery periods, context checks.

Migrated from ``agentic/agents/classification_verifier.py`` (the rule functions only;
the LLM verifier agent is gone). Each check returns :class:`Issue` values; an ``error``
or ``warning`` puts the asset in front of the engineer (``needs_review``).

Changes from legacy, each a bug fix:

* Buckets are the ``Bucket`` enum, not free text parsed by substring (legacy matched
  ``"5"`` inside ``"15-year"`` and needed a chain of exclusions).
* Component context uses whole label tokens. Legacy used substrings, so ``floor`` (a
  structural word) matched ``hardwood_floor`` and ``flooring`` while ``tile`` matched the
  personal-property list for the same label, and every label containing ``wall`` was
  structural (``wall_cabinet``).
* The "personal property classified as 1250" check could never fire (its condition was
  always false); it now fires for a personal-property label in a 1250 building bucket.
* The recovery period is checked against the bucket (legacy never compared them).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from basis.db.enums import Bucket
from basis.domain.rooms import normalize_room_type
from basis.domain.rules import RECOVERY_YEARS, normalize_label

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: Severity
    message: str

    @property
    def needs_review(self) -> bool:
        return self.severity != "info"


VALID_SECTION_BUCKETS: Final[dict[str, frozenset[Bucket]]] = {
    "1245": frozenset({Bucket.Y5, Bucket.Y7, Bucket.Y15}),  # personal property
    "1250": frozenset({Bucket.Y15, Bucket.Y27_5, Bucket.Y39}),  # real property
}
BUILDING_BUCKETS: Final = frozenset({Bucket.Y27_5, Bucket.Y39})
BUILDING_BUCKET_FOR_CLASS: Final[dict[str, Bucket]] = {
    "residential_rental": Bucket.Y27_5,
    "nonresidential": Bucket.Y39,
}

STRUCTURAL_TOKENS: Final = frozenset(
    {
        "wall",
        "walls",
        "ceiling",
        "foundation",
        "roof",
        "roofing",
        "structural",
        "framing",
        "beam",
        "column",
        "slab",
        "drywall",
        "stucco",
        "brick",
        "masonry",
        "insulation",
    }
)
PERSONAL_TOKENS: Final = frozenset(
    {
        "appliance",
        "cabinet",
        "cabinets",
        "cabinetry",
        "countertop",
        "carpet",
        "carpeting",
        "blind",
        "blinds",
        "thermostat",
        "refrigerator",
        "dishwasher",
        "microwave",
        "washer",
        "dryer",
        "furniture",
        "desk",
        "chair",
    }
)
# Labels where a structural token is a modifier, not the component itself.
STRUCTURAL_EXCEPTIONS: Final = frozenset({"wall_cabinet", "retaining_wall", "wall_sconce"})

SPECIAL_ROOM_RULES: Final[dict[str, tuple[str, str]]] = {
    # room type -> (section that warrants a warning, why)
    "mechanical_room": ("1250", "Items in a mechanical room are often section 1245 equipment"),
}


def tokens(label: str | None) -> frozenset[str]:
    return frozenset(t for t in normalize_label(label).split("_") if t)


def validate_section_bucket(section: str | None, bucket: Bucket) -> list[Issue]:
    """Section 1245 takes 5/7/15-year buckets; section 1250 takes 15/27.5/39-year."""
    if bucket is Bucket.LAND:
        return [Issue("LAND_NOT_DEPRECIABLE", "error", "land is not depreciable property")]
    if section is None or section == "":
        return [Issue("SECTION_MISSING", "warning", "no IRS section recorded")]
    valid = VALID_SECTION_BUCKETS.get(section)
    if valid is None:
        return [Issue("SECTION_UNKNOWN", "error", f"unknown IRS section {section!r}")]
    if bucket not in valid:
        names = ", ".join(sorted(b.value for b in valid))
        return [
            Issue(
                "SECTION_BUCKET_MISMATCH",
                "error",
                f"section {section} cannot have bucket {bucket.value}; valid: {names}",
            )
        ]
    return []


def expected_recovery_period(bucket: Bucket) -> Decimal | None:
    """Years for a depreciable bucket; ``None`` for land."""
    return RECOVERY_YEARS.get(bucket)


def check_recovery_period(bucket: Bucket, recovery_period: Decimal) -> list[Issue]:
    expected = expected_recovery_period(bucket)
    if expected is None:
        return []
    if Decimal(recovery_period) != expected:
        return [
            Issue(
                "RECOVERY_PERIOD_MISMATCH",
                "error",
                f"bucket {bucket.value} recovers over {expected} years, not {recovery_period}",
            )
        ]
    return []


def check_property_class_bucket(bucket: Bucket, property_class: str) -> list[Issue]:
    """A building bucket must match the property class (27.5 residential, 39 otherwise)."""
    if bucket not in BUILDING_BUCKETS:
        return []
    expected = BUILDING_BUCKET_FOR_CLASS.get(property_class)
    if expected is not None and bucket is not expected:
        return [
            Issue(
                "BUILDING_BUCKET_PROPERTY_CLASS",
                "error",
                f"{property_class} property uses {expected.value} for the building, "
                f"not {bucket.value}",
            )
        ]
    return []


def check_component_context(
    label: str | None, section: str | None, bucket: Bucket, room_type: str | None = None
) -> list[Issue]:
    """Structural labels as 1245, personal-property labels as building, room anomalies."""
    issues: list[Issue] = []
    name = normalize_label(label)
    words = tokens(label)
    if section == "1245" and name not in STRUCTURAL_EXCEPTIONS and words & STRUCTURAL_TOKENS:
        issues.append(
            Issue(
                "STRUCTURAL_AS_PERSONAL",
                "warning",
                f"{label!r} looks structural but is classified as section 1245",
            )
        )
    if section == "1250" and bucket in BUILDING_BUCKETS and words & PERSONAL_TOKENS:
        issues.append(
            Issue(
                "PERSONAL_AS_BUILDING",
                "warning",
                f"{label!r} looks like personal property but is classified as building",
            )
        )
    room = normalize_room_type(room_type)
    special = SPECIAL_ROOM_RULES.get(room)
    if special is not None and section == special[0]:
        issues.append(Issue("ROOM_CONTEXT", "info", special[1]))
    return issues


def validate_classification(
    *,
    label: str | None,
    section: str | None,
    bucket: Bucket,
    recovery_period: Decimal,
    property_class: str,
    room_type: str | None = None,
) -> list[Issue]:
    """Every classification check, in a fixed order."""
    return [
        *validate_section_bucket(section, bucket),
        *check_recovery_period(bucket, recovery_period),
        *check_property_class_bucket(bucket, property_class),
        *check_component_context(label, section, bucket, room_type),
    ]
