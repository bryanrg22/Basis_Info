"""Basis and land allocation: from the purchase price to a depreciable basis, and from a
replacement-cost schedule to the dollars the preparer files (STUDY_REPORT_SPEC sections 5.1,
5.2, 5.4; the sources in docs/review_2026-10-15/BASIS_RESEARCH.md).

Pure: ``Decimal`` end to end, no I/O, no floats, deterministic. Two steps:

1. :func:`worksheet`: ``price + capitalized acquisition costs − land``. The land comes from
   one chosen source (the appraisal's site value, a land appraisal, or a flat percentage with
   its source); the assessor's land/improvement ratio is only ever a check (ATG p.64); land is
   never the residual of price minus improvements (ATG p.64). Acquisition costs that are not
   capitalized (loan costs, prepaid interest, escrows: Pub. 551) are refused. The use decides
   27.5 or 39 years (§168(e)(2): 80 % of gross rental income from dwelling units, none of it
   transient lodging).
2. :func:`allocate`: one factor on every row (``T = B + CC``), bucket totals and
   percentages, and ``B`` and ``CC`` split pro rata per bucket. With ``depreciated=True``
   (the structure model, docs/review_2026-10-15/STRUCTURE_MODEL.md section 3.8) every row
   carries its replacement cost new less depreciation and the factor is ``T / ΣRCNLD``, by
   construction within the reconciliation's tolerance of 1 (the ``rcnld_factor`` check);
   without it the factor is ``T / ΣRCN``, the design partner's method, and the ATG's
   objection to that uniform factor (ch. 3 A(6), D(3)–(5)) is recorded as the
   ``uniform_factor`` check.

Rounding, stated once:

* Money is quantized to the cent with ``ROUND_HALF_UP``. Percentages to 0.1 (as printed),
  shares to 6 places, the factor is reported to 9 places and used at full precision.
* Rows: ``allocated_i = RCN_i × f`` rounded half up. When the rounded rows do not sum to
  ``T``, the residual cents go one each to the rows with the largest rounding remainders
  (largest-remainder rule, ties broken by schedule order); every row records the cents it
  gained or lost against plain rounding. The rows therefore always sum to ``T`` to the cent.
* The pro-rata block: ``building_b = B × bucket_b / T`` with the same largest-remainder rule
  across buckets so the building column sums to ``B``; ``closing_b = bucket_b − building_b``
  so each pair sums to its bucket and the closing column sums to ``CC``.
* A row split across building systems (a weighted mapping): every part but the last is
  rounded half up, the last part is the remainder, so the parts sum to the row.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from typing import Final

CENT: Final = Decimal("0.01")
TENTH_PERCENT: Final = Decimal("0.1")
SHARE_PLACES: Final = Decimal("0.000001")
FACTOR_PLACES: Final = Decimal("0.000000001")
ZERO: Final = Decimal(0)
ONE: Final = Decimal(1)
HUNDRED: Final = Decimal(100)
PRECISION: Final = 34

# Checks from the spec (5.2): the land share of the price and the appraisal's age.
LAND_SHARE_MIN: Final = Decimal("0.05")
LAND_SHARE_MAX: Final = Decimal("0.60")
APPRAISAL_WINDOW_DAYS: Final = 365
# How far the chosen land may sit from the appraisal's site value (and from the assessor's
# ratio) before the check warns.
LAND_TOLERANCE: Final = Decimal("0.05")
# §168(e)(2)(A): residential rental needs 80 % of gross rental income from dwelling units.
DWELLING_UNIT_INCOME_MIN: Final = Decimal("0.8")


class BasisError(ValueError):
    """An input the worksheet or the allocation refuses; the message says why."""


# --------------------------------------------------------------------------- acquisition costs


class CostCategory(StrEnum):
    """A settlement-statement line. Capitalized ones add to basis (Reg. §1.263(a)-2(f),
    Pub. 551 "Settlement costs"); the others are refused with the reason."""

    # Capitalized (facilitative).
    ABSTRACT_FEES = "abstract_fees"
    TITLE_SEARCH = "title_search"
    TITLE_INSURANCE_OWNER = "title_insurance_owner"
    LEGAL_FEES = "legal_fees"
    RECORDING_FEES = "recording_fees"
    SURVEY = "survey"
    TRANSFER_TAXES = "transfer_taxes"
    BROKER_COMMISSION = "broker_commission"
    APPRAISAL_BUYER = "appraisal_buyer"
    INSPECTION = "inspection"
    UTILITY_CONNECTION = "utility_connection"
    SELLER_OBLIGATIONS_PAID = "seller_obligations_paid"
    OTHER_FACILITATIVE = "other_facilitative"
    # Not capitalized.
    POINTS = "points"
    LOAN_ORIGINATION = "loan_origination"
    MORTGAGE_INSURANCE = "mortgage_insurance"
    LOAN_ASSUMPTION_FEE = "loan_assumption_fee"
    CREDIT_REPORT = "credit_report"
    APPRAISAL_LENDER = "appraisal_lender"
    PREPAID_INTEREST = "prepaid_interest"
    ESCROW_TAXES = "escrow_taxes"
    ESCROW_INSURANCE = "escrow_insurance"
    CASUALTY_INSURANCE = "casualty_insurance"
    PROPERTY_TAX_PRORATION = "property_tax_proration"
    PREPAID_RENT = "prepaid_rent"
    UTILITIES_BEFORE_CLOSING = "utilities_before_closing"
    REFINANCING_FEES = "refinancing_fees"


CAPITALIZED: Final[dict[CostCategory, str]] = {
    CostCategory.ABSTRACT_FEES: "Pub. 551: abstract fees",
    CostCategory.TITLE_SEARCH: "Reg. 1.263(a)-2(f)(2)(ii)(F): examining the title; Pub. 551",
    CostCategory.TITLE_INSURANCE_OWNER: "Pub. 551: owner's title insurance",
    CostCategory.LEGAL_FEES: "Reg. 1.263(a)-2(f)(2)(ii)(E): the documents of the acquisition; "
    "Pub. 551: legal fees (title search, contract, deed)",
    CostCategory.RECORDING_FEES: "Reg. 1.263(a)-2(f)(2)(ii)(H): title registration; Pub. 551",
    CostCategory.SURVEY: "Reg. 1.263(a)-2(f)(2)(ii)(J): survey services; Pub. 551",
    CostCategory.TRANSFER_TAXES: "Reg. 1.263(a)-2(f)(2)(ii)(H): sales and transfer taxes; Pub. 551",
    CostCategory.BROKER_COMMISSION: "Reg. 1.263(a)-2(f)(2)(ii)(I): brokers' commissions",
    CostCategory.APPRAISAL_BUYER: "Reg. 1.263(a)-2(f)(2)(ii)(B): an appraisal the buyer "
    "commissions to set the price",
    CostCategory.INSPECTION: "Reg. 1.263(a)-2(f)(2)(ii)(J): inspection services",
    CostCategory.UTILITY_CONNECTION: "Pub. 551: charges for installing utility services",
    CostCategory.SELLER_OBLIGATIONS_PAID: "Pub. 551: amounts the seller owed that the buyer pays "
    "(back taxes, interest, commissions)",
    CostCategory.OTHER_FACILITATIVE: "Reg. 1.263(a)-2(f)(1): an amount paid to facilitate the "
    "acquisition (describe it)",
}

NOT_CAPITALIZED: Final[dict[CostCategory, str]] = {
    CostCategory.POINTS: "points are prepaid interest, deducted over the loan's term (Pub. 527); "
    "a loan cost, not basis (Pub. 551)",
    CostCategory.LOAN_ORIGINATION: "a charge connected with getting a loan (Pub. 551)",
    CostCategory.MORTGAGE_INSURANCE: "a charge connected with getting a loan (Pub. 551)",
    CostCategory.LOAN_ASSUMPTION_FEE: "a charge connected with getting a loan (Pub. 551)",
    CostCategory.CREDIT_REPORT: "a charge connected with getting a loan (Pub. 551)",
    CostCategory.APPRAISAL_LENDER: "an appraisal required by a lender is a loan cost (Pub. 551)",
    CostCategory.PREPAID_INTEREST: "interest, deducted as paid or over the loan's term, not basis",
    CostCategory.ESCROW_TAXES: "amounts placed in escrow for future taxes are not settlement "
    "costs (Pub. 551)",
    CostCategory.ESCROW_INSURANCE: "amounts placed in escrow for future insurance are not "
    "settlement costs (Pub. 551)",
    CostCategory.CASUALTY_INSURANCE: "casualty insurance premiums are not basis (Pub. 551)",
    CostCategory.PROPERTY_TAX_PRORATION: "the buyer's share of the year's real estate taxes is "
    "deductible under §164 (Reg. 1.164-6), not basis",
    CostCategory.PREPAID_RENT: "rent for occupancy before closing is not basis (Pub. 551)",
    CostCategory.UTILITIES_BEFORE_CLOSING: "charges for utilities before closing are not basis "
    "(Pub. 551)",
    CostCategory.REFINANCING_FEES: "fees for refinancing a mortgage are not basis (Pub. 551)",
}

assert set(CAPITALIZED) | set(NOT_CAPITALIZED) == set(CostCategory)
assert not set(CAPITALIZED) & set(NOT_CAPITALIZED)


@dataclass(frozen=True, slots=True)
class AcquisitionCost:
    category: CostCategory
    amount: Decimal
    description: str | None = None


# --------------------------------------------------------------------------- land, use


class LandMethod(StrEnum):
    """Where the land figure comes from, in the spec's order of preference (5.2). The
    assessor's ratio is not a method (never alone: ATG p.64) and there is no residual."""

    APPRAISAL_SITE_VALUE = "appraisal_site_value"
    LAND_APPRAISAL = "land_appraisal"
    FLAT_PERCENTAGE = "flat_percentage"


