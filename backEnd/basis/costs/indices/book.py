"""The released 2020-book tables as a fetcher (``source`` ``rsmeans_2020_book``).

The values are the ones :mod:`basis.domain.cost_indices` reads from
``basis/data/cost_indices/rsmeans_2020.json`` (verified against the manifest): the
Historical Cost Index 1970-2020 (national, ``region`` ``US``) and the 651 Residential
Location Factors of 2020 (``region`` ``<state>:<zip prefixes>``, the city in ``detail``).
Offline and always available: the first refresh run seeds the store with them, each row
citing its book and page, and a later run finds nothing new to ask for. The data file stays
the citation source; the store is the ledger of what the product has obtained.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from basis.config import Settings
from basis.costs.indices.protocol import Fetched, Wanted
from basis.db.repositories.cost_index import NATIONAL, IndexEntry, Series
from basis.domain.cost_indices import BOOK_SOURCE, CostIndices, indices

LOCATION_BASIS: Final = (
    "the City Cost Index's weighted average over 100, as of July 1, 2019 "
    "(RSMeans Building Construction Costs 2020 p.779)"
)


def region_for(state: str, zip_prefixes: str) -> str:
    """The store's ``region`` of a city row: ``AL:350-352``."""
    return f"{state}:{zip_prefixes.replace(' ', '')}"


def book_entries(book: CostIndices | None = None) -> list[IndexEntry]:
    """Every value of the released file as a store entry."""
    book = book or indices()
    entries = [
        IndexEntry(
            source=BOOK_SOURCE,
            series="historical",
            year=row.year,
            region=NATIONAL,
            value=row.index,
            as_of=row.as_of,
            citation=row.source,
            document_sha256=book.sha256,
            detail={"estimated": row.estimated, "base": "Jan. 1, 1993 = 100"},
        )
        for row in sorted(book.years.values(), key=lambda r: r.year)
    ]
    for city in book.cities:
        entries.append(
            IndexEntry(
                source=BOOK_SOURCE,
                series="city",
                year=book.base.year,
                region=region_for(city.state, city.zip_prefixes),
                value=city.factor,
                as_of=book.base.as_of,
                citation=f"{book.location_source} p.{city.printed_page}",
                document_sha256=book.sha256,
                detail={
                    "state": city.state,
                    "city": city.city,
                    "zip_prefixes": city.zip_prefixes,
                    "printed_page": city.printed_page,
                    "basis": LOCATION_BASIS,
                },
            )
        )
    return entries


class BookFetcher:
    name = BOOK_SOURCE
    network = False

    def unavailable(self, settings: Settings) -> str | None:
        return None

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        book = indices()
        years = sorted(set(book.years) - set(have.get("historical", {})))
        cities = [] if book.base.year in set(have.get("city", {})) else [book.base.year]
        return {"historical": years, "city": cities}

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        book = indices()
        years = set(wanted.get("historical", ()))
        city_years = set(wanted.get("city", ()))
        entries = tuple(
            e
            for e in book_entries(book)
            if (e.series == "historical" and e.year in years)
            or (e.series == "city" and e.year in city_years)
        )
        return Fetched(entries=entries, document_sha256=book.sha256)


__all__ = ["BookFetcher", "book_entries", "region_for"]
