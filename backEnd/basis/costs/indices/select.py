"""Selection: the index values a study prices with, read from the store
(COST_INDICES.md section 10).

:func:`values_from_rows` turns the store's current rows (the latest version of every key,
every source) into one :class:`IndexValues`: for each year of the Historical Cost Index the
value of the best-ranked source that holds it, and for each year that has location rows
one :class:`~basis.domain.cost_indices.LocationTable`. The rank is: a manual entry (a firm
owner's, cited; the store is shared by every firm on the deployment) first, then the
configured sources in ``COST_INDEX_SOURCES`` order, then the released book, then any other
source alphabetically; a value converted from another series (``derived``) never outranks
a published one for the same year. The released book's values are the baseline under
everything (the store seeds from them, so they agree; an empty store prices exactly as
the file alone did). Every value read from a store row names it (``YearIndex.store``,
``CityFactor.store``: id, source, version), so a priced row's cost trail
(``basis.domain.cost_trail``) cites the exact append-only row it used.

The per-study choice of the cost year (the intake's placed-in-service date, the appraisal's
effective date as the cross-check) and of the location row (the intake's ZIP or state, the
appraisal's) stays in :func:`basis.domain.measurements.resolve_cost_index`, which takes the
``IndexValues`` and picks the location table of the latest year at or before the cost
year (a later table than the cost year is used only when no earlier one exists), and
records the table's year when it is not the cost year.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from basis.config import Settings
from basis.db.models import CostIndexValue
from basis.db.repositories.cost_index import NATIONAL, CostIndexRepository
from basis.domain.cost_indices import (
    BOOK_SOURCE,
    CityFactor,
    LocationTable,
    StoreRow,
    YearIndex,
    book_location_table,
    indices,
    location_table_from,
)

MANUAL_SOURCE: Final = "manual"
PAGE_PATTERN: Final = re.compile(r"\s+p\.\d+$")


@dataclass(frozen=True, slots=True)
class IndexValues:
    """What a study prices with: the Historical Cost Index by year (the stored value of the
    best-ranked source, the released book under it) and the location tables by year."""

    years: Mapping[int, YearIndex]
    location_tables: Mapping[int, LocationTable]
    origin: str  # "store" or "released file" (the store held nothing)
    priority: tuple[str, ...]

    def location_table_for(self, cost_year: int) -> LocationTable:
        """The table of the latest year at or before ``cost_year``, else the earliest
        later one (there is always the book's)."""
        years = sorted(self.location_tables)
        before = [y for y in years if y <= cost_year]
        return self.location_tables[before[-1] if before else years[0]]


def source_priority(settings: Settings) -> tuple[str, ...]:
    """Manual entries, then the configured sources in order, then the released book."""
    configured = [s.strip() for s in settings.cost_index_sources.split(",") if s.strip()]
    order: list[str] = [MANUAL_SOURCE]
    for name in [*configured, BOOK_SOURCE]:
        if name not in order:
            order.append(name)
    return tuple(order)


def _rank(source: str, priority: Sequence[str]) -> tuple[int, str]:
    return (priority.index(source) if source in priority else len(priority), source)


def _plain(value: Decimal) -> Decimal:
    """``239.1000`` (the column's scale) back to ``239.1``; ``100.0000`` to ``100``."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def store_row_of(row: CostIndexValue) -> StoreRow:
    """Which store row a value came from (id, source, version): what a priced row's cost
    trail names (``basis.domain.cost_trail``)."""
    return StoreRow(id=getattr(row, "id", None), source=row.source, version=row.version)


def year_index_of(row: CostIndexValue) -> YearIndex:
    detail = row.detail if isinstance(row.detail, dict) else {}
    return YearIndex(
        year=row.year,
        index=_plain(row.value),
        as_of=row.as_of,
        source=row.citation,
        estimated=bool(detail.get("estimated", False)),
        derived=row.derived,
        store=store_row_of(row),
    )


def city_factor_of(row: CostIndexValue) -> CityFactor:
    detail = row.detail if isinstance(row.detail, dict) else {}
    state, _, prefixes = row.region.partition(":")
    page = detail.get("printed_page")
    found = re.search(r"\bp\.(\d+)", row.citation)
    return CityFactor(
        state=str(detail.get("state") or state),
        zip_prefixes=str(detail.get("zip_prefixes") or prefixes),
        city=str(detail.get("city") or prefixes),
        factor=_plain(row.value),
        printed_page=int(page) if isinstance(page, int) else int(found.group(1)) if found else 0,
        store=store_row_of(row),
    )


def _location_table(rows: Sequence[CostIndexValue], *, year: int, source: str) -> LocationTable:
    book = book_location_table()
    if source == BOOK_SOURCE and year == book.year and len(rows) == book.cities:
        # The seed, as the file loader reads it: identical citations to the file alone.
        return book
    citations = {PAGE_PATTERN.sub("", row.citation) for row in rows}
    label = citations.pop() if len(citations) == 1 else f"{source} {year} location factors"
    pages = sorted({c.printed_page for c in (city_factor_of(r) for r in rows) if c.printed_page})
    return location_table_from(
        (city_factor_of(row) for row in rows),
        year=year,
        origin=source,
        source=label,
        pages=f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else str(pages[0]) if pages else "",
    )


def book_values(priority: Sequence[str] = (MANUAL_SOURCE, BOOK_SOURCE)) -> IndexValues:
    """The released file alone (an empty store, or no database at hand)."""
    book = indices()
    table = book_location_table()
    return IndexValues(
        years=dict(book.years),
        location_tables={table.year: table},
        origin="released file",
        priority=tuple(priority),
    )


def values_from_rows(rows: Iterable[CostIndexValue], *, priority: Sequence[str]) -> IndexValues:
    """The store's current rows as :class:`IndexValues` (pure; the book under them)."""
    book = indices()
    years: dict[int, YearIndex] = dict(book.years)
    best_year: dict[int, tuple[int, int, str]] = {}  # year -> (derived, rank, source)
    cities: dict[tuple[int, str], list[CostIndexValue]] = {}
    for row in rows:
        if row.series == "historical":
            if row.region != NATIONAL:
                continue
            rank = (1 if row.derived else 0, *_rank(row.source, priority))
            if row.year not in best_year or rank < best_year[row.year]:
                best_year[row.year] = rank
                years[row.year] = year_index_of(row)
        elif row.series == "city":
            cities.setdefault((row.year, row.source), []).append(row)
    tables: dict[int, LocationTable] = {}
    best_table: dict[int, tuple[int, str]] = {}
    for (year, source), group in cities.items():
        table_rank = _rank(source, priority)
        if year not in best_table or table_rank < best_table[year]:
            best_table[year] = table_rank
            tables[year] = _location_table(group, year=year, source=source)
    book_table = book_location_table()
    tables.setdefault(book_table.year, book_table)
    return IndexValues(
        years=years,
        location_tables=tables,
        origin="store" if best_year or len(tables) > 1 or best_table else "released file",
        priority=tuple(priority),
    )


async def load_index_values(session: AsyncSession, settings: Settings) -> IndexValues:
    """The store's current values under ``settings``' source priority."""
    rows = await CostIndexRepository(session).latest()
    priority = source_priority(settings)
    if not rows:
        return book_values(priority)
    return values_from_rows(rows, priority=priority)


__all__ = [
    "MANUAL_SOURCE",
    "IndexValues",
    "book_values",
    "city_factor_of",
    "load_index_values",
    "source_priority",
    "store_row_of",
    "values_from_rows",
    "year_index_of",
]
