"""Classification validators: section/bucket, recovery period, property class, context."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.db.enums import Bucket
from basis.domain.validators import (
    check_component_context,
    check_property_class_bucket,
    check_recovery_period,
    expected_recovery_period,
    validate_classification,
    validate_section_bucket,
)


def codes(issues: list) -> list[str]:  # type: ignore[type-arg]
    return [i.code for i in issues]


@pytest.mark.parametrize(
    ("section", "bucket"),
    [
        ("1245", Bucket.Y5),
        ("1245", Bucket.Y7),
        ("1245", Bucket.Y15),
        ("1250", Bucket.Y15),
        ("1250", Bucket.Y27_5),
        ("1250", Bucket.Y39),
    ],
)
def test_valid_section_bucket_pairs(section: str, bucket: Bucket) -> None:
    assert validate_section_bucket(section, bucket) == []


@pytest.mark.parametrize(
    ("section", "bucket"),
    [("1245", Bucket.Y27_5), ("1245", Bucket.Y39), ("1250", Bucket.Y5), ("1250", Bucket.Y7)],
)
def test_invalid_section_bucket_pairs_are_errors(section: str, bucket: Bucket) -> None:
    [issue] = validate_section_bucket(section, bucket)
    assert (issue.code, issue.severity, issue.needs_review) == (
        "SECTION_BUCKET_MISMATCH",
        "error",
        True,
    )


def test_unknown_missing_section_and_land() -> None:
    assert codes(validate_section_bucket("1231", Bucket.Y5)) == ["SECTION_UNKNOWN"]
    assert codes(validate_section_bucket(None, Bucket.Y5)) == ["SECTION_MISSING"]
    assert codes(validate_section_bucket("1250", Bucket.LAND)) == ["LAND_NOT_DEPRECIABLE"]


@pytest.mark.parametrize(
    ("bucket", "years"),
    [
        (Bucket.Y5, "5"),
        (Bucket.Y7, "7"),
        (Bucket.Y15, "15"),
        (Bucket.Y27_5, "27.5"),
        (Bucket.Y39, "39"),
    ],
)
def test_recovery_period_by_bucket(bucket: Bucket, years: str) -> None:
    assert expected_recovery_period(bucket) == Decimal(years)
    assert check_recovery_period(bucket, Decimal(years)) == []
    assert check_recovery_period(bucket, Decimal(years) + 1)[0].code == "RECOVERY_PERIOD_MISMATCH"
    assert check_recovery_period(bucket, Decimal(years).quantize(Decimal("0.0"))) == []


def test_land_has_no_recovery_period() -> None:
    assert expected_recovery_period(Bucket.LAND) is None
    assert check_recovery_period(Bucket.LAND, Decimal(1)) == []


def test_building_bucket_must_match_the_property_class() -> None:
    assert check_property_class_bucket(Bucket.Y27_5, "residential_rental") == []
    assert check_property_class_bucket(Bucket.Y39, "nonresidential") == []
    assert codes(check_property_class_bucket(Bucket.Y39, "residential_rental")) == [
        "BUILDING_BUCKET_PROPERTY_CLASS"
    ]
    assert codes(check_property_class_bucket(Bucket.Y27_5, "nonresidential")) == [
        "BUILDING_BUCKET_PROPERTY_CLASS"
    ]
    assert check_property_class_bucket(Bucket.Y5, "nonresidential") == []


def test_structural_label_as_personal_property_is_a_warning() -> None:
    [issue] = check_component_context("Drywall partition", "1245", Bucket.Y5)
    assert (issue.code, issue.severity) == ("STRUCTURAL_AS_PERSONAL", "warning")


@pytest.mark.parametrize("label", ["hardwood_floor", "flooring", "wall_cabinet", "retaining_wall"])
def test_whole_tokens_avoid_legacy_false_positives(label: str) -> None:
    """Legacy matched ``floor`` inside ``flooring`` and ``wall`` inside ``wall_cabinet``."""
    assert check_component_context(label, "1245", Bucket.Y5) == []


def test_personal_label_in_a_building_bucket_is_a_warning() -> None:
    """The legacy version of this check could never fire."""
    [issue] = check_component_context("kitchen_cabinet", "1250", Bucket.Y27_5)
    assert issue.code == "PERSONAL_AS_BUILDING"
    assert check_component_context("carpet", "1250", Bucket.Y15) == []  # land improvement


def test_room_context_is_informational() -> None:
    [issue] = check_component_context("boiler", "1250", Bucket.Y39, room_type="mechanical")
    assert (issue.code, issue.needs_review) == ("ROOM_CONTEXT", False)


def test_validate_classification_runs_every_check_in_order() -> None:
    issues = validate_classification(
        label="kitchen cabinet",
        section="1250",
        bucket=Bucket.Y39,
        recovery_period=Decimal("27.5"),
        property_class="residential_rental",
    )
    assert codes(issues) == [
        "RECOVERY_PERIOD_MISMATCH",
        "BUILDING_BUCKET_PROPERTY_CLASS",
        "PERSONAL_AS_BUILDING",
    ]
    clean = validate_classification(
        label="carpet",
        section="1245",
        bucket=Bucket.Y5,
        recovery_period=Decimal(5),
        property_class="residential_rental",
    )
    assert clean == []