LAND_METHOD_RANK: Final = {
    LandMethod.APPRAISAL_SITE_VALUE: 0,
    LandMethod.LAND_APPRAISAL: 1,
    LandMethod.FLAT_PERCENTAGE: 2,
}


@dataclass(frozen=True, slots=True)
class LandInputs:
    appraisal_site_value: Decimal | None = None
    appraisal_effective_date: date | None = None
    land_appraisal_value: Decimal | None = None
    land_appraisal_date: date | None = None
    assessor_land_value: Decimal | None = None
    assessor_improvement_value: Decimal | None = None
    # Percent of the purchase price (12 means 12 %), and where that percentage comes from.
    flat_percentage: Decimal | None = None
    flat_source: str | None = None


class PropertyUse(StrEnum):
    RESIDENTIAL_RENTAL = "residential_rental"
    NONRESIDENTIAL = "nonresidential"


RECOVERY_PERIOD: Final[dict[PropertyUse, Decimal]] = {
    PropertyUse.RESIDENTIAL_RENTAL: Decimal("27.5"),
    PropertyUse.NONRESIDENTIAL: Decimal("39"),
}


@dataclass(frozen=True, slots=True)
class UseInputs:
    use: PropertyUse
    # Share (0..1) of gross rental income from dwelling units (§168(e)(2)(A)(i)).
    dwelling_unit_income_share: Decimal | None = None
    # More than half the units let on a transient basis (hotel, motel, B&B): not dwelling
    # units (§168(e)(2)(A)(ii)).
    transient_lodging: bool = False


