"""RSMeans cost indices: the time and location factors every priced asset rests on.

Research and the rules: docs/review_2026-10-15/COST_INDICES.md. The data:
``basis/data/cost_indices/rsmeans_2020.json`` (published RSMeans index numbers transcribed
from the two 2020 books in the reference corpus, each with its book and page), verified
against ``manifest.json`` at load so a released file cannot drift.

* **Time.** The RSMeans Historical Cost Index (Building Construction Costs 2020, p.778; base
  Jan. 1, 1993 = 100; one value per year, July 1, and Jan. 1 for 2020): a cost known in
  year B is moved to year A by ``Index A / Index B``. The 2020 books' unit costs are as of
  Jan. 1, 2020 (index 239.1), so the year factor of a cost year is its index over 239.1.
* **Location.** The Residential book's Location Factors (pp.718-722): one factor per city
  by three-digit ZIP prefix, the City Cost Index's weighted average over 100. A study with a
  ZIP in the table takes its city's factor; one with a state alone takes the mean of the
  state's listed cities (the book's rule for an unlisted city, "a nearby city with similar
  economic characteristics", needs a judgment the product does not make, so the mean is
  recorded as what it is).
* **Years after the book.** The 2020 book's last index is Jan. 1, 2020. A later cost year
  takes the latest published index (factor 1.0000) with the assumption recorded on every
  asset and the asset flagged (``cost:YEAR_INDEX_EXTRAPOLATED``), until the firm loads the
  year's published index: a supplement file (``COST_INDEX_SUPPLEMENT_PATH``) adds later years
  from a later book or the quarterly RSMeans Construction Cost Index, each with its source.
  Trending by a rate is not done: neither RSMeans nor Marshall & Swift extrapolates, and the
  IRS guide asks for the published source, volume and page (ATG ch. 3 C.2(2)).
* **Years before the table** (before 1970) raise :class:`UnknownCostYear`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any, Final

DATA_DIR: Final = Path(__file__).resolve().parent.parent / "data" / "cost_indices"
RELEASED_FILE: Final = "rsmeans_2020.json"
# The store's name for the released file's values (basis.costs.indices.book).
BOOK_SOURCE: Final = "rsmeans_2020_book"
SCHEMA_VERSION: Final = 1
SUPPLEMENT_SCHEMA_VERSION: Final = 1
FACTOR_QUANTUM: Final = Decimal("0.0001")
LOCATION_QUANTUM: Final = Decimal("0.001")
HUNDRED: Final = Decimal(100)


class CostIndexError(ValueError):
    """The index file is missing, unreadable, breaks the schema or fails its manifest."""


class UnknownCostYear(ValueError):
    """No published index covers the cost year (before the table's first year)."""


@dataclass(frozen=True, slots=True)
class YearIndex:
    """One published index value: the Historical Cost Index for a year."""

    year: int
    index: Decimal  # Jan. 1, 1993 = 100
    as_of: date
    source: str  # book and page (or the stored row's citation)
    estimated: bool = False
    # Set when the value is not a published RSMeans index but one converted from another
    # public series (COST_INDICES.md section 11): how, with the error it carries.
    derived: str | None = None

    def describe(self) -> str:
        when = self.as_of.strftime("%b %Y").replace("Jul ", "July ")
        est = ", est." if self.estimated else ""
        return f"{self.index} ({when}{est})"


@dataclass(frozen=True, slots=True)
class YearFactor:
    """The time factor of a cost year against the books' Jan. 1, 2020 cost basis."""

    cost_year: int
    factor: Decimal  # index / base index, four decimals
    index: YearIndex
    base: YearIndex
    assumption: str | None = None  # set when the year has no published index loaded

    @property
    def extrapolated(self) -> bool:
        return self.assumption is not None

    @property
    def conversion(self) -> str | None:
        """How the year's value was converted from another series, when it was."""
        return self.index.derived

    def citation(self) -> str:
        """``HCI 222.9 (July 2018) / 239.1 (Jan 2020, estimated), <book> p.778``."""
        text = f"HCI {self.index.describe()} / {self.base.describe()}, {self.base.source}"
        if self.index.source != self.base.source:
            text = (
                f"HCI {self.index.describe()} [{self.index.source}] / "
                f"{self.base.describe()} [{self.base.source}]"
            )
        return text


@dataclass(frozen=True, slots=True)
class CityFactor:
    state: str
    zip_prefixes: str  # as printed: "350-352", "942,956-958"
    city: str
    factor: Decimal
    printed_page: int

    def prefixes(self) -> frozenset[str]:
        return frozenset(_expand_prefixes(self.zip_prefixes))


@dataclass(frozen=True, slots=True)
class LocationFactor:
    """A location factor with the row(s) it came from."""

    factor: Decimal
    state: str
    source: str  # book, table, page(s)
    city: CityFactor | None  # the ZIP's city, or None for a state mean
    cities: int  # rows averaged (1 for a city)
    low: Decimal
    high: Decimal
    pages: str  # printed page(s)

    @property
    def is_state_mean(self) -> bool:
        return self.city is None

    def citation(self) -> str:
        if self.city is not None:
            return (
                f"{self.city.zip_prefixes} {self.city.city} {self.city.factor}, "
                f"{self.source} p.{self.pages}"
            )
        return (
            f"mean of {self.cities} {self.state} cities ({self.low}-{self.high}), "
            f"{self.source} pp.{self.pages}"
        )


@dataclass(frozen=True, slots=True)
class LocationTable:
    """One year's location factors: the released book's, or rows of the store
    (COST_INDICES.md section 10) for a later table."""

    year: int
    origin: str  # rsmeans_2020_book, manual, ...
    source: str  # book and table name
    pages: str  # printed page(s), or the citation's locator
    by_prefix: Mapping[str, CityFactor]
    by_state: Mapping[str, tuple[CityFactor, ...]]

    @property
    def cities(self) -> int:
        return sum(len(rows) for rows in self.by_state.values())


@dataclass(frozen=True, slots=True)
class CostIndices:
    name: str
    sha256: str
    base: YearIndex  # the books' cost basis (Jan. 1, 2020)
    years: Mapping[int, YearIndex]
    year_source: str  # book, table and page of the HCI
    cities: tuple[CityFactor, ...]
    location_source: str  # book and table of the location factors
    location_pages: str  # the table's printed pages
    by_prefix: Mapping[str, CityFactor]
    by_state: Mapping[str, tuple[CityFactor, ...]]

    @property
    def first_year(self) -> int:
        return min(self.years)

    @property
    def last_year(self) -> int:
        return max(self.years)


# ------------------------------------------------------------------ loading


def _expand_prefixes(text: str) -> list[str]:
    out: list[str] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            if not (low.isdigit() and high.isdigit() and len(low) == len(high) == 3):
                raise CostIndexError(f"bad ZIP prefix range {part!r}")
            if int(low) > int(high):
                raise CostIndexError(f"bad ZIP prefix range {part!r}")
            out.extend(f"{i:03d}" for i in range(int(low), int(high) + 1))
        else:
            if not (part.isdigit() and len(part) == 3):
                raise CostIndexError(f"bad ZIP prefix {part!r}")
            out.append(part)
    return out


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CostIndexError(f"{what} must be a decimal string, got {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise CostIndexError(f"{what} is not a number: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise CostIndexError(f"{what} must be positive, got {value!r}")
    return number


def _date(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CostIndexError(f"{what} is not an ISO date: {value!r}") from exc


def _year_entries(entries: Any, source: str, *, what: str) -> dict[int, YearIndex]:
    if not isinstance(entries, list) or not entries:
        raise CostIndexError(f"{what} must be a non-empty list")
    years: dict[int, YearIndex] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise CostIndexError(f"{what}: every entry must be an object")
        year = raw.get("year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
            raise CostIndexError(f"{what}: bad year {year!r}")
        if year in years:
            raise CostIndexError(f"{what}: year {year} listed twice")
        as_of = _date(raw.get("as_of"), f"{what} {year} as_of")
        if as_of.year != year:
            raise CostIndexError(f"{what}: {year} is dated {as_of}")
        row_source = raw.get("source", source)
        if not isinstance(row_source, str) or not row_source.strip():
            raise CostIndexError(f"{what}: {year} needs a source")
        years[year] = YearIndex(
            year=year,
            index=_decimal(raw.get("index"), f"{what} {year} index"),
            as_of=as_of,
            source=row_source,
            estimated=bool(raw.get("estimated", False)),
        )
    return years


def parse_indices(data: Any, *, sha256: str = "") -> CostIndices:
    """Validate the index document; ``CostIndexError`` names the first defect."""
    if not isinstance(data, dict):
        raise CostIndexError("the cost index file must hold a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CostIndexError(f"cost index schema_version must be {SCHEMA_VERSION}")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise CostIndexError("a cost index file needs a name")
    hci = data.get("historical_cost_index")
    if not isinstance(hci, dict):
        raise CostIndexError("historical_cost_index must be an object")
    if not isinstance(hci.get("short_title"), str) or not isinstance(hci.get("printed_page"), int):
        raise CostIndexError("historical_cost_index needs its short_title and printed_page")
    year_source = f"{hci['short_title']} p.{hci['printed_page']}"
    years = _year_entries(hci.get("annual"), year_source, what="historical_cost_index.annual")
    basis = hci.get("cost_basis")
    if not isinstance(basis, dict):
        raise CostIndexError("historical_cost_index.cost_basis must be an object")
    base = YearIndex(
        year=_date(basis.get("as_of"), "cost_basis.as_of").year,
        index=_decimal(basis.get("index"), "cost_basis.index"),
        as_of=_date(basis.get("as_of"), "cost_basis.as_of"),
        source=year_source,
        estimated=bool(basis.get("estimated", False)),
    )
    if base.year not in years or years[base.year].index != base.index:
        raise CostIndexError("the cost basis must be one of the annual index values")
    loc = data.get("location_factors")
    if not isinstance(loc, dict):
        raise CostIndexError("location_factors must be an object")
    if not isinstance(loc.get("short_title"), str) or not isinstance(loc.get("printed_pages"), str):
        raise CostIndexError("location_factors needs its short_title and printed_pages")
    location_source = f"{loc['short_title']} {loc.get('table', 'Location Factors')}"
    location_pages = str(loc["printed_pages"])
    raw_cities = loc.get("cities")
    if not isinstance(raw_cities, list) or not raw_cities:
        raise CostIndexError("location_factors.cities must be a non-empty list")
    cities: list[CityFactor] = []
    by_prefix: dict[str, CityFactor] = {}
    by_state: dict[str, list[CityFactor]] = {}
    for raw in raw_cities:
        if not isinstance(raw, dict):
            raise CostIndexError("location_factors.cities: every entry must be an object")
        state = raw.get("state")
        if not isinstance(state, str) or len(state) != 2 or not state.isupper():
            raise CostIndexError(f"bad state code {state!r}")
        city_name = raw.get("city")
        if not isinstance(city_name, str) or not city_name.strip():
            raise CostIndexError(f"{state}: a city needs a name")
        page = raw.get("printed_page")
        if isinstance(page, bool) or not isinstance(page, int):
            raise CostIndexError(f"{state} {city_name}: printed_page must be an integer")
        zips = raw.get("zip_prefixes")
        if not isinstance(zips, str):
            raise CostIndexError(f"{state} {city_name}: zip_prefixes must be a string")
        city = CityFactor(
            state=state,
            zip_prefixes=zips,
            city=city_name,
            factor=_decimal(raw.get("factor"), f"{state} {city_name} factor"),
            printed_page=page,
        )
        for prefix in _expand_prefixes(zips):
            if prefix in by_prefix:
                raise CostIndexError(
                    f"ZIP prefix {prefix} is listed for {by_prefix[prefix].city} and {city_name}"
                )
            by_prefix[prefix] = city
        cities.append(city)
        by_state.setdefault(state, []).append(city)
    return CostIndices(
        name=name,
        sha256=sha256,
        base=base,
        years=years,
        year_source=year_source,
        cities=tuple(cities),
        location_source=location_source,
        location_pages=location_pages,
        by_prefix=by_prefix,
        by_state={state: tuple(rows) for state, rows in by_state.items()},
    )


def load_indices(path: Path, manifest: Path | None) -> CostIndices:
    """Load an index file; with a manifest, refuse one whose sha256 is not the released one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CostIndexError(f"cannot read the cost index file {path}: {exc}") from exc
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if manifest is not None:
        try:
            released = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CostIndexError(f"cannot read the cost index manifest {manifest}: {exc}") from exc
        expected = released.get(path.name, {}).get("sha256") if isinstance(released, dict) else None
        if expected != digest:
            raise CostIndexError(
                f"{path.name} changed after release (sha256 {digest[:12]} != manifest "
                f"{str(expected)[:12]}); add the next file instead of editing a released one"
            )
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CostIndexError(f"the cost index file {path} is not JSON: {exc}") from exc
    return parse_indices(data, sha256=digest)


@cache
def indices() -> CostIndices:
    """The released RSMeans 2020 indices, verified against the manifest (loaded once)."""
    return load_indices(DATA_DIR / RELEASED_FILE, DATA_DIR / "manifest.json")


# ------------------------------------------------------------------ the supplement


def parse_supplement(data: Any, *, book: CostIndices | None = None) -> dict[int, YearIndex]:
    """The firm's later index values (``COST_INDEX_SUPPLEMENT_PATH``)::

        {"schema_version": 1, "historical_cost_index": [
            {"year": 2024, "as_of": "2024-01-01", "index": "<Jan. 1, 1993 = 100>",
             "source": "<book or quarterly index, volume, page>"}]}

    Every entry names its source. A year the released book covers is allowed: a later
    book revises the earlier one's July estimate, and the store keeps both versions
    (COST_INDICES.md section 9); the import is what ``python -m basis.costs.refresh import``
    appends to the store.
    """
    if not isinstance(data, dict):
        raise CostIndexError("the supplement must hold a JSON object")
    if data.get("schema_version") != SUPPLEMENT_SCHEMA_VERSION:
        raise CostIndexError(f"supplement schema_version must be {SUPPLEMENT_SCHEMA_VERSION}")
    entries = data.get("historical_cost_index")
    if not isinstance(entries, list):
        raise CostIndexError("supplement.historical_cost_index must be a list")
    for raw in entries:
        if not isinstance(raw, dict) or not isinstance(raw.get("source"), str):
            raise CostIndexError("every supplement entry needs its own source")
    if not entries:
        return {}
    return _year_entries(entries, "", what="supplement.historical_cost_index")


@cache
def load_supplement(path: str) -> Mapping[int, YearIndex]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise CostIndexError(f"cannot read the cost index supplement {path}: {exc}") from exc
    except ValueError as exc:
        raise CostIndexError(f"the cost index supplement {path} is not JSON: {exc}") from exc
    return parse_supplement(data)


def supplement_for(path: str | None) -> Mapping[int, YearIndex] | None:
    return None if path is None else load_supplement(path)


# ------------------------------------------------------------------ lookups


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(FACTOR_QUANTUM, rounding=ROUND_HALF_UP)


def year_index(cost_year: int, supplement: Mapping[int, YearIndex] | None = None) -> YearIndex:
    """The index of ``cost_year``: the stored values' (``supplement``: the store's current
    rows, which hold the released book's years and any later or revised value), else the
    released book's; ``UnknownCostYear`` for a year before the table, ``KeyError`` for one
    nobody has loaded."""
    book = indices()
    if supplement and cost_year in supplement:
        return supplement[cost_year]
    if cost_year in book.years:
        return book.years[cost_year]
    if cost_year < book.first_year:
        raise UnknownCostYear(
            f"no cost index for {cost_year}; the Historical Cost Index covers "
            f"{book.first_year}-{book.last_year}"
        )
    raise KeyError(cost_year)


def year_factor_for(
    cost_year: int, supplement: Mapping[int, YearIndex] | None = None
) -> YearFactor:
    """The time factor of ``cost_year`` against the Jan. 1, 2020 cost basis.

    A year after the latest published index takes that index with the assumption recorded
    (``extrapolated``); a year before the table raises :class:`UnknownCostYear`.
    """
    book = indices()
    try:
        index = year_index(cost_year, supplement)
    except KeyError:
        latest = latest_index_before(cost_year, supplement)
        return YearFactor(
            cost_year=cost_year,
            factor=_ratio(latest.index, book.base.index),
            index=latest,
            base=book.base,
            assumption=missing_year_assumption(cost_year, latest),
        )
    return YearFactor(
        cost_year=cost_year,
        factor=_ratio(index.index, book.base.index),
        index=index,
        base=book.base,
    )


def latest_index_before(
    cost_year: int, supplement: Mapping[int, YearIndex] | None = None
) -> YearIndex:
    """The latest loaded index of a year before ``cost_year`` (the book's or the store's):
    what the missing-year rule applies (COST_INDICES.md section 4)."""
    book = indices()
    years = {y for y in book.years if y < cost_year}
    if supplement:
        years |= {y for y in supplement if y < cost_year}
    if not years:
        raise UnknownCostYear(f"no cost index before {cost_year}")
    return year_index(max(years), supplement)


def missing_year_assumption(cost_year: int, latest: YearIndex) -> str:
    """The assumption written on every row priced under the missing-year rule."""
    return (
        f"no published cost index for {cost_year} is loaded; the latest, "
        f"{latest.describe()}, is applied. Enter the {cost_year} Historical Cost Index "
        "(RSMeans quarterly Construction Cost Index or the year's book; POST /v1/cost-indices "
        "or python -m basis.costs.refresh enter) before the study is signed"
    )


def year_ratio(
    cost_year: int, data_year: int, supplement: Mapping[int, YearIndex] | None = None
) -> tuple[Decimal, str]:
    """``(Index cost_year / Index data_year, the formula)``: a cost dated ``data_year``
    moved to ``cost_year`` (the book's time adjustment, p.778). Raises ``KeyError`` when
    either year has no published index loaded, ``UnknownCostYear`` before the table."""
    a, b = year_index(cost_year, supplement), year_index(data_year, supplement)
    return _ratio(a.index, b.index), f"HCI {a.describe()} / HCI {b.describe()}"


def location_table_from(
    cities: Iterable[CityFactor], *, year: int, origin: str, source: str, pages: str
) -> LocationTable:
    """A location table from city rows (the book's, or the store's for a later year);
    ``CostIndexError`` when two rows claim one ZIP prefix."""
    by_prefix: dict[str, CityFactor] = {}
    by_state: dict[str, list[CityFactor]] = {}
    for city in cities:
        for prefix in city.prefixes():
            if prefix in by_prefix:
                raise CostIndexError(
                    f"ZIP prefix {prefix} is listed for {by_prefix[prefix].city} and {city.city}"
                )
            by_prefix[prefix] = city
        by_state.setdefault(city.state, []).append(city)
    return LocationTable(
        year=year,
        origin=origin,
        source=source,
        pages=pages,
        by_prefix=by_prefix,
        by_state={state: tuple(rows) for state, rows in by_state.items()},
    )


@cache
def book_location_table() -> LocationTable:
    """The released book's location factors as a :class:`LocationTable`."""
    book = indices()
    return LocationTable(
        year=book.base.year,
        origin=BOOK_SOURCE,
        source=book.location_source,
        pages=book.location_pages,
        by_prefix=book.by_prefix,
        by_state=book.by_state,
    )


def location_factor_for_zip(
    zip_code: str, table: LocationTable | None = None
) -> LocationFactor | None:
    """The city factor of a ZIP's first three digits in ``table`` (the released book's
    when None), or ``None`` when the table has no row for the prefix."""
    table = table or book_location_table()
    digits = "".join(ch for ch in zip_code.strip() if ch.isdigit())
    if len(digits) < 3:
        return None
    city = table.by_prefix.get(digits[:3])
    if city is None:
        return None
    return LocationFactor(
        factor=city.factor,
        state=city.state,
        source=table.source,
        city=city,
        cities=1,
        low=city.factor,
        high=city.factor,
        pages=str(city.printed_page),
    )


def location_factor_for_state_code(
    state: str, table: LocationTable | None = None
) -> LocationFactor | None:
    """The mean of the state's listed cities (three decimals) in ``table`` (the released
    book's when None), or ``None`` for a code the table does not have."""
    table = table or book_location_table()
    code = state.strip().upper()
    rows = table.by_state.get(code)
    if not rows:
        return None
    factors = [row.factor for row in rows]
    mean = (sum(factors, Decimal(0)) / len(factors)).quantize(
        LOCATION_QUANTUM, rounding=ROUND_HALF_UP
    )
    return LocationFactor(
        factor=mean,
        state=code,
        source=table.source,
        city=None,
        cities=len(rows),
        low=min(factors),
        high=max(factors),
        pages=table.pages,
    )


__all__ = [
    "BOOK_SOURCE",
    "DATA_DIR",
    "RELEASED_FILE",
    "CityFactor",
    "CostIndexError",
    "CostIndices",
    "LocationFactor",
    "LocationTable",
    "UnknownCostYear",
    "YearFactor",
    "YearIndex",
    "book_location_table",
    "indices",
    "latest_index_before",
    "load_indices",
    "load_supplement",
    "location_factor_for_state_code",
    "location_factor_for_zip",
    "location_table_from",
    "missing_year_assumption",
    "parse_indices",
    "parse_supplement",
    "supplement_for",
    "year_factor_for",
    "year_index",
    "year_ratio",
]
