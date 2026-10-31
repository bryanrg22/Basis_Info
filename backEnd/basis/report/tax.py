"""What the preparer's schedule says about each property class (STUDY_REPORT_SPEC 11.5).

MACRS GDS method, convention and recovery period per class follow IRS Publication 946,
Table 4-1 and "Which Convention Applies?"; the ADS lives follow its Table B-1 (Rev. Proc.
87-56 classes) and the residential rental and nonresidential real property rows. Bonus
depreciation is the **statutory default** for the acquisition and placed-in-service dates
(IRC 168(k) as amended by P.L. 115-97 and P.L. 119-21; Pub 946 (2024) and (2025)): the
preparer makes the elections, and the schedule says so. It keys on the acquisition date,
not only the placed-in-service date: property acquired before 2025-01-20 keeps the phase-down
rate of its placed-in-service year even when placed in service later.

Nothing here knows the taxpayer's other assets, so the convention for 5, 7 and 15-year
property is the half-year default with a note: mid-quarter applies when more than 40 % of
the year's depreciable basis (other than real property) is placed in service in the last
quarter, which only the preparer can tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from basis.db.enums import Bucket

Method = Literal["200DB", "150DB", "SL"]
Convention = Literal["HY", "MM"]

PUB_946: Final = "IRS Publication 946, Table 4-1 and 'Which Convention Applies?'"
PUB_946_ADS: Final = "IRS Publication 946, Appendix B, Table B-1 (Rev. Proc. 87-56)"
MID_QUARTER_NOTE: Final = (
    "half-year by default; mid-quarter applies if more than 40 % of the year's depreciable "
    "basis other than real property was placed in service in the last 3 months (preparer)"
)


@dataclass(frozen=True, slots=True)
class ClassTerms:
    """One property class as the schedule prints it."""

    bucket: Bucket
    name: str
    irc_section: str
    gds_life: Decimal
    ads_life: Decimal
    method: Method
    convention: Convention
    default_asset_class: str | None
    real_property: bool
    bonus_eligible: bool
    convention_note: str | None


# Residential rental and nonresidential real property have no Rev. Proc. 87-56 class
# number: they are defined in IRC 168(e)(2). ADS: 30 years for residential rental placed in
# service after 2017 (P.L. 115-97), 40 for nonresidential real.
TERMS: Final[dict[Bucket, ClassTerms]] = {
    Bucket.Y5: ClassTerms(
        Bucket.Y5,
        "5-year property",
        "1245",
        Decimal(5),
        Decimal(9),
        "200DB",
        "HY",
        "57.0",
        False,
        True,
        MID_QUARTER_NOTE,
    ),
    Bucket.Y7: ClassTerms(
        Bucket.Y7,
        "7-year property",
        "1245",
        Decimal(7),
        Decimal(10),
        "200DB",
        "HY",
        "00.11",
        False,
        True,
        MID_QUARTER_NOTE,
    ),
    Bucket.Y15: ClassTerms(
        Bucket.Y15,
        "15-year land improvements",
        "1250",
        Decimal(15),
        Decimal(20),
        "150DB",
        "HY",
        "00.3",
        False,
        True,
        MID_QUARTER_NOTE,
    ),
    Bucket.Y27_5: ClassTerms(
        Bucket.Y27_5,
        "Residential rental property",
        "1250",
        Decimal("27.5"),
        Decimal(30),
        "SL",
        "MM",
        None,
        True,
        False,
        None,
    ),
    Bucket.Y39: ClassTerms(
        Bucket.Y39,
        "Nonresidential real property",
        "1250",
        Decimal(39),
        Decimal(40),
        "SL",
        "MM",
        None,
        True,
        False,
        None,
    ),
}

# 168(k)(6) phase-down for property acquired before 2025-01-20 (P.L. 115-97), by the
# calendar year it is placed in service.
PHASE_DOWN: Final[dict[int, Decimal]] = {
    2023: Decimal("0.80"),
    2024: Decimal("0.60"),
    2025: Decimal("0.40"),
    2026: Decimal("0.20"),
}
FULL_BONUS_AFTER: Final = date(2025, 1, 19)  # P.L. 119-21: acquired and placed after it
TCJA_ACQUIRED_AFTER: Final = date(2017, 9, 27)  # P.L. 115-97: 100 % for acquisitions after it


@dataclass(frozen=True, slots=True)
class BonusDefault:
    """The statutory default rate for eligible property, or ``None`` with the reason."""

    rate: Decimal | None
    reason: str
    source: str


def bonus_default(acquired: date | None, placed_in_service: date) -> BonusDefault:
    """The default bonus rate for 5, 7 and 15-year property with these dates (the preparer
    elects out or into the transitional 40 % for the first tax year ending after
    2025-01-19). ``acquired`` is the binding contract or closing date."""
    if acquired is None:
        return BonusDefault(
            None,
            "the acquisition date is needed: the rate depends on it (P.L. 119-21)",
            "IRC 168(k)",
        )
    if placed_in_service < acquired:
        return BonusDefault(
            None, "placed in service before it was acquired: check the dates", "IRC 168(k)"
        )
    if acquired > FULL_BONUS_AFTER:
        return BonusDefault(
            Decimal("1.00"),
            "acquired and placed in service after 2025-01-19",
            "IRC 168(k) as amended by P.L. 119-21 (Pub 946 (2025), What's New)",
        )
    if acquired <= TCJA_ACQUIRED_AFTER:
        return BonusDefault(
            None,
            "acquired on or before 2017-09-27: the pre-2017-Act rules apply (preparer)",
            "IRC 168(k) before P.L. 115-97",
        )
    year = placed_in_service.year
    if year < 2023:
        return BonusDefault(
            Decimal("1.00"),
            f"acquired before 2025-01-20 and placed in service in {year}",
            "IRC 168(k)(6) (P.L. 115-97)",
        )
    rate = PHASE_DOWN.get(year, Decimal(0))
    return BonusDefault(
        rate,
        f"acquired before 2025-01-20 and placed in service in {year}: the phase-down rate",
        "IRC 168(k)(6) (P.L. 115-97); Pub 946 (2024) for 2024"
        + ("; Notice 2026-11 for 2026" if year == 2026 else ""),
    )


def terms(bucket: Bucket | str) -> ClassTerms:
    """The class terms of an asset bucket (:class:`~basis.db.enums.Bucket` or its value)."""
    return TERMS[Bucket(bucket)]


__all__ = [
    "FULL_BONUS_AFTER",
    "MID_QUARTER_NOTE",
    "PHASE_DOWN",
    "PUB_946",
    "PUB_946_ADS",
    "TCJA_ACQUIRED_AFTER",
    "TERMS",
    "BonusDefault",
    "ClassTerms",
    "bonus_default",
    "terms",
]
