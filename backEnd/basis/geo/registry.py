"""The registry of public geographic sources (``sources.json``; EVAL_REPORT 101 has the live
re-test of every entry).

One entry per source: its ``kind`` (``imagery`` or ``parcels``), the box it covers
(``bbox``, lon/lat; for ArcGIS services the service's own full extent, read at the re-test), how
to ask it (``protocol``, ``url``, ``params``), the imagery's native pixel size, year and
season, the terms as the provider states them (``terms_class``, a short ``terms_quote`` and
the ``terms_url`` it was read at) and the ``credit`` line to show and print with it. Sources
that failed the re-test stay in the file with ``status: unavailable``, the date and the
reason, and are never asked.

**Which image is the property's current view** (:func:`rank_key`). For measuring a driveway or a
fence the pixel size decides first (section 5 of the research note: 6-inch pixels or finer put
a traced driveway within roughly 3 to 7 %; 60 cm federal pixels make the house and drive a
blur), then the season (leaf-off shows the pavement under the canopy), then recency (what is
there now):

1. the pixel class: ``sharp`` (15.25 cm or finer), ``fair`` (to 35 cm), ``coarse`` (the rest);
2. leaf-off before leaf-on or unknown, but only within ``LEAF_OFF_YEARS`` of the newest image
   in its class, so a fifteen-year-old leaf-off image does not beat last year's;
3. the newer year;
4. the finer pixel; then the id, so the order is total.

The gatherer tries the sources in that order and keeps the first that answers with real imagery
at the point (a blank or transparent answer means the point is outside its coverage).

**The history** is one view per year: for each year any covering source has, the best-ranked
source of that year.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCES_PATH: Final = Path(__file__).with_name("sources.json")
SHARP_M: Final = 0.1525
FAIR_M: Final = 0.35
LEAF_OFF_YEARS: Final = 4

Kind = Literal["imagery", "parcels"]
Protocol = Literal[
    "arcgis-imageserver", "arcgis-mapserver", "wms", "tiles", "arcgis-feature", "unsupported"
]
TermsClass = Literal["public-domain", "open-with-credit", "no-terms-stated", "restricted"]


class YearQuery(BaseModel):
    """Where the year at a point is read when a mosaic mixes years: an ArcGIS ``query``
    endpoint (a layer, or an ImageServer's catalog), the attribute holding the year (or a name
    with the year in it) and an optional ``where``."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str
    field: str
    where: str = "1=1"


class ParcelFields(BaseModel):
    """Attribute names in a parcel layer (absent: the layer has no such field)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    owner: str | None = None
    acres: tuple[str, ...] = ()
    acres_unit: str | None = None  # a field naming the unit of ``acres``; acres only when "A..."
    area_sqft: str | None = None
    land: str | None = None
    improvements: str | None = None
    total: str | None = None
    year_built: str | None = None
    building_area: str | None = None
    stories: str | None = None
    address: str | None = None


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: Kind
    name: str
    states: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    protocol: Protocol
    url: str
    params: dict[str, str] = Field(default_factory=dict)
    native_pixel_m: float | None = None
    year: int | None = None
    year_label: str | None = None
    year_query: YearQuery | None = None
    leaf_off: bool | None = None
    terms_class: TermsClass
    terms_quote: str
    terms_url: str
    credit: str
    fields: ParcelFields | None = None
    status: Literal["available", "unavailable"] = "available"
    checked_on: str | None = None
    status_reason: str | None = None
    retest: dict[str, Any] | None = None
    notes: str | None = None

    @field_validator("bbox")
    @classmethod
    def _box(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        west, south, east, north = v
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError(f"bad bbox {v}")
        return v

    def covers(self, lat: float, lon: float) -> bool:
        west, south, east, north = self.bbox
        return west <= lon <= east and south <= lat <= north

    @property
    def pixel_class(self) -> int:
        px = self.native_pixel_m
        if px is None:
            return 2
        return 0 if px <= SHARP_M else 1 if px <= FAIR_M else 2


def parse(document: dict[str, Any]) -> list[Source]:
    raw = document.get("sources")
    if not isinstance(raw, list):
        raise ValueError("sources.json needs a 'sources' list")
    sources = [Source.model_validate(entry) for entry in raw]
    ids = [s.id for s in sources]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate source ids: {dupes}")
    for s in sources:
        if s.kind == "parcels" and s.fields is None and s.status == "available":
            raise ValueError(f"{s.id}: a parcel layer needs its fields")
    return sources


@cache
def load(path: Path = SOURCES_PATH) -> tuple[Source, ...]:
    return tuple(parse(json.loads(path.read_text())))


def covering(sources: Sequence[Source], lat: float, lon: float, kind: Kind) -> list[Source]:
    return [s for s in sources if s.kind == kind and s.status == "available" and s.covers(lat, lon)]


def rank_key(
    source: Source, newest_in_class: dict[int, int], year: int | None = None
) -> tuple[Any, ...]:
    y = year if year is not None else source.year
    newest = newest_in_class.get(source.pixel_class, y or 0)
    leaf_first = bool(source.leaf_off) and y is not None and newest - y <= LEAF_OFF_YEARS
    return (
        source.pixel_class,
        0 if leaf_first else 1,
        -(y or 0),
        source.native_pixel_m if source.native_pixel_m is not None else 99.0,
        source.id,
    )


def rank(sources: Sequence[Source], years: dict[str, int | None] | None = None) -> list[Source]:
    """The imagery sources best first (module docstring). ``years`` overrides a source's
    year with the one read at the point (``year_query``)."""
    years = years or {}

    def year_of(s: Source) -> int | None:
        return years.get(s.id, s.year)

    newest: dict[int, int] = {}
    for s in sources:
        y = year_of(s)
        if y is not None:
            newest[s.pixel_class] = max(newest.get(s.pixel_class, y), y)
    return sorted(sources, key=lambda s: rank_key(s, newest, year_of(s)))


def history_plan(
    ranked: Sequence[Source], years: dict[str, int | None] | None = None
) -> list[tuple[int, list[Source]]]:
    """For each year, newest first, the sources of that year best first."""
    years = years or {}
    by_year: dict[int, list[Source]] = {}
    for s in ranked:
        y = years.get(s.id, s.year)
        if y is not None:
            by_year.setdefault(y, []).append(s)
    return sorted(by_year.items(), key=lambda kv: -kv[0])
