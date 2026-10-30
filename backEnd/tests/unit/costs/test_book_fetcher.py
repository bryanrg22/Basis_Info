"""The released 2020-book tables as a fetcher: every value of the file, cited, offline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.config import Settings
from basis.costs.indices.book import BookFetcher, book_entries, region_for
from basis.domain.cost_indices import BOOK_SOURCE, indices


def test_every_value_of_the_file_is_an_entry_with_its_citation_and_the_file_digest() -> None:
    book = indices()
    entries = book_entries(book)
    historical = [e for e in entries if e.series == "historical"]
    cities = [e for e in entries if e.series == "city"]
    assert len(historical) == len(book.years) == 51
    assert len(cities) == len(book.cities) == 651
    assert {e.source for e in entries} == {BOOK_SOURCE}
    assert all(e.document_sha256 == book.sha256 for e in entries)
    by_year = {e.year: e for e in historical}
    assert by_year[2020].value == Decimal("239.1") and by_year[2020].region == "US"
    assert by_year[2020].as_of == date(2020, 1, 1)
    assert by_year[2020].citation == "RSMeans Building Construction Costs 2020 p.778"
    assert by_year[2020].detail == {"estimated": True, "base": "Jan. 1, 1993 = 100"}
    assert by_year[2018].value == Decimal("222.9") and by_year[2018].as_of == date(2018, 7, 1)
    birmingham = next(e for e in cities if e.region == "AL:350-352")
    assert birmingham.value == Decimal("0.96") and birmingham.year == 2020
    assert birmingham.citation == "RSMeans Residential 2020 Location Factors - Residential p.718"
    assert birmingham.detail is not None and birmingham.detail["city"] == "Birmingham"
    assert len({e.key for e in entries}) == len(entries)  # one row per key


def test_region_keys_a_city_row_by_state_and_printed_prefixes() -> None:
    assert region_for("CA", "900-902") == "CA:900-902"
    assert region_for("NY", "100-102, 104") == "NY:100-102,104"


def test_the_fetcher_asks_only_for_what_the_store_lacks_and_is_always_available() -> None:
    fetcher = BookFetcher()
    assert fetcher.name == BOOK_SOURCE and fetcher.network is False
    assert fetcher.unavailable(Settings(basis_env="test")) is None
    today = date(2026, 10, 30)
    wanted = fetcher.wanted({"historical": {}, "city": {}}, today)
    assert wanted == {"historical": list(range(1970, 2021)), "city": [2020]}
    held = {"historical": {y: object() for y in range(1970, 2021)}, "city": {2020: object()}}
    assert fetcher.wanted(held, today) == {"historical": [], "city": []}
    partial = {"historical": {y: object() for y in range(1970, 2019)}, "city": {2020: object()}}
    assert fetcher.wanted(partial, today) == {"historical": [2019, 2020], "city": []}


@pytest.mark.asyncio
async def test_fetch_returns_exactly_the_wanted_years() -> None:
    fetched = await BookFetcher().fetch({"historical": [2019, 2020], "city": []}, timeout=1)
    assert [(e.year, str(e.value)) for e in fetched.entries] == [(2019, "232.2"), (2020, "239.1")]
    assert fetched.document_sha256 == indices().sha256 and fetched.failures == ()
    cities = await BookFetcher().fetch({"historical": [], "city": [2020]}, timeout=1)
    assert len(cities.entries) == 651 and {e.series for e in cities.entries} == {"city"}
