"""``basis.report.tax``: the class terms of IRS Pub 946 Table 4-1 and the statutory bonus
default, which keys on the acquisition date (STUDY_REPORT_SPEC 11.5)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.db.enums import Bucket
from basis.report.tax import TERMS, bonus_default, terms


@pytest.mark.parametrize(
    ("bucket", "life", "ads", "method", "convention", "section", "asset_class", "bonus"),
    [
        (Bucket.Y5, "5", "9", "200DB", "HY", "1245", "57.0", True),
        (Bucket.Y7, "7", "10", "200DB", "HY", "1245", "00.11", True),
        (Bucket.Y15, "15", "20", "150DB", "HY", "1250", "00.3", True),
        (Bucket.Y27_5, "27.5", "30", "SL", "MM", "1250", None, False),
        (Bucket.Y39, "39", "40", "SL", "MM", "1250", None, False),
    ],
)
def test_each_class_follows_pub_946(
    bucket: Bucket,
    life: str,
    ads: str,
    method: str,
    convention: str,
    section: str,
    asset_class: str | None,
    bonus: bool,
) -> None:
    t = terms(bucket)
    assert (t.gds_life, t.ads_life) == (Decimal(life), Decimal(ads))
    assert (t.method, t.convention, t.irc_section) == (method, convention, section)
    assert (t.default_asset_class, t.bonus_eligible) == (asset_class, bonus)
    assert t.real_property is (bucket in (Bucket.Y27_5, Bucket.Y39))
    assert (t.convention_note is not None) is bonus  # the mid-quarter caveat on HY classes
    assert terms(bucket.value) is t
    assert Bucket.LAND not in TERMS  # land is not depreciated


@pytest.mark.parametrize(
    ("acquired", "placed", "rate"),
    [
        # The public sample's defect (STUDY_REPORT_SPEC 11.1): acquired 2024, placed 2025.
        (date(2024, 6, 18), date(2025, 3, 1), "0.40"),
        (date(2024, 12, 3), date(2024, 12, 3), "0.60"),
        (date(2023, 5, 1), date(2023, 5, 1), "0.80"),
        (date(2022, 5, 1), date(2022, 5, 1), "1.00"),
        (date(2017, 9, 28), date(2020, 1, 1), "1.00"),
        (date(2025, 1, 19), date(2025, 6, 1), "0.40"),  # the last day before the change
        (date(2025, 1, 20), date(2025, 6, 1), "1.00"),  # the first day of P.L. 119-21
        (date(2025, 2, 1), date(2027, 1, 1), "1.00"),
        (date(2024, 12, 1), date(2026, 2, 1), "0.20"),
        (date(2024, 12, 1), date(2027, 2, 1), "0.00"),
    ],
)
def test_the_bonus_default_keys_on_the_acquisition_date(
    acquired: date, placed: date, rate: str
) -> None:
    found = bonus_default(acquired, placed)
    assert found.rate == Decimal(rate), found.reason
    assert found.source.startswith("IRC 168(k)")


@pytest.mark.parametrize(
    ("acquired", "placed", "why"),
    [
        (None, date(2025, 3, 1), "acquisition date is needed"),
        (date(2025, 3, 1), date(2025, 2, 1), "placed in service before it was acquired"),
        (date(2017, 9, 27), date(2018, 1, 1), "pre-2017-Act rules"),
    ],
)
def test_the_bonus_default_refuses_to_guess(acquired: date | None, placed: date, why: str) -> None:
    found = bonus_default(acquired, placed)
    assert found.rate is None and why in found.reason
