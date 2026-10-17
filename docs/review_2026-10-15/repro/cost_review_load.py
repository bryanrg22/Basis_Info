"""Re-price a live study's stored assets with a cost table and count the review load
(EVAL_REPORT section 27, item 6).

Run from ``backEnd/``::

    uv run python ../docs/review_2026-10-15/repro/cost_review_load.py ASSETS_JSON \\
        [--cost-table PATH] [--property-class nonresidential] [--cost-year 2025]

``ASSETS_JSON`` is one row per asset of the study's final revision, read from the
database: ``asset_id``, ``review_reason``, ``needs_review``, ``bucket``, ``irs_section``,
and its component's ``label``, ``material``, ``attachment``, ``count`` (``raw.count``),
``room_id``, ``room_type``, ``document_id``. Nothing is called: the classification and
every non-cost review reason are the stored ones. The takeoff and the cost are recomputed
the way ``process_assets`` computes them now (the rules-table key or the label, the room
type's typical area, area and perimeter rows measured once per room, the legacy rows
then the cost table by label), the cost reasons (``cross:COST_*``) are replaced by the
recomputed ones, and the reasons are tallied before and after. Prints the table; writes
nothing.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from basis.db.enums import Bucket
from basis.domain.cost_table import cost_table_for
from basis.domain.costs import CostError, NeedsQuantity, estimate_cost
from basis.domain.cross_validation import cross_validate
from basis.domain.rooms import typical_area_sf
from basis.domain.rules import lookup
from basis.domain.takeoffs import MissingTakeoffInput, Takeoff, estimate_quantity, takeoff_key

COST_CODES = ("cross:COST_ROW_MISSING", "cross:COST_NEEDS_QUANTITY")


def reasons_of(text: str | None) -> list[str]:
    return [r for r in (text or "").split("; ") if r]


def family(reason: str) -> str:
    """A reason without its free text (``model:...`` is one family)."""
    return "model" if reason.startswith("model:") else reason


def count_of(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path)
    parser.add_argument("--cost-table", default=None)
    parser.add_argument("--property-class", default="nonresidential")
    parser.add_argument("--cost-year", type=int, default=2025)
    args = parser.parse_args()
    table = cost_table_for(args.cost_table)
    rows = json.loads(args.assets.read_text())
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    flagged_before = flagged_after = 0
    priced_before = priced_after = 0
    total = Decimal(0)
    missing_labels: Counter[str] = Counter()
    measured: dict[tuple[str | None, str], str] = {}
    for row in rows:
        old = reasons_of(row["review_reason"])
        for r in dict.fromkeys(map(family, old)):
            before[r] += 1
        flagged_before += bool(row["needs_review"])
        priced_before += "cross:COST_ROW_MISSING" not in old
        label = row["label"] or "other"
        hit = lookup(label, row["material"], row["attachment"], args.property_class)
        area, _ = typical_area_sf(row["room_type"], args.property_class)
        try:
            takeoff: Takeoff = estimate_quantity(
                hit.key if hit else label, area, count_of(row["count"])
            )
        except MissingTakeoffInput:  # pragma: no cover - every room has a typical area
            takeoff = estimate_quantity(label, 150, 1)
        if takeoff.is_measured:
            key = (row["room_id"], takeoff_key(takeoff.component))
            first = measured.setdefault(key, row["asset_id"])
            if first != row["asset_id"]:
                takeoff = Takeoff(
                    component=takeoff.component,
                    quantity=Decimal("0.00"),
                    unit=takeoff.unit,
                    formula="measured once per room",
                )
        priced_per = None
        try:
            cost = estimate_cost(takeoff, args.cost_year, "1.0", table=table, label=label)
        except NeedsQuantity as exc:
            cost, priced_per = None, exc.unit
        except CostError:
            cost = None
        issues = cross_validate(
            section=row["irs_section"],
            bucket=Bucket(row["bucket"]),
            takeoff=takeoff,
            cost=cost,
            priced_per=priced_per,
        )
        cost_reasons = [
            f"cross:{i.code}" for i in issues if i.needs_review and i.code.startswith("COST_")
        ]
        new = [r for r in old if r not in COST_CODES] + cost_reasons
        new = list(dict.fromkeys(new))
        for r in dict.fromkeys(map(family, new)):
            after[r] += 1
        flagged_after += bool(new)
        if cost is not None:
            priced_after += 1
            total += cost.total
        elif "cross:COST_ROW_MISSING" in new:
            missing_labels[label] += 1
    name, size = (table.name, len(table.rows)) if table else ("none", 0)
    print(f"assets: {len(rows)}; table: {name} ({size} rows)")
    print(f"needs review: {flagged_before} -> {flagged_after}")
    print(
        f"priced: {priced_before} -> {priced_after} (estimated total ${total:,.2f}, "
        f"cost year {args.cost_year}, location 1.0)"
    )
    only_before = sum(
        1 for r in rows if reasons_of(r["review_reason"]) == ["cross:COST_ROW_MISSING"]
    )
    print(f"flagged only for COST_ROW_MISSING before: {only_before}")
    print("| reason | before | after |")
    print("|---|---|---|")
    for reason in sorted(set(before) | set(after), key=lambda r: -max(before[r], after[r])):
        print(f"| `{reason}` | {before[reason]} | {after[reason]} |")
    print("still missing a row, by label:", dict(missing_labels.most_common()))


if __name__ == "__main__":
    main()
