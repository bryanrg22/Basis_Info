"""The deterministic tail of a cost segregation schedule (WORKFLOW_REVIEW_2026-10-29 section 2).

Once a study has a replacement cost new (RCN) and a recovery bucket for every schedule row,
a building basis B and a closing-cost figure CC, every printed number of the engineer's
deliverable follows by arithmetic:

* total T = B + CC;
* one factor f = T / sum(RCN); every row = RCN x f, to the cent;
* bucket totals (sums of rows) and their percentages of T (one decimal);
* a pro-rata allocation block: building_b = B x bucket_b / T and closing_b = CC x
  bucket_b / T, to the cent, for every bucket, each column footed to B and to CC by
  charging its rounding residual (a cent or two) to the largest bucket (the rule the
  engineer's printed block follows; largest-remainder apportionment would move the cent
  to another bucket and does not reproduce it);
* a building-systems page regrouping the same rows by trade: each system's depreciable
  cost is the sum of its rows' (scaled) costs and its replacement cost the sum of their
  RCNs; a row may be split between systems by shares.

Everything here is ``Decimal`` with ``ROUND_HALF_UP`` at cents (percentages at tenths).
Rounding each row to the cent can leave the rows a few cents short of or over T: the
``residual`` is reported and, with ``absorb_residual``, charged to the largest row so the
schedule foots exactly. :func:`implied_rcn` inverts the chain: from printed row costs, T
and the printed sum of RCNs back to the RCNs, which is how a printed report is reproduced
when it does not print its RCNs per row (OUTPUT_REPORT_ANATOMY section 2 does this).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

CENT: Final = Decimal("0.01")
TENTH: Final = Decimal("0.1")
ZERO: Final = Decimal("0")
BUCKET_ORDER: Final[tuple[str, ...]] = ("y5", "y7", "y15", "y27_5", "y39")


class ScheduleError(ValueError):
    pass


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def tenths(value: Decimal) -> Decimal:
    return value.quantize(TENTH, rounding=ROUND_HALF_UP)


def ordered_buckets(buckets: Iterable[str]) -> list[str]:
    """Buckets in recovery-period order; unknown names last, alphabetically."""
    known = {b: i for i, b in enumerate(BUCKET_ORDER)}
    return sorted(set(buckets), key=lambda b: (known.get(b, len(known)), b))


@dataclass(frozen=True)
class Schedule:
    """Every number of the schedule pages, from the inputs (see the module docstring)."""

    building_basis: Decimal
    closing_costs: Decimal
    total: Decimal  # T = B + CC
    rcn_total: Decimal
    factor: Decimal  # f = T / rcn_total, unrounded
    rows: dict[str, Decimal]  # row id -> RCN x f, cents (residual absorbed when asked)
    buckets: dict[str, str]  # row id -> bucket
    residual: Decimal  # T - sum(rows) before absorbing
    bucket_totals: dict[str, Decimal]
    bucket_percent: dict[str, Decimal]  # of T, tenths
    allocation_building: dict[str, Decimal]  # B x bucket / T, cents, column footed to B
    allocation_closing: dict[str, Decimal]  # CC x bucket / T, cents, column footed to CC
    # The cents each column's largest bucket absorbed so the column foots (0 when none).
    allocation_adjustment: dict[str, Decimal] = field(default_factory=dict)
    absorbed_by: str | None = None

    @property
    def bucket_order(self) -> list[str]:
        return ordered_buckets(self.bucket_totals)

    @property
    def allocation_total(self) -> dict[str, Decimal]:
        """Building + closing per bucket; equals the bucket total except by rounding."""
        return {
            b: self.allocation_building[b] + self.allocation_closing[b] for b in self.bucket_order
        }

    @property
    def allocation_residuals(self) -> dict[str, Decimal]:
        """How far each bucket's allocation pair (building + closing) is from its bucket
        total, in cents: zero except where the two columns' footing cents disagree."""
        return {b: self.bucket_totals[b] - self.allocation_total[b] for b in self.bucket_order}


