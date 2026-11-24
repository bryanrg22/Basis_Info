"""One gather of the public sources around a study's pin, start to end (EVAL_REPORT 101).

In order, each source on its own (one failing never stops the others):

1. **Parcels** from every open parcel layer covering the point: the parcel under the pin and the
   owner's adjacent ones (:mod:`.parcels`).
2. **The current view**: the covering imagery sources ranked (:func:`basis.geo.registry.rank`,
   with the year read at the point where a mosaic mixes years); the first that answers with real
   imagery is kept as ``current``.
3. **History** (``GEO_HISTORY``): for every year a covering source has, the best source of that
   year, as ``history``; the current view's own year is not fetched twice.
4. **Lidar** (``GEO_LIDAR``): the summary over the parcel's outline plus 10 m, else 40 m each way
   from the pin (:mod:`.lidar`).
5. **The site check** against the appraisal (:mod:`.site_check`), recorded on the timeline.

Each source's outcome (``ok``, ``blank``: the point is outside its coverage, ``none``: nothing
there, ``failed``) goes into the gather's ``sources``; a failure is also an incident (code and
source) and a ``geo.source_failed`` timeline event, and the worker logs it with its
``error_code``. A retried activity skips the views and parcels it already stored.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final

from basis.errors.codes import E
from basis.geo import imagery, lidar, parcels, registry, site_check
from basis.geo.geometry import geojson_bounds
from basis.geo.http import PoliteClient, SourceError
from basis.geo.registry import Source
from basis.geo.store import GatherRow, GeoStore, Incident, ViewRecord
from basis.storage import Storage

logger = logging.getLogger(__name__)

LIDAR_MARGIN_M: Final = 10.0
LIDAR_NO_PARCEL_HALF_M: Final = 40.0
LIDAR_SOURCE: Final = "us-3dep-lidar"


@dataclass(frozen=True, slots=True)
class GatherConfig:
    extent_m: float = 160.0
    max_px: int = 2048
    history: bool = True
    lidar: bool = True
    lidar_max_nodes: int = 64
    tolerance_pct: float = 5.0

    @classmethod
    def of(cls, settings: Any) -> GatherConfig:
        return cls(
            extent_m=settings.geo_extent_m,
            max_px=settings.geo_max_image_px,
            history=settings.geo_history,
            lidar=settings.geo_lidar,
            lidar_max_nodes=settings.geo_lidar_max_nodes,
            tolerance_pct=settings.geo_site_tolerance_pct,
        )


def gather_prefix(bucket: str, study_id: uuid.UUID, gather_id: uuid.UUID) -> str:
    """Under the study's own prefix, so the retention purge takes it with the study's files."""
    return f"gs://{bucket}/studies/{study_id}/public/{gather_id}/"


def view_label(kind: str, source_id: str, year: int | None) -> str:
    return f"{kind}-{source_id}" if year is None else f"{kind}-{source_id}-{year}"


class Gatherer:
    def __init__(
        self,
        store: GeoStore,
        storage: Storage,
        bucket: str,
        client: PoliteClient,
        *,
        config: GatherConfig,
        sources: Sequence[Source] | None = None,
        heartbeat: Callable[[str], None] = lambda _step: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._storage = storage
        self._bucket = bucket
        self._client = client
        self._config = config
        self._sources = tuple(sources) if sources is not None else registry.load()
        self._beat = heartbeat
        self._now = clock

    async def run(self, gather_id: uuid.UUID) -> str:
        """Run the gather; the final status (``succeeded``, or what it already was)."""
        row = await self._store.start(gather_id, now=self._now())
        if row is None:
            existing = await self._store.gather(gather_id)
            return "missing" if existing is None else existing.status
        outcomes: list[dict[str, Any]] = []
        found = await self._parcels(row, outcomes)
        stored = await self._store.labels(row.id)
        current, years = await self._imagery(row, outcomes, stored)
        if self._config.history:
            await self._history(row, outcomes, stored, current, years)
        summary = await self._lidar(row, outcomes, found) if self._config.lidar else None
        await self._site_check(row, found)
        await self._store.finish(
            row.id, sources=outcomes, lidar=summary, code=E.GEO_COMPLETED, now=self._now()
        )
        logger.info(
            "public geo gathered",
            extra={
                "gather_id": str(row.id),
                "study_id": str(row.study_id),
                "sources": len(outcomes),
                "failed": sum(1 for o in outcomes if o["status"] == "failed"),
            },
        )
        return "succeeded"

    # ------------------------------------------------------------------ helpers
    async def _failed(
        self,
        row: GatherRow,
        outcomes: list[dict[str, Any]],
        source_id: str,
        kind: str,
        exc: SourceError,
        started: float,
    ) -> None:
        outcome = {
            "source": source_id,
            "kind": kind,
            "status": "failed",
            "code": exc.code,
            "detail": exc.detail[:300],
            "seconds": round(time.monotonic() - started, 2),
        }
        outcomes.append(outcome)
        logger.warning(
            "public source failed",
            extra={
                "error_code": exc.code,
                "source_id": source_id,
                "gather_id": str(row.id),
                "http_status": exc.status,
            },
        )
        await self._store.source_failed(
            row,
            outcome,
            Incident(
                code=exc.code,
                source_id=source_id,
                at=self._now(),
                study_id=row.study_id,
                gather_id=row.id,
                url=exc.url,
                http_status=exc.status,
                detail=exc.detail[:500],
            ),
        )

    async def _parcels(
        self, row: GatherRow, outcomes: list[dict[str, Any]]
    ) -> list[parcels.Parcel]:
        layers = registry.covering(self._sources, row.lat, row.lon, "parcels")
        layers = [s for s in layers if s.protocol == "arcgis-feature"]
        if not layers:
            outcomes.append(
                {"source": None, "kind": "parcels", "status": "none", "code": E.GEO_NO_PARCEL}
            )
            return []
        already = await self._store.has_parcels(row.id)
        found: list[parcels.Parcel] = []
        for source in layers:
            self._beat(f"parcels {source.id}")
            started = time.monotonic()
            try:
                got = await parcels.parcels_at(self._client, source, row.lat, row.lon)
            except SourceError as exc:
                await self._failed(row, outcomes, source.id, "parcels", exc, started)
                continue
            seconds = round(time.monotonic() - started, 2)
            if not got:
                outcomes.append(
                    {
                        "source": source.id,
                        "kind": "parcels",
                        "status": "none",
                        "code": E.GEO_NO_PARCEL,
                        "seconds": seconds,
                    }
                )
                continue
            outcomes.append(
                {
                    "source": source.id,
                    "kind": "parcels",
                    "status": "ok",
                    "seconds": seconds,
                    "parcels": len(got),
                }
            )
            if not already:
                await self._store.add_parcels(
                    row, got, credit=source.credit, terms_class=source.terms_class
                )
            found.extend(got)
            break  # one parcel layer per point (the states' layers do not overlap)
        return found

    async def _year(self, source: Source, row: GatherRow) -> int | None:
        if source.year_query is None:
            return source.year
        try:
            return await imagery.year_at(self._client, source, row.lat, row.lon)
        except SourceError:
            return source.year  # the image is still usable without the year read at the point

    async def _store_view(
        self, row: GatherRow, source: Source, kind: str, year: int | None, image: imagery.Image
    ) -> None:
        label = view_label(kind, source.id, year)
        ext = "png" if image.content_type == "image/png" else "jpg"
        uri = f"{gather_prefix(self._bucket, row.study_id, row.id)}{label}.{ext}"
        await self._storage.put(uri, image.data, image.content_type)
        await self._store.add_view(
            ViewRecord(
                gather_id=row.id,
                study_id=row.study_id,
                source_id=source.id,
                kind=kind,
                label=label,
                year=year,
                year_label=source.year_label,
                leaf_off=source.leaf_off,
                lat=row.lat,
                lon=row.lon,
                pixel_size_m=image.box.pixel_size_m,
                native_pixel_m=source.native_pixel_m,
                bbox=image.box.bbox,
                width=image.box.width,
                height=image.box.height,
                uri=uri,
                content_type=image.content_type,
                sha256=hashlib.sha256(image.data).hexdigest(),
                bytes=len(image.data),
                credit=source.credit,
                terms_class=source.terms_class,
                terms_url=source.terms_url,
                captured_at=self._now(),
            )
        )

    async def _try(
        self,
        row: GatherRow,
        outcomes: list[dict[str, Any]],
        source: Source,
        kind: str,
        year: int | None,
        stored: set[str],
    ) -> bool:
        """Fetch and keep one view; True when it is stored (now or by an earlier attempt)."""
        if view_label(kind, source.id, year) in stored:
            outcomes.append(
                {"source": source.id, "kind": kind, "status": "ok", "year": year, "kept": "earlier"}
            )
            return True
        self._beat(f"{kind} {source.id}")
        started = time.monotonic()
        try:
            image = await imagery.fetch(
                self._client,
                source,
                row.lat,
                row.lon,
                extent_m=self._config.extent_m,
                max_px=self._config.max_px,
            )
        except SourceError as exc:
            await self._failed(row, outcomes, source.id, kind, exc, started)
            return False
        seconds = round(time.monotonic() - started, 2)
        if image.blank:
            outcomes.append(
                {
                    "source": source.id,
                    "kind": kind,
                    "status": "blank",
                    "year": year,
                    "seconds": seconds,
                }
            )
            return False
        await self._store_view(row, source, kind, year, image)
        outcomes.append(
            {
                "source": source.id,
                "kind": kind,
                "status": "ok",
                "year": year,
                "seconds": seconds,
                "pixel_size_m": round(image.box.pixel_size_m, 4),
                "bytes": len(image.data),
            }
        )
        return True

    async def _imagery(
        self, row: GatherRow, outcomes: list[dict[str, Any]], stored: set[str]
    ) -> tuple[tuple[str, int | None] | None, dict[str, int | None]]:
        candidates = registry.covering(self._sources, row.lat, row.lon, "imagery")
        years = {s.id: await self._year(s, row) for s in candidates if s.year_query is not None}
        for source in registry.rank(candidates, years):
            year = years.get(source.id, source.year)
            if await self._try(row, outcomes, source, "current", year, stored):
                return (source.id, year), years
        outcomes.append(
            {"source": None, "kind": "current", "status": "none", "code": E.GEO_NO_IMAGERY}
        )
        return None, years

    async def _history(
        self,
        row: GatherRow,
        outcomes: list[dict[str, Any]],
        stored: set[str],
        current: tuple[str, int | None] | None,
        years: dict[str, int | None],
    ) -> None:
        candidates = registry.covering(self._sources, row.lat, row.lon, "imagery")
        for year, ranked in registry.history_plan(registry.rank(candidates, years), years):
            if current is not None and current[1] == year:
                continue  # the current view is that year's view
            for source in ranked:
                if await self._try(row, outcomes, source, "history", year, stored):
                    break

    async def _lidar(
        self, row: GatherRow, outcomes: list[dict[str, Any]], found: Sequence[parcels.Parcel]
    ) -> dict[str, Any] | None:
        self._beat("lidar")
        under = [p for p in found if p.under_pin]
        if under:
            w, s, e, n = geojson_bounds(under[0].geometry)
            pad = lidar.box_around((w + e) / 2, (s + n) / 2, LIDAR_MARGIN_M)
            grow = (pad[2] - pad[0]) / 2, (pad[3] - pad[1]) / 2
            box = (w - grow[0], s - grow[1], e + grow[0], n + grow[1])
        else:
            box = lidar.box_around(row.lon, row.lat, LIDAR_NO_PARCEL_HALF_M)
        started = time.monotonic()
        try:
            summary = await lidar.summarize(
                self._client, row.lon, row.lat, box, max_nodes=self._config.lidar_max_nodes
            )
        except SourceError as exc:
            await self._failed(row, outcomes, LIDAR_SOURCE, "lidar", exc, started)
            return None
        seconds = round(time.monotonic() - started, 2)
        if summary is None:
            outcomes.append(
                {
                    "source": LIDAR_SOURCE,
                    "kind": "lidar",
                    "status": "none",
                    "code": E.GEO_NO_LIDAR,
                    "seconds": seconds,
                }
            )
            return None
        outcomes.append(
            {
                "source": LIDAR_SOURCE,
                "kind": "lidar",
                "status": "ok",
                "seconds": seconds,
                "nodes": summary.nodes,
                "bytes": summary.bytes,
            }
        )
        out = asdict(summary)
        out["box_lonlat"] = list(box)
        out["credit"] = lidar.CREDIT
        out["terms_class"] = "public-domain"
        out["terms_url"] = lidar.TERMS_URL
        out["code"] = None if summary.building is not None else E.GEO_NO_BUILDING
        return out

    async def _site_check(self, row: GatherRow, found: Sequence[parcels.Parcel]) -> None:
        appraisal = await self._store.appraisal_site_sqft(row.study_id)
        result = site_check.check(found, appraisal, tolerance_pct=self._config.tolerance_pct)
        if result.status in ("no_parcel", "no_appraisal"):
            return
        detail = asdict(result)
        detail["parcels"] = list(result.parcels)
        await self._store.site_checked(row, detail, result.code)
