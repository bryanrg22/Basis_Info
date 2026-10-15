"""Cross-stage checks: classification vs takeoff vs cost for one asset.

Migrated from ``agentic/validation/cross_validator.py``. Issues with severity ``warning``
or ``error`` set ``needs_review`` on the asset (S3_SPEC section 5); ``info`` issues are
recorded only.

Changes from legacy, each a bug fix:

* Checks run per asset. Legacy matched takeoffs and costs to classifications by component
  *name*, so two carpets in two rooms shared one takeoff.
* The unit/bucket table is keyed by the ``Bucket`` enum. Legacy matched ``"5_year"`` as a
  substring and so applied the 5-year row to 15- and 27.5-year buckets.
* "No RSMeans line item" becomes ``COST_ROW_MISSING`` (a warning) when there is no cost
  estimate at all; the legacy check fired on every static estimate because the static
  path never set a line item.
* Cost ranges are looked up by the takeoff row, not by substring of the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from basis.db.enums import Bucket
from basis.domain.costs import CostEstimate
from basis.domain.takeoffs import SF_PER_SY, Takeoff
from basis.domain.validators import Issue

SECTION_1245_SF_THRESHOLD: Final = Decimal(100)

BUCKET_TYPICAL_UNITS: Final[dict[Bucket, frozenset[str]]] = {
    Bucket.Y5: frozenset({"EA", "LF"}),
    Bucket.Y7: frozenset({"EA", "LF"}),
    Bucket.Y15: frozenset({"SF", "LF", "EA"}),
    Bucket.Y27_5: frozenset({"SF"}),
    Bucket.Y39: frozenset({"SF", "SY", "LF"}),
}


@dataclass(frozen=True, slots=True)
class CostRange:
    low: Decimal
    high: Decimal
    unit: str


COST_RANGES: Final[dict[str, CostRange]] = {
    name: CostRange(Decimal(low), Decimal(high), unit)
    for name, (low, high, unit) in {
        "light_fixture": ("20", "1000", "EA"),
        "electrical_outlet": ("15", "150", "EA"),
        "hvac_unit": ("1000", "15000", "EA"),
        "carpet": ("2", "30", "SF"),
        "tile": ("3", "50", "SF"),
        "cabinet": ("80", "800", "LF"),
        "countertop": ("20", "250", "SF"),
        "door": ("150", "3000", "EA"),
        "window": ("200", "2500", "EA"),
        "plumbing_fixture": ("100", "5000", "EA"),
        "paint": ("1", "8", "SF"),
        "fire_sprinkler": ("50", "300", "EA"),
    }.items()
}
# A unit cost below half the range minimum or above twice the maximum is flagged.
LOW_TOLERANCE: Final = Decimal("0.5")
HIGH_TOLERANCE: Final = Decimal(2)


def check_classification_takeoff(
    section: str | None, bucket: Bucket, takeoff: Takeoff
) -> list[Issue]:
    issues: list[Issue] = []
    if section == "1245" and takeoff.unit == "SF" and takeoff.quantity > SECTION_1245_SF_THRESHOLD:
        issues.append(
            Issue(
                "SECTION_1245_SF_LARGE",
                "warning",
                f"section 1245 with {takeoff.quantity} SF is unusual; 1245 property is "
                "usually equipment or removable finishes, not large areas",
            )
        )
    if bucket is Bucket.Y39 and takeoff.unit == "EA":
        issues.append(
            Issue(
                "39_YEAR_EA_UNIT",
                "info",
                "39-year property measured in EA; building components are usually SF/LF",
            )
        )
    typical = BUCKET_TYPICAL_UNITS.get(bucket)
    if typical is not None and takeoff.unit not in typical:
        issues.append(
            Issue(
                "UNIT_BUCKET_MISMATCH",
                "info",
                f"unit {takeoff.unit} is unusual for {bucket.value}; typical: "
                f"{', '.join(sorted(typical))}",
            )
        )
    return issues


def check_takeoff_cost(takeoff: Takeoff, cost: CostEstimate | None) -> list[Issue]:
    if cost is None:
        return [
            Issue(
                "COST_ROW_MISSING",
                "warning",
                f"no cost row for {takeoff.component!r}; the cost needs a manual estimate",
            )
        ]
    issues: list[Issue] = []
    expected = COST_RANGES.get(takeoff.component)
    if expected is not None:
        base = cost.base_unit_cost
        if cost.unit == "SY" and expected.unit == "SF":
            base = base / SF_PER_SY
        comparable = cost.unit == expected.unit or (cost.unit == "SY" and expected.unit == "SF")
        if comparable and base > 0:
            if base < expected.low * LOW_TOLERANCE:
                issues.append(
                    Issue(
                        "COST_BELOW_RANGE",
                        "warning",
                        f"unit cost {base:.2f}/{expected.unit} is far below the typical "
                        f"{expected.low}-{expected.high}",
                    )
                )
            elif base > expected.high * HIGH_TOLERANCE:
                issues.append(
                    Issue(
                        "COST_ABOVE_RANGE",
                        "warning",
                        f"unit cost {base:.2f}/{expected.unit} is far above the typical "
                        f"{expected.low}-{expected.high}",
                    )
                )
    if takeoff.quantity > 0 and cost.unit_cost > 0:
        recomputed = cost.unit_cost * takeoff.quantity
        if abs(recomputed - cost.total) > Decimal("0.01"):
            issues.append(
                Issue(
                    "COST_TOTAL_MISMATCH",
                    "error",
                    f"total {cost.total} != unit cost {cost.unit_cost} x {takeoff.quantity}",
                )
            )
    multiplier = cost.year_factor * cost.location_factor
    if abs(multiplier - 1) > Decimal("0.2"):
        issues.append(
            Issue(
                "COST_MULTIPLIER_DETECTED",
                "info",
                f"unit cost includes a x{multiplier:.3f} year/location adjustment",
            )
        )
    return issues


def cross_validate(
    *,
    section: str | None,
    bucket: Bucket,
    takeoff: Takeoff,
    cost: CostEstimate | None,
) -> list[Issue]:
    """Every cross-stage check for one asset, in a fixed order."""
    return [
        *check_classification_takeoff(section, bucket, takeoff),
        *check_takeoff_cost(takeoff, cost),
    ]