def build_schedule(
    rcn: Mapping[str, Decimal],
    buckets: Mapping[str, str],
    building_basis: Decimal,
    closing_costs: Decimal,
    *,
    absorb_residual: bool = False,
) -> Schedule:
    """The schedule for ``rcn`` (row id -> replacement cost new) with ``buckets`` (row id
    -> bucket), B and CC. Rows with a zero RCN are kept (they print as 0.00)."""
    if set(rcn) != set(buckets):
        missing = sorted(set(rcn) ^ set(buckets))
        raise ScheduleError(f"rows with an RCN and rows with a bucket differ: {missing}")
    if not rcn:
        raise ScheduleError("no rows")
    negative = sorted(k for k, v in rcn.items() if v < 0)
    if negative:
        raise ScheduleError(f"negative RCN: {negative}")
    if building_basis < 0 or closing_costs < 0:
        raise ScheduleError("basis and closing costs must not be negative")
    rcn_total = sum(rcn.values(), ZERO)
    if rcn_total == 0:
        raise ScheduleError("the RCNs sum to zero; no factor")
    total = cents(building_basis + closing_costs)
    factor = total / rcn_total
    rows = {k: cents(v * factor) for k, v in rcn.items()}
    residual = total - sum(rows.values(), ZERO)
    absorbed_by = None
    if absorb_residual and residual != 0:
        absorbed_by = max(rows, key=lambda k: (rows[k], k))
        rows[absorbed_by] += residual
    bucket_totals: dict[str, Decimal] = {}
    for k, v in rows.items():
        bucket_totals[buckets[k]] = bucket_totals.get(buckets[k], ZERO) + v
    order = ordered_buckets(bucket_totals)
    bucket_totals = {b: bucket_totals[b] for b in order}
    bucket_percent = {
        b: tenths(v * 100 / total) if total else ZERO for b, v in bucket_totals.items()
    }
    alloc_b, adjust_b = _footed_column(building_basis, bucket_totals, total)
    alloc_c, adjust_c = _footed_column(closing_costs, bucket_totals, total)
    return Schedule(
        building_basis=building_basis,
        closing_costs=closing_costs,
        total=total,
        rcn_total=rcn_total,
        factor=factor,
        rows=rows,
        buckets=dict(buckets),
        residual=residual,
        bucket_totals=bucket_totals,
        bucket_percent=bucket_percent,
        allocation_building=alloc_b,
        allocation_closing=alloc_c,
        allocation_adjustment={"building": adjust_b, "closing_costs": adjust_c},
        absorbed_by=absorbed_by,
    )


def _footed_column(
    amount: Decimal, bucket_totals: Mapping[str, Decimal], total: Decimal
) -> tuple[dict[str, Decimal], Decimal]:
    """``amount`` split pro rata over the buckets, to the cent, with the column's rounding
    residual charged to the largest bucket so the column sums to ``amount`` exactly."""
    column = {b: cents(amount * v / total) if total else ZERO for b, v in bucket_totals.items()}
    residual = amount - sum(column.values(), ZERO)
    if residual != 0 and column:
        largest = max(column, key=lambda b: (bucket_totals[b], b))
        column[largest] += residual
    return column, residual


@dataclass(frozen=True)
class SystemLine:
    name: str
    depreciable_cost: Decimal  # sum of the rows' scaled costs x share, cents
    replacement_cost: Decimal  # sum of the rows' RCNs x share, cents


