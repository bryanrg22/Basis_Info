"""A study whose cost year the index store lacks asks for it before it prices (EVAL_REPORT 77).

``process_assets._ensure_cost_year`` chooses the year as pricing does (the intake's
placed-in-service year here), and when the store has no published index for it runs the
refresh job once (``trigger = study``), with no connection held; pricing then reads the
store again. The network source is ``FakeFetcher`` through the registry, so nothing leaves
the machine. Covered: the fetch and the priced factor afterwards; a year already loaded and a
year in the released book are not fetched; fetching off asks nothing; a source that fails and
a refresh that raises leave the year flagged and never fail the study.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from dbtest_support import db_settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities import process_assets
from basis.activities.process_assets import TakeoffPlan, _ensure_cost_year
from basis.config import Settings
from basis.costs.indices import registry
from basis.costs.indices.book import BookFetcher
from basis.costs.indices.protocol import Fetched, Fetcher, Wanted
from basis.costs.indices.select import load_index_values
from basis.db.repositories.cost_index import NATIONAL, CostIndexRepository, IndexEntry, Series
from basis.db.session import session_scope
from basis.domain.cost_indices import indices
from basis.domain.intake import StudyIntake

pytestmark = pytest.mark.integration

BOOK_BASE = Decimal("239.1")  # the 2020 book's Jan. 1, 2020 cost basis


class FakeFetcher:
    """The public source, answering from a script; counts its calls."""

    name = "rsmeans_public"
    network = True

    def __init__(self, answers: Mapping[int, str], *, fail: bool = False) -> None:
        self.answers = dict(answers)
        self.fail = fail
        self.calls: list[Wanted] = []

    def unavailable(self, settings: Settings) -> str | None:
        return None

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        held = set(have.get("historical", {}))
        return {"historical": [y for y in sorted(self.answers) if y not in held], "city": []}

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        self.calls.append(wanted)
        if self.fail:
            raise TimeoutError("the page did not answer")
        entries = tuple(
            IndexEntry(
                source=self.name,
                series="historical",
                year=y,
                region=NATIONAL,
                value=Decimal(self.answers[y]),
                as_of=date(y, 7, 1),
                citation=f"page {y}",
            )
            for y in wanted.get("historical", ())
            if y in self.answers
        )
        return Fetched(entries=entries)


def intake(placed_in_service: str) -> StudyIntake:
    return StudyIntake.model_validate(
        {
            "purchase_price": "650000.00",
            "acquisition_costs": [],
            "land_method": "appraisal_site_value",
            "appraisal_site_value": "90000.00",
            "appraisal_effective_date": placed_in_service,
            "property_use": "residential_rental",
            "dwelling_unit_income_share": "1",
            "transient_lodging": False,
            "placed_in_service_date": placed_in_service,
            "acquisition_date": placed_in_service,
            "state": "CA",
            "zip": "90210",
        }
    )


def prepared(placed_in_service: str) -> Any:
    """What ``_ensure_cost_year`` reads of ``_Prepared``: the intake, no appraisal fields."""
    return SimpleNamespace(measurements=None, rooms={}, intake=intake(placed_in_service))


def deps(sm: async_sessionmaker[AsyncSession], settings: Settings) -> Any:
    return SimpleNamespace(settings=settings, sessionmaker=sm)


def use(monkeypatch: pytest.MonkeyPatch, fetchers: Sequence[Fetcher]) -> None:
    monkeypatch.setattr(registry, "fetchers_for", lambda settings, names=None: list(fetchers))


async def plan_for(sm: async_sessionmaker[AsyncSession], settings: Settings, pis: str) -> Any:
    async with session_scope(sm) as session:
        values = await load_index_values(session, settings)
    return TakeoffPlan.build(settings, None, [], intake=intake(pis), values=values)


async def study_runs(sm: async_sessionmaker[AsyncSession]) -> list[Any]:
    async with session_scope(sm) as session:
        return [r for r in await CostIndexRepository(session).runs() if r.trigger == "study"]


async def test_a_missing_cost_year_is_fetched_once_and_priced_with(
    committed: async_sessionmaker[AsyncSession],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = db_settings(database_url, cost_index_fetch="on")
    public = FakeFetcher({2021: "257.5", 2024: "294.8", 2025: "300.7"})
    use(monkeypatch, [BookFetcher(), public])
    before = await plan_for(committed, on, "2024-06-01")
    assert before.index.cost_year == 2024 and before.index.year.extrapolated

    await _ensure_cost_year(deps(committed, on), prepared("2024-06-01"))

    assert public.calls == [{"historical": [2021, 2024, 2025], "city": []}]
    [run] = [r for r in await study_runs(committed) if r.source == "rsmeans_public"]
    assert (run.status, run.rows_added) == ("ok", 3)
    after = await plan_for(committed, on, "2024-06-01")
    assert not after.index.year.extrapolated
    assert after.index.year.factor == (Decimal("294.8") / BOOK_BASE).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )

    # The next study in 2024 (or 2025) finds the year loaded: nothing is asked again.
    await _ensure_cost_year(deps(committed, on), prepared("2025-02-01"))
    assert len(public.calls) == 1


async def test_a_year_in_the_released_book_is_never_fetched(
    committed: async_sessionmaker[AsyncSession],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = db_settings(database_url, cost_index_fetch="on")
    public = FakeFetcher({2024: "294.8"})
    use(monkeypatch, [public])
    assert 2015 in indices().years
    await _ensure_cost_year(deps(committed, on), prepared("2015-06-01"))
    assert public.calls == [] and await study_runs(committed) == []


async def test_fetching_off_asks_nothing_and_the_year_stays_flagged(
    committed: async_sessionmaker[AsyncSession],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = db_settings(database_url)  # BASIS_ENV=test: COST_INDEX_FETCH defaults to off
    assert off.cost_index_fetch is None and off.cost_index_fetching == "off"
    public = FakeFetcher({2024: "294.8"})
    use(monkeypatch, [public])
    await _ensure_cost_year(deps(committed, off), prepared("2024-06-01"))
    assert public.calls == [] and await study_runs(committed) == []
    assert (await plan_for(committed, off, "2024-06-01")).index.year.extrapolated


async def test_a_failing_source_leaves_the_year_flagged_and_the_study_goes_on(
    committed: async_sessionmaker[AsyncSession],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = db_settings(database_url, cost_index_fetch="on")
    use(monkeypatch, [FakeFetcher({2024: "294.8"}, fail=True)])
    await _ensure_cost_year(deps(committed, on), prepared("2024-06-01"))  # does not raise
    [run] = await study_runs(committed)
    assert run.status == "failed"
    assert (await plan_for(committed, on, "2024-06-01")).index.year.extrapolated


async def test_a_refresh_that_raises_is_logged_and_pricing_goes_on(
    committed: async_sessionmaker[AsyncSession],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    on = db_settings(database_url, cost_index_fetch="on")

    def broken(settings: Settings, names: Sequence[str] | None = None) -> list[Fetcher]:
        raise RuntimeError("registry broken")

    monkeypatch.setattr(registry, "fetchers_for", broken)
    with caplog.at_level(logging.WARNING, logger=process_assets.__name__):
        await _ensure_cost_year(deps(committed, on), prepared("2024-06-01"))
    [record] = [r for r in caplog.records if "cost index fetch for a study failed" in r.message]
    assert record.cost_year == 2024  # type: ignore[attr-defined]
    assert (await plan_for(committed, on, "2024-06-01")).index.year.extrapolated
