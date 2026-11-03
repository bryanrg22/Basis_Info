"""The suite's synthetic cost table for tests that price directly (EVAL_REPORT 78).

The product has no built-in prices: every row comes from a cost table. Settings-driven code
reads ``COST_TABLE_PATH`` (tests/conftest.py points it at this table); a test that calls
:func:`basis.domain.costs.estimate_cost` itself passes :data:`SUITE_TABLE`. Invented numbers:
the six labels the removed legacy placeholder rows priced, at the values those rows carried.
"""

from __future__ import annotations

from functools import partial

from fixtures import fixture_path

from basis.domain.cost_table import load_cost_table
from basis.domain.costs import estimate_cost

SUITE_TABLE = load_cost_table(str(fixture_path("suite_cost_table.json")))
priced = partial(estimate_cost, table=SUITE_TABLE)