@dataclass(frozen=True, slots=True)
class BasisInputs:
    purchase_price: Decimal
    acquisition_costs: tuple[AcquisitionCost, ...]
    land_method: LandMethod
    land: LandInputs
    use: UseInputs
    placed_in_service_date: date
    acquisition_date: date | None = None


# --------------------------------------------------------------------------- checks


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True, slots=True)
class Check:
    code: str
    severity: Severity
    message: str


def _check(code: str, severity: Severity, message: str) -> Check:
    return Check(code, severity, message)


# --------------------------------------------------------------------------- the worksheet


@dataclass(frozen=True, slots=True)
class BasisWorksheet:
    purchase_price: Decimal
    acquisition_costs: tuple[AcquisitionCost, ...]
    capitalized_costs: Decimal  # CC
    land: Decimal
    land_method: LandMethod
    land_source: str
    building_basis: Decimal  # B = price − land
    total_basis: Decimal  # T = B + CC: the depreciable basis of spec 5.1
    property_use: PropertyUse
    recovery_period: Decimal
    placed_in_service_date: date
    acquisition_date: date | None
    checks: tuple[Check, ...]

    @property
    def errors(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.severity is Severity.ERROR)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _require_money(name: str, value: Decimal, *, positive: bool) -> Decimal:
    if not isinstance(value, Decimal):
        raise BasisError(f"{name} must be a Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise BasisError(f"{name} must be finite")
    if positive and value <= 0:
        raise BasisError(f"{name} must be positive")
    if not positive and value < 0:
        raise BasisError(f"{name} cannot be negative")
    if value != money(value):
        raise BasisError(f"{name} must be whole cents")
    return value


def _require_amount(name: str, value: Decimal) -> Decimal:
    """A non-negative finite Decimal at any precision (a cost model's RCN need not be cents)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise BasisError(f"{name} must be a finite Decimal")
    if value < 0:
        raise BasisError(f"{name} cannot be negative")
    return value


def capitalized_costs(costs: Iterable[AcquisitionCost]) -> tuple[AcquisitionCost, ...]:
    """The costs, each checked: a capitalized category and whole non-negative cents."""
    accepted: list[AcquisitionCost] = []
    for cost in costs:
        category = CostCategory(cost.category)
        if category in NOT_CAPITALIZED:
            reason = NOT_CAPITALIZED[category]
            raise BasisError(f"acquisition cost {category.value!r} is not capitalized: {reason}")
        amount = _require_money(f"acquisition cost {category.value!r}", cost.amount, positive=False)
        accepted.append(AcquisitionCost(category, amount, cost.description))
    return tuple(accepted)


def _choose_land(price: Decimal, method: LandMethod, land: LandInputs) -> tuple[Decimal, str]:
    if method is LandMethod.APPRAISAL_SITE_VALUE:
        if land.appraisal_site_value is None:
            raise BasisError("land method appraisal_site_value needs appraisal_site_value")
        value = _require_money("appraisal_site_value", land.appraisal_site_value, positive=True)
        when = f" as of {land.appraisal_effective_date}" if land.appraisal_effective_date else ""
        return value, f"the appraisal's opinion of site value{when}"
    if method is LandMethod.LAND_APPRAISAL:
        if land.land_appraisal_value is None:
            raise BasisError("land method land_appraisal needs land_appraisal_value")
        value = _require_money("land_appraisal_value", land.land_appraisal_value, positive=True)
        when = f" as of {land.land_appraisal_date}" if land.land_appraisal_date else ""
        return value, f"a land appraisal{when}"
    if land.flat_percentage is None:
        raise BasisError("land method flat_percentage needs flat_percentage")
    if not land.flat_source or not land.flat_source.strip():
        raise BasisError("land method flat_percentage needs flat_source (ATG ch. 3 C.6)")
    pct = land.flat_percentage
    if not isinstance(pct, Decimal) or not pct.is_finite() or not 0 < pct < 100:
        raise BasisError("flat_percentage must be a Decimal percent between 0 and 100")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        value = money(price * pct / HUNDRED)
    return value, f"{pct.normalize()} % of the purchase price ({land.flat_source.strip()})"


def _land_checks(
    price: Decimal,
    method: LandMethod,
    land: LandInputs,
    chosen: Decimal,
    reference_date: date | None,
    *,
    tolerance: Decimal,
) -> list[Check]:
    checks: list[Check] = []
    with localcontext() as ctx:
        ctx.prec = PRECISION
        share = chosen / price
        if LAND_SHARE_MIN <= share <= LAND_SHARE_MAX:
            checks.append(
                _check(
                    "land_share_of_price",
                    Severity.OK,
                    f"land is {_pct(share)} of the price (expected 5–60 %)",
                )
            )
        else:
            checks.append(
                _check(
                    "land_share_of_price",
                    Severity.WARNING,
                    f"land is {_pct(share)} of the price, outside 5–60 %",
                )
            )

        # Hierarchy: the preferred source, when available, should be the one used.
        available = [
            m
            for m, value in (
                (LandMethod.APPRAISAL_SITE_VALUE, land.appraisal_site_value),
                (LandMethod.LAND_APPRAISAL, land.land_appraisal_value),
            )
            if value is not None
        ]
        preferred = min(available, key=LAND_METHOD_RANK.__getitem__, default=None)
        if preferred is not None and LAND_METHOD_RANK[preferred] < LAND_METHOD_RANK[method]:
            checks.append(
                _check(
                    "land_hierarchy",
                    Severity.WARNING,
                    f"{preferred.value} is available and preferred over {method.value} "
                    "(ATG p.37, p.64); record why it was not used",
                )
            )
        else:
            checks.append(
                _check(
                    "land_hierarchy", Severity.OK, f"{method.value} is the best available source"
                )
            )

        if method is LandMethod.FLAT_PERCENTAGE:
            checks.append(
                _check(
                    "land_flat_percentage",
                    Severity.WARNING,
                    "a flat percentage is the ATG's rule-of-thumb approach (ch. 3 C.6): the "
                    f"source is recorded ({(land.flat_source or '').strip()}) but it is not an "
                    "opinion of value",
                )
            )

        # Against the appraisal's site value (when it exists and was not the source).
        site = land.appraisal_site_value
        if site is None:
            checks.append(
                _check(
                    "land_vs_appraisal_site_value",
                    Severity.NOT_CHECKED,
                    "the appraisal gives no site value (cost approach not developed): "
                    "land_value_not_in_appraisal",
                )
            )
        elif method is LandMethod.APPRAISAL_SITE_VALUE:
            checks.append(
                _check("land_vs_appraisal_site_value", Severity.OK, "land is the site value")
            )
        else:
            diff = (chosen - site) / site
            severity = Severity.OK if abs(diff) <= tolerance else Severity.WARNING
            checks.append(
                _check(
                    "land_vs_appraisal_site_value",
                    severity,
                    f"land {chosen} differs from the appraisal's site value {site} by "
                    f"{_pct(diff, signed=True)} (tolerance {_pct(tolerance)})",
                )
            )

        # The assessor's ratio, only ever as a check (ATG p.64).
        if land.assessor_land_value is None or land.assessor_improvement_value is None:
            checks.append(
                _check(
                    "land_vs_assessor_ratio",
                    Severity.NOT_CHECKED,
                    "no assessor land and improvement values",
                )
            )
        else:
            a_land = _require_money("assessor_land_value", land.assessor_land_value, positive=False)
            a_impr = _require_money(
                "assessor_improvement_value", land.assessor_improvement_value, positive=False
            )
            total = a_land + a_impr
            if total <= 0:
                raise BasisError("assessor land and improvement values cannot both be zero")
            implied = money(price * a_land / total)
            diff = (chosen - implied) / implied if implied > 0 else ONE
            severity = Severity.OK if abs(diff) <= tolerance else Severity.WARNING
            checks.append(
                _check(
                    "land_vs_assessor_ratio",
                    severity,
                    f"the assessor's ratio {_pct(a_land / total)} of the price implies land "
                    f"{implied}; the chosen land differs by {_pct(diff, signed=True)}",
                )
            )

        # The appraisal's effective date against the acquisition (value as of the purchase).
        when = (
            land.appraisal_effective_date
            if method is LandMethod.APPRAISAL_SITE_VALUE
            else land.land_appraisal_date
            if method is LandMethod.LAND_APPRAISAL
            else None
        )
        if method is LandMethod.FLAT_PERCENTAGE:
            checks.append(
                _check("land_value_date", Severity.NOT_CHECKED, "a flat percentage has no date")
            )
        elif when is None or reference_date is None:
            checks.append(
                _check(
                    "land_value_date",
                    Severity.NOT_CHECKED,
                    "the value's date or the acquisition date is missing",
                )
            )
        else:
            days = abs((reference_date - when).days)
            severity = Severity.OK if days <= APPRAISAL_WINDOW_DAYS else Severity.WARNING
            checks.append(
                _check(
                    "land_value_date",
                    severity,
                    f"the land value is dated {when}, {days} days from {reference_date} "
                    f"(window {APPRAISAL_WINDOW_DAYS} days)",
                )
            )
    return checks


def _use_checks(use: UseInputs) -> tuple[Decimal, list[Check]]:
    checks: list[Check] = []
    share = use.dwelling_unit_income_share
    if share is not None:
        if not isinstance(share, Decimal) or not share.is_finite() or not ZERO <= share <= ONE:
            raise BasisError("dwelling_unit_income_share must be a Decimal between 0 and 1")
    qualifies = (
        share is not None and share >= DWELLING_UNIT_INCOME_MIN and not use.transient_lodging
    )
    if use.use is PropertyUse.RESIDENTIAL_RENTAL:
        if use.transient_lodging:
            raise BasisError(
                "residential_rental cannot be transient lodging: units let on a transient basis "
                "are not dwelling units (§168(e)(2)(A)(ii)); the use is nonresidential (39 years)"
            )
        if share is None:
            raise BasisError(
                "residential_rental needs dwelling_unit_income_share (the 80 % test of "
                "§168(e)(2)(A))"
            )
        if share < DWELLING_UNIT_INCOME_MIN:
            raise BasisError(
                f"residential_rental fails the 80 % test: {_pct(share)} of gross rental income "
                "from dwelling units (§168(e)(2)(A)); the use is nonresidential (39 years)"
            )
        checks.append(
            _check(
                "recovery_period",
                Severity.OK,
                f"residential rental property, 27.5 years: {_pct(share)} of gross rental income "
                "from dwelling units, not transient (§168(e)(2)(A))",
            )
        )
    elif qualifies:
        checks.append(
            _check(
                "recovery_period",
                Severity.WARNING,
                f"declared nonresidential (39 years) although {_pct(share or ZERO)} of gross "
                "rental income is from dwelling units and none is transient: that is residential "
                "rental property (27.5 years) under §168(e)(2)(A)",
            )
        )
    else:
        why = (
            "transient lodging"
            if use.transient_lodging
            else "no dwelling-unit income share given"
            if share is None
            else f"{_pct(share)} of gross rental income from dwelling units"
        )
        checks.append(
            _check(
                "recovery_period",
                Severity.OK,
                f"nonresidential real property, 39 years ({why}; §168(e)(2)(B))",
            )
        )
    return RECOVERY_PERIOD[use.use], checks


def worksheet(inputs: BasisInputs, *, land_tolerance: Decimal = LAND_TOLERANCE) -> BasisWorksheet:
    """The basis worksheet (spec 5.1 and 5.2) with every check, or ``BasisError``."""
    price = _require_money("purchase_price", inputs.purchase_price, positive=True)
    costs = capitalized_costs(inputs.acquisition_costs)
    cc = sum((c.amount for c in costs), ZERO)
    land, source = _choose_land(price, inputs.land_method, inputs.land)
    if land >= price:
        raise BasisError(f"land {land} is not below the purchase price {price}")
    building = price - land
    total = building + cc
    checks = _land_checks(
        price,
        inputs.land_method,
        inputs.land,
        land,
        inputs.acquisition_date or inputs.placed_in_service_date,
        tolerance=land_tolerance,
    )
    period, use_checks = _use_checks(inputs.use)
    checks += use_checks
    if (
        inputs.acquisition_date is not None
        and inputs.placed_in_service_date < inputs.acquisition_date
    ):
        checks.append(
            _check(
                "placed_in_service_date",
                Severity.WARNING,
                f"placed in service {inputs.placed_in_service_date} is before the acquisition "
                f"{inputs.acquisition_date}",
            )
        )
    else:
        checks.append(
            _check(
                "placed_in_service_date",
                Severity.OK,
                f"placed in service {inputs.placed_in_service_date} (ready and available for "
                "its use; mid-month convention)",
            )
        )
    return BasisWorksheet(
        purchase_price=price,
        acquisition_costs=costs,
        capitalized_costs=money(cc),
        land=land,
        land_method=inputs.land_method,
        land_source=source,
        building_basis=money(building),
        total_basis=money(total),
        property_use=inputs.use.use,
        recovery_period=period,
        placed_in_service_date=inputs.placed_in_service_date,
        acquisition_date=inputs.acquisition_date,
        checks=tuple(checks),
    )


def _pct(share: Decimal, *, signed: bool = False) -> str:
    value = (share * HUNDRED).quantize(TENTH_PERCENT, rounding=ROUND_HALF_UP)
    return f"{value:+} %" if signed else f"{value} %"


# --------------------------------------------------------------------------- the allocation

# The design partner's twelve building systems (page 3 of their study; the row names are in
# COMPONENT_TAXONOMY.md). A schedule whose rows all carry a system can be regrouped by trade.
SystemWeights = tuple[tuple[str, Decimal], ...]

MISCELLANEOUS: Final = "Miscellaneous Building Components"
HALF_WALL_SPLIT: Final[SystemWeights] = (
    ("Interior Framing / Partitions", Decimal("0.8")),
    ("Painting", Decimal("0.2")),
)
REPORT_ROW_SYSTEMS: Final[dict[str, SystemWeights]] = {
    "Ceiling Systems": (("Ceiling Systems", ONE),),
    "Doors & Frames": (("Doors and Frames", ONE),),
    "Overhead Doors": (("Doors and Frames", ONE),),
    "Electrical": (("Electrical", ONE),),
    "Specialty Electrical - Appliances/Electric Fireplace": (("Electrical", ONE),),
    "Communication / Data": (("Electrical", ONE),),
    "Security / Exterior Lighting": (("Electrical", ONE),),
    "Fire Protection & Alarm": (("Fire Protection & Alarm", ONE),),
    "HVAC": (("HVAC", ONE),),
    "Specialty HVAC - Dryer Vents": (("HVAC", ONE),),
    "Interior Framing / Partitions": (("Interior Framing / Partitions", ONE),),
    "Interior Half Walls": HALF_WALL_SPLIT,
    "Painting": (("Painting", ONE),),
    "Plumbing": (("Plumbing", ONE),),
    "Specialty Plumbing - Kitchen & Utility Sink / Laundry": (("Plumbing", ONE),),
    "Roofing Systems": (("Roofing Systems", ONE),),
    "Access Control": (("Security", ONE),),
    "Windows": (("Windows", ONE),),
    **{
        row: ((MISCELLANEOUS, ONE),)
        for row in (
            "Cabinets / Millwork",
            "Moldings",
            "Fireplaces",
            "Raised Wood Paneling",
            "Flooring - Carpet",
            "Wood Flooring",
            "Wall Coverings",
            "Door & Window Guards",
            "Window Treatments",
            "Appliances",
            "FF&E",
            "Movable Shed",
            "Driveway",
            "Exterior Signage",
            "Sidewalks / Curbs",
            "Exterior Fencing",
            "Retaining Walls",
            "Exterior Equipment Concrete Pads",
            "Foundations",
            "Exterior Façade / Building Skin",
            "Gutter Systems",
            "Structural Framing",
            "Site Utilities",
            "Stairs / Balconies",
            "Flooring - Sealed Concrete",
            "Bathroom Accessories",
            "Ceramic Tile Flooring",
            "Cabinets / Millwork - Restrooms",
        )
    },
}

BUCKET_ORDER: Final = ("y5", "y7", "y15", "y27_5", "y39")


@dataclass(frozen=True, slots=True)
class RcnRow:
    """One schedule line at replacement cost new. ``systems`` maps it to building systems
    with weights summing to 1 (empty: not mapped; then no regrouping by system)."""

    id: str
    bucket: str
    rcn: Decimal
    label: str | None = None
    systems: SystemWeights = ()
    # Replacement cost new less depreciation (the structure model); required when the
    # allocation is depreciated, ignored otherwise.
    rcnld: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AllocatedRow:
    id: str
    bucket: str
    label: str | None
    rcn: Decimal
    allocated: Decimal
    # Cents added (+) or removed (−) against plain half-up rounding so the rows sum to T.
    rounding_cents: int
    rcnld: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BucketTotal:
    bucket: str
    rows: int
    rcn: Decimal
    allocated: Decimal
    percent: Decimal  # of T, to 0.1
    share: Decimal  # of T, to 6 places
    building: Decimal  # B × allocated / T (largest remainder across buckets)
    closing_costs: Decimal  # allocated − building
    rcnld: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SystemTotal:
    system: str
    rcn: Decimal
    allocated: Decimal
    rcnld: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Allocation:
    building_basis: Decimal
    closing_costs: Decimal
    total: Decimal
    rcn_total: Decimal
    factor: Decimal  # T / ΣRCN to 9 places (the computation used full precision)
    rows: tuple[AllocatedRow, ...]
    by_bucket: tuple[BucketTotal, ...]
    by_system: tuple[SystemTotal, ...] | None
    by_system_reason: str | None
    checks: tuple[Check, ...]
    rows_sum: Decimal  # == total, asserted
    residual_cents: int = field(default=0)
    # The depreciated schedule's total and whether the factor scaled RCNLD (else RCN).
    rcnld_total: Decimal | None = None
    depreciated: bool = False


def _largest_remainder(exact: Sequence[Decimal], total: Decimal) -> tuple[list[Decimal], list[int]]:
    """Round ``exact`` to cents so they sum to ``total``: half up, then the residual cents go
    one each to the largest remainders (ties by order). Returns the values and each value's
    cents against plain half-up rounding."""
    half_up = [money(x) for x in exact]
    floors = [x.quantize(CENT, rounding=ROUND_DOWN) for x in exact]
    residual = int(((total - sum(floors, ZERO)) / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    if residual < 0 or residual > len(exact):
        raise BasisError(
            f"cannot reconcile: the rounded-down parts leave {residual} cents for {len(exact)} rows"
        )
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    values = list(floors)
    for i in order[:residual]:
        values[i] = floors[i] + CENT
    cents = [
        int(((v - h) / CENT).to_integral_value()) for v, h in zip(values, half_up, strict=True)
    ]
    return values, cents


def _split(amount: Decimal, weights: SystemWeights) -> list[tuple[str, Decimal]]:
    parts: list[tuple[str, Decimal]] = []
    given = ZERO
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for system, weight in weights[:-1]:
            part = money(amount * weight)
            parts.append((system, part))
            given += part
    parts.append((weights[-1][0], amount - given))
    return parts


def allocate(
    rows: Iterable[RcnRow],
    building_basis: Decimal,
    closing_costs: Decimal,
    *,
    depreciated: bool = False,
) -> Allocation:
    """Scale a schedule to ``T = B + CC`` with one factor (on each row's RCNLD when
    ``depreciated``, else on its RCN); totals, percentages, the pro-rata block, the
    regrouping by system when every row is mapped; exact reconciliation."""
    b = _require_money("building_basis", building_basis, positive=True)
    cc = _require_money("closing_costs", closing_costs, positive=False)
    total = b + cc
    rows = tuple(rows)
    if not rows:
        raise BasisError("the schedule has no rows")
    seen: set[str] = set()
    for row in rows:
        if row.id in seen:
            raise BasisError(f"duplicate row id {row.id!r}")
        seen.add(row.id)
        if row.bucket not in BUCKET_ORDER:
            raise BasisError(
                f"row {row.id!r} has bucket {row.bucket!r}; land is not depreciable and is not "
                "a schedule line (expected one of y5, y7, y15, y27_5, y39)"
            )
        _require_amount(f"row {row.id!r} rcn", row.rcn)
        if depreciated:
            if row.rcnld is None:
                raise BasisError(f"row {row.id!r} has no rcnld; a depreciated allocation needs one")
            _require_amount(f"row {row.id!r} rcnld", row.rcnld)
        if row.systems and sum((w for _, w in row.systems), ZERO) != ONE:
            raise BasisError(f"row {row.id!r} system weights do not sum to 1")
    rcn_total = sum((r.rcn for r in rows), ZERO)
    if rcn_total <= 0:
        raise BasisError("the schedule's replacement cost new is zero")
    bases = [(r.rcnld if depreciated and r.rcnld is not None else r.rcn) for r in rows]
    base_total = sum(bases, ZERO)
    if base_total <= 0:
        raise BasisError("the schedule's replacement cost new less depreciation is zero")

    with localcontext() as ctx:
        ctx.prec = PRECISION
        factor = total / base_total
        exact = [base * factor for base in bases]
    values, cents = _largest_remainder(exact, total)
    allocated = tuple(
        AllocatedRow(r.id, r.bucket, r.label, r.rcn, v, c, r.rcnld if depreciated else None)
        for r, v, c in zip(rows, values, cents, strict=True)
    )
    rows_sum = sum((r.allocated for r in allocated), ZERO)
    if rows_sum != total:
        raise BasisError(f"reconciliation failed: rows sum to {rows_sum}, total is {total}")

    buckets = [bk for bk in BUCKET_ORDER if any(r.bucket == bk for r in rows)]
    bucket_allocated = {
        bk: sum((r.allocated for r in allocated if r.bucket == bk), ZERO) for bk in buckets
    }
    with localcontext() as ctx:
        ctx.prec = PRECISION
        building_exact = [b * bucket_allocated[bk] / total for bk in buckets]
        shares = {bk: bucket_allocated[bk] / total for bk in buckets}
    building_parts, _ = _largest_remainder(building_exact, b)
    by_bucket = tuple(
        BucketTotal(
            bucket=bk,
            rows=sum(r.bucket == bk for r in rows),
            rcn=sum((r.rcn for r in rows if r.bucket == bk), ZERO),
            allocated=bucket_allocated[bk],
            percent=(shares[bk] * HUNDRED).quantize(TENTH_PERCENT, rounding=ROUND_HALF_UP),
            share=shares[bk].quantize(SHARE_PLACES, rounding=ROUND_HALF_UP),
            building=part,
            closing_costs=bucket_allocated[bk] - part,
            rcnld=(
                sum((r.rcnld or ZERO for r in rows if r.bucket == bk), ZERO)
                if depreciated
                else None
            ),
        )
        for bk, part in zip(buckets, building_parts, strict=True)
    )
    if sum((t.building for t in by_bucket), ZERO) != b:
        raise BasisError("reconciliation failed: the building column does not sum to B")
    if sum((t.closing_costs for t in by_bucket), ZERO) != cc:
        raise BasisError("reconciliation failed: the closing-costs column does not sum to CC")

    by_system: tuple[SystemTotal, ...] | None = None
    reason: str | None = None
    unmapped = [r.id for r in rows if not r.systems]
    if unmapped:
        reason = (
            f"{len(unmapped)} of {len(rows)} rows are not mapped to a building system; the "
            "schedule is grouped by bucket only"
        )
    else:
        rcn_by: dict[str, Decimal] = {}
        rcnld_by: dict[str, Decimal] = {}
        allocated_by: dict[str, Decimal] = {}
        for r, a in zip(rows, allocated, strict=True):
            for system, part in _split(r.rcn, r.systems):
                rcn_by[system] = rcn_by.get(system, ZERO) + part
            if depreciated and r.rcnld is not None:
                for system, part in _split(r.rcnld, r.systems):
                    rcnld_by[system] = rcnld_by.get(system, ZERO) + part
            for system, part in _split(a.allocated, r.systems):
                allocated_by[system] = allocated_by.get(system, ZERO) + part
        by_system = tuple(
            SystemTotal(
                system,
                rcn_by[system],
                allocated_by[system],
                rcnld_by.get(system, ZERO) if depreciated else None,
            )
            for system in sorted(rcn_by)
        )
        if sum((s.allocated for s in by_system), ZERO) != total:
            raise BasisError("reconciliation failed: the systems do not sum to T")

    checks = [
        _rcnld_factor_check(factor, base_total, total)
        if depreciated
        else _uniform_factor_check(factor, rcn_total, total)
    ]
    return Allocation(
        building_basis=b,
        closing_costs=cc,
        total=total,
        rcn_total=rcn_total,
        factor=factor.quantize(FACTOR_PLACES, rounding=ROUND_HALF_UP),
        rows=allocated,
        by_bucket=by_bucket,
        by_system=by_system,
        by_system_reason=reason,
        checks=tuple(checks),
        rows_sum=rows_sum,
        residual_cents=sum(abs(c) for c in cents),
        rcnld_total=base_total if depreciated else None,
        depreciated=depreciated,
    )


# The reconciliation's tolerance (docs/review_2026-10-15/STRUCTURE_MODEL.md section 3.8):
# a depreciated schedule is allocated only when its total is within this of the basis.
RCNLD_FACTOR_TOLERANCE: Final = Decimal("0.10")


def _rcnld_factor_check(factor: Decimal, rcnld_total: Decimal, total: Decimal) -> Check:
    shown = factor.quantize(FACTOR_PLACES, rounding=ROUND_HALF_UP)
    within = abs(factor - ONE) <= RCNLD_FACTOR_TOLERANCE
    return _check(
        "rcnld_factor",
        Severity.OK if within else Severity.WARNING,
        f"the schedule's replacement cost new less depreciation {rcnld_total} is allocated to "
        f"the basis {total} pro rata (factor {shown}; "
        f"{'within' if within else 'outside'} {RCNLD_FACTOR_TOLERANCE * HUNDRED} % of 1, the "
        "square-foot method's accuracy, STRUCTURE_MODEL.md 3.8)",
    )


def _uniform_factor_check(factor: Decimal, rcn_total: Decimal, total: Decimal) -> Check:
    shown = factor.quantize(FACTOR_PLACES, rounding=ROUND_HALF_UP)
    if factor > ONE:
        return _check(
            "uniform_factor",
            Severity.ERROR,
            f"the schedule's replacement cost new {rcn_total} is below the basis {total}: "
            f"scaling every row up by {shown} is the pro-rata step-up the ATG rejects "
            "(ch. 3 D(3)–(5)); the schedule is missing items (see not_produced)",
        )
    if factor < ONE:
        return _check(
            "uniform_factor",
            Severity.WARNING,
            f"one factor {shown} writes every row down by the same {_pct(ONE - factor)} in "
            "place of per-item depreciation for age and condition (ATG ch. 3 A(6))",
        )
    return _check("uniform_factor", Severity.OK, "the schedule equals the basis; no scaling")


__all__ = [
    "APPRAISAL_WINDOW_DAYS",
    "BUCKET_ORDER",
    "CAPITALIZED",
    "CENT",
    "DWELLING_UNIT_INCOME_MIN",
    "HALF_WALL_SPLIT",
    "LAND_SHARE_MAX",
    "LAND_SHARE_MIN",
    "LAND_TOLERANCE",
    "MISCELLANEOUS",
    "NOT_CAPITALIZED",
    "RCNLD_FACTOR_TOLERANCE",
    "RECOVERY_PERIOD",
    "REPORT_ROW_SYSTEMS",
    "AcquisitionCost",
    "AllocatedRow",
    "Allocation",
    "BasisError",
    "BasisInputs",
    "BasisWorksheet",
    "BucketTotal",
    "Check",
    "CostCategory",
    "LandInputs",
    "LandMethod",
    "PropertyUse",
    "RcnRow",
    "Severity",
    "SystemTotal",
    "SystemWeights",
    "UseInputs",
    "allocate",
    "capitalized_costs",
    "money",
    "worksheet",
]
