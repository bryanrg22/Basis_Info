"""Selection: the store's rows become the values a study prices with, by source priority,
with the released book under them; the location table of the nearest year."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from basis.config import Settings
from basis.costs.indices.select import (
    MANUAL_SOURCE,
    book_values,
    source_priority,
    values_from_rows,
)
from basis.domain.cost_indices import BOOK_SOURCE, book_location_table, indices
from basis.domain.measurements import location_table_for, resolve_cost_index

D = Decimal


def row(
    year: int,
    value: str,
    *,
    source: str = MANUAL_SOURCE,
    series: str = "historical",
    region: str = "US",
    citation: str = "a citation",
    derived: str | None = None,
    detail: dict[str, Any] | None = None,
    version: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        series=series,
        year=year,
        region=region,
        value=D(value),
        as_of=date(year, 7, 1),
        citation=citation,
        derived=derived,
        detail=detail,
        version=version,
    )


def settings(**overrides: Any) -> Settings:
    return Settings(basis_env="test", **overrides)


def test_the_priority_is_manual_then_the_configured_sources_then_the_book() -> None:
    assert source_priority(settings()) == (MANUAL_SOURCE, BOOK_SOURCE, "rsmeans_public")
    assert source_priority(settings(cost_index_sources="rsmeans_public, rsmeans_api")) == (
        MANUAL_SOURCE,
        "rsmeans_public",
        "rsmeans_api",
        BOOK_SOURCE,
    )
    assert source_priority(settings(cost_index_sources="")) == (MANUAL_SOURCE, BOOK_SOURCE)


def test_an_empty_store_prices_as_the_released_file_alone() -> None:
    values = book_values()
    assert values.origin == "released file"
    assert dict(values.years) == dict(indices().years)
    assert values.location_tables == {2020: book_location_table()}
    assert values.location_table_for(2024) is book_location_table()


def test_a_later_year_from_the_store_joins_the_book_years() -> None:
    values = values_from_rows(
        [row(2024, "294.8", source="rsmeans_public", citation="Gordian 2024 page")],
        priority=source_priority(settings()),
    )
    assert values.origin == "store"
    assert values.years[2020].index == D("239.1")  # the book under it
    got = values.years[2024]
    assert (got.index, got.as_of, got.source, got.derived) == (
        D("294.8"),
        date(2024, 7, 1),
        "Gordian 2024 page",
        None,
    )
    assert values.years[2024].describe() == "294.8 (July 2024)"


def test_the_stored_value_is_plain_decimal_whatever_the_columns_scale() -> None:
    values = values_from_rows(
        [row(2024, "294.8000"), row(2025, "300.0000")], priority=(MANUAL_SOURCE,)
    )
    assert str(values.years[2024].index) == "294.8"
    assert str(values.years[2025].index) == "300"


def test_a_manual_entry_outranks_a_fetched_value_and_a_published_one_a_converted_one() -> None:
    priority = source_priority(settings(cost_index_sources="rsmeans_2020_book,rsmeans_public"))
    rows = [
        row(2024, "294.8", source="rsmeans_public"),
        row(2024, "295.0", source=MANUAL_SOURCE, citation="the 2024 book p.1"),
        row(2025, "320.0", source=MANUAL_SOURCE, derived="Turner Q4 2025 x 239.1 / 1171"),
        row(2025, "300.7", source="rsmeans_public"),
    ]
    values = values_from_rows(rows, priority=priority)
    assert values.years[2024].index == D("295.0")  # manual first
    assert values.years[2025].index == D("300.7")  # published beats converted
    assert values.years[2025].derived is None
    only_converted = values_from_rows([rows[2]], priority=priority)
    assert only_converted.years[2025].derived == "Turner Q4 2025 x 239.1 / 1171"


def test_a_stored_book_year_revised_by_a_manual_entry_wins_over_the_file() -> None:
    values = values_from_rows(
        [row(2020, "234.6", citation="2021 book, July 2020 actual")], priority=(MANUAL_SOURCE,)
    )
    assert values.years[2020].index == D("234.6")
    assert values.years[2019].index == D("232.2")  # untouched book year


def test_the_seeded_city_rows_give_back_the_books_table_exactly() -> None:
    from basis.costs.indices.book import book_entries

    seed = [
        SimpleNamespace(
            source=e.source,
            series=e.series,
            year=e.year,
            region=e.region,
            value=e.value,
            as_of=e.as_of,
            citation=e.citation,
            derived=None,
            detail=e.detail,
            version=1,
        )
        for e in book_entries()
    ]
    values = values_from_rows(seed, priority=source_priority(settings()))
    assert values.location_tables[2020] is book_location_table()
    assert dict(values.years) == dict(indices().years)


def test_a_later_location_table_from_the_store_serves_the_nearest_year() -> None:
    rows = [
        row(
            2024,
            "1.05",
            series="city",
            region="CA:900-902",
            citation="RSMeans Residential 2024 Location Factors p.720",
            detail={"city": "Los Angeles", "state": "CA", "zip_prefixes": "900-902"},
        ),
        row(
            2024,
            "0.97",
            series="city",
            region="CA:945",
            citation="RSMeans Residential 2024 Location Factors p.720",
            detail={"city": "Oakland", "state": "CA", "zip_prefixes": "945"},
        ),
    ]
    values = values_from_rows(rows, priority=(MANUAL_SOURCE,))
    assert sorted(values.location_tables) == [2020, 2024]
    table = values.location_tables[2024]
    assert (table.year, table.origin, table.cities) == (2024, MANUAL_SOURCE, 2)
    assert table.source == "RSMeans Residential 2024 Location Factors"
    assert table.pages == "720"
    assert values.location_table_for(2023) is book_location_table()
    assert values.location_table_for(2024) is table
    assert values.location_table_for(2030) is table
    assert location_table_for(2024, values.location_tables) is table
    assert location_table_for(2024, None) is None

    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2024, 12, 3),
        state="CA",
        zip_code="90001",
        supplement=values.years,
        location_tables=values.location_tables,
    )
    assert index.location_factor == D("1.05")
    assert index.location_source == (
        "zip 90001 (intake): 900-902 Los Angeles 1.05, "
        "RSMeans Residential 2024 Location Factors p.720"
    )
    state_only = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2024, 12, 3),
        state="CA",
        supplement=values.years,
        location_tables=values.location_tables,
    )
    assert state_only.location_factor == D("1.010")  # mean of the two 2024 rows
    assert "mean of 2 CA cities (0.97-1.05)" in (state_only.location_source or "")


def test_the_missing_year_rule_applies_the_latest_loaded_year_from_either_source() -> None:
    values = values_from_rows([row(2023, "293.7", source="rsmeans_public")], priority=())
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=D("1"),
        placed_in_service=date(2025, 6, 1),
        state="CA",
        supplement=values.years,
        location_tables=values.location_tables,
    )
    assert index.cost_year == 2025 and index.year is not None
    assert index.year.extrapolated and index.year.index.year == 2023
    assert index.year.factor == (D("293.7") / D("239.1")).quantize(D("0.0001"))
    assert "no published cost index for 2025 is loaded; the latest, 293.7 (July 2023)" in (
        index.year_assumption or ""
    )