def systems_page(
    schedule: Schedule,
    rcn: Mapping[str, Decimal],
    systems: Mapping[str, Mapping[str, Decimal]],
) -> list[SystemLine]:
    """The building-systems page: ``systems`` maps a system name to ``{row id: share}``.
    Every schedule row must be charged to systems with shares summing to 1 (a row absent
    from every system is an error, as is a share total that is not 1)."""
    charged: dict[str, Decimal] = {}
    for name, shares in systems.items():
        for row_id, share in shares.items():
            if row_id not in schedule.rows:
                raise ScheduleError(f"system {name!r} names an unknown row {row_id!r}")
            charged[row_id] = charged.get(row_id, ZERO) + share
    bad = sorted(k for k in schedule.rows if charged.get(k, ZERO) != 1)
    if bad:
        raise ScheduleError(f"rows whose system shares do not sum to 1: {bad}")
    lines = []
    for name, shares in systems.items():
        dep = sum((schedule.rows[k] * s for k, s in shares.items()), ZERO)
        rep = sum((rcn[k] * s for k, s in shares.items()), ZERO)
        lines.append(SystemLine(name, cents(dep), cents(rep)))
    return lines


def implied_rcn(
    costs: Mapping[str, Decimal], rcn_total: Decimal, total: Decimal
) -> dict[str, Decimal]:
    """The RCN behind each printed row cost: cost x sum(RCN) / T (the inverse of the
    factor), unrounded. Scaling them back through :func:`build_schedule` reproduces the
    printed costs exactly; their sums per system reproduce the printed replacement costs
    to within rounding."""
    if total == 0:
        raise ScheduleError("a zero total has no factor")
    return {k: v * rcn_total / total for k, v in costs.items()}


@dataclass(frozen=True)
class Deviation:
    item: str
    reproduced: Decimal
    printed: Decimal

    @property
    def difference(self) -> Decimal:
        return self.reproduced - self.printed


@dataclass
class Reproduction:
    """A reproduced schedule against printed numbers: every compared item with its
    deviation, the largest absolute deviation, and whether all agree to the cent."""

    compared: int = 0
    deviations: list[Deviation] = field(default_factory=list)

    def add(self, item: str, reproduced: Decimal, printed: Decimal) -> None:
        self.compared += 1
        if cents(reproduced) != cents(printed):
            self.deviations.append(Deviation(item, reproduced, printed))

    @property
    def exact(self) -> bool:
        return not self.deviations

    @property
    def max_abs_deviation(self) -> Decimal:
        return max((abs(d.difference) for d in self.deviations), default=ZERO)


def check_reproduction(
    schedule: Schedule,
    printed_rows: Mapping[str, Decimal],
    printed_bucket_totals: Mapping[str, Decimal] | None = None,
    printed_bucket_percent: Mapping[str, Decimal] | None = None,
    printed_allocation_building: Mapping[str, Decimal] | None = None,
    printed_allocation_closing: Mapping[str, Decimal] | None = None,
    printed_total: Decimal | None = None,
    systems: Sequence[SystemLine] = (),
    printed_systems: Mapping[str, tuple[Decimal, Decimal]] | None = None,
) -> Reproduction:
    """Compare a schedule (and optionally its systems page) with printed numbers, item by
    item, to the cent (percentages to the tenth)."""
    result = Reproduction()
    for k, printed in printed_rows.items():
        result.add(f"row {k}", schedule.rows[k], printed)
    if printed_total is not None:
        result.add("total", schedule.total, printed_total)
    for name, mine, theirs in (
        ("bucket total", schedule.bucket_totals, printed_bucket_totals),
        ("allocation building", schedule.allocation_building, printed_allocation_building),
        ("allocation closing", schedule.allocation_closing, printed_allocation_closing),
    ):
        for b, printed in (theirs or {}).items():
            result.add(f"{name} {b}", mine[b], printed)
    for b, printed in (printed_bucket_percent or {}).items():
        result.compared += 1
        if tenths(schedule.bucket_percent[b]) != tenths(printed):
            result.deviations.append(Deviation(f"percent {b}", schedule.bucket_percent[b], printed))
    for line in systems:
        if printed_systems is None or line.name not in printed_systems:
            continue
        dep, rep = printed_systems[line.name]
        result.add(f"system {line.name} depreciable", line.depreciable_cost, dep)
        result.add(f"system {line.name} replacement", line.replacement_cost, rep)
    return result
