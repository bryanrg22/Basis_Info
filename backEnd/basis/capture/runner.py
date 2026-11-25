"""One capture, start to end, and what every failure leaves behind (EVAL_REPORT 98).

:meth:`CaptureRunner.run` takes the lease (one capture at a time across every worker), asks
the governor before every navigation, waits the spacing, navigates, checks the page for a
block after every navigation, waits for the imagery, takes the planned views it does not have
yet, checks each for a blank render, stores it with its camera, and records the result. It
returns what the workflow does next: nothing (:class:`Done`, :class:`Failed`) or wait until a
time and run again (:class:`Waiting`).

Every failure and pause goes the same way (the coordinator's requirement and the shape of the
client-error capture): its own ``BASIS-CAPTURE-<nnn>`` code; a structured log line carrying
``error_code``; the study's timeline event (``capture.paused`` / ``capture.failed`` with the
engineer-facing reason, which never names the provider); the grouped incident for the code;
for a block signal the breaker's trip and its one alert; and, when a page was involved, the
evidence kept with the capture's files (``.../captures/<id>/evidence/<time>-<code>/``: the
screenshot, the HTML and ``page.json`` with the address, the HTTP status and the failed
responses), never in the repository. Views already stored are kept across a pause, and a
resumed capture takes only the ones it is missing.

EVAL_REPORT 99 adds, each behind :class:`CaptureOptions` (whose defaults are EVAL_REPORT 98's
capture exactly; :meth:`CaptureOptions.of` reads the settings):

* clean views: a taller viewport cropped to the band between the margins
  (:mod:`basis.capture.frame`), the status strip kept beside the view as the date's evidence;
* the imagery date read from the status bar (:mod:`basis.capture.imagery_date`) and a
  leaf-off label (:mod:`basis.capture.leaf`) on every dated view;
* history: the dated history of the straight-down view, walked with the viewer's arrow, and
  the dates :func:`basis.capture.history.select` keeps;
* street level (:mod:`basis.capture.street`), one more page load.

A part that does not work (no history, no street-level picture, an unread date) leaves a
note on the capture, an incident and a ``capture.part_failed`` event, with evidence where a
page was involved, and the capture goes on: it never fails the capture or opens the breaker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from basis.capture import governor as gov
from basis.capture import history as hist
from basis.capture import leaf as leafs
from basis.capture import messages, street
from basis.capture.browser import BrowserCrashed, BrowserFactory, CaptureBrowser
from basis.capture.camera import (
    ORIGIN,
    Camera,
    found_nothing,
    parse,
    pixel_size_m,
    search_address,
)
from basis.capture.frame import Layout, crop_view
from basis.capture.governor import GovernorConfig, NavigationKind
from basis.capture.imagery_date import (
    DateReading,
    address_date,
    agreed,
    parse_status_text,
    read_status_date,
)
from basis.capture.plan import PlannedView, plan
from basis.capture.signals import Frame, PageRecord, Signal, detect, frame_stats
from basis.capture.store import CaptureRow, CaptureStore, Incident, NewView, Note, PinRow
from basis.config import Settings
from basis.errors.codes import E
from basis.storage import Storage

logger = logging.getLogger(__name__)

LEASE_TTL: Final = timedelta(minutes=10)
TURN_RETRY: Final = timedelta(minutes=2)
# The fov a point-first camera opens with, and its starting range (the plan sets each view's).
DEFAULT_FOV_DEG: Final = 35.0
START_RANGE_M: Final = 250.0
# The map without the toolbar above it and the status bar below it, for the blank check.
TOOLBAR_PX: Final = 90
STATUS_BAR_PX: Final = 32


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """What a capture takes beyond EVAL_REPORT 98's six views. The defaults are that capture
    exactly (no crop, the browser's own date, no history, no street level), which the
    regression tests hold; :meth:`of` is what the worker runs (EVAL_REPORT 99)."""

    origin: str = ORIGIN
    layout: Layout = Layout()
    read_dates: bool = False
    tesseract: str | None = None
    history: bool = False
    history_window_days: int = 730
    history_max_views: int = 6
    history_max_steps: int = 40
    street_view: bool = False
    street_origin: str = street.MAPS_ORIGIN

    @classmethod
    def of(cls, settings: Settings) -> CaptureOptions:
        from basis.capture.imagery_date import tesseract_path

        margin = settings.capture_frame_margin_px if settings.capture_clean_views else 0
        return cls(
            origin=settings.capture_viewer_origin,
            layout=Layout(margin=margin),
            read_dates=True,
            tesseract=tesseract_path(settings.capture_tesseract),
            history=settings.capture_history,
            history_window_days=settings.capture_history_window_days,
            history_max_views=settings.capture_history_max_views,
            history_max_steps=settings.capture_history_max_steps,
            street_view=settings.capture_street_view,
        )


@dataclass(frozen=True, slots=True)
class Shot:
    """A screenshot made ready to store: the view (cropped clean when the layout says so),
    its size, its frame statistics, the status strip and the date read from it."""

    view: bytes
    width: int
    height: int
    frame: Frame
    strip: bytes
    date: DateReading
    green: leafs.Greenness | None


@dataclass(frozen=True, slots=True)
class Done:
    views: int


@dataclass(frozen=True, slots=True)
class Waiting:
    until: datetime
    code: str | None


@dataclass(frozen=True, slots=True)
class Failed:
    code: str


Outcome = Done | Waiting | Failed


class _Stop(Exception):
    """Unwinds the capture with the outcome already recorded."""

    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome


def capture_prefix(bucket: str, study_id: uuid.UUID, capture_id: uuid.UUID) -> str:
    """Under the study's prefix, so the retention purge removes it with the study's files."""
    return f"gs://{bucket}/studies/{study_id}/captures/{capture_id}/"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaptureRunner:
    def __init__(
        self,
        store: CaptureStore,
        storage: Storage,
        bucket: str,
        browser: BrowserFactory,
        *,
        config: GovernorConfig,
        holder: str,
        settle_s: float = 12.0,
        source_text: str = "Aerial imagery",
        street_source_text: str = "Street-level imagery",
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        heartbeat: Callable[[str], None] | None = None,
        options: CaptureOptions | None = None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._bucket = bucket
        self._browser = browser
        self._config = config
        self._holder = holder
        self._settle_s = settle_s
        self._source = source_text
        self._street_source = street_source_text
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.SystemRandom()
        self._heartbeat = heartbeat or (lambda _step: None)
        self._options = options or CaptureOptions()
        self._undated_noted = False

    # ------------------------------------------------------------------ entry point
    async def run(self, capture_id: uuid.UUID) -> Outcome:
        row = await self._store.capture(capture_id)
        if row is None:
            raise LookupError(f"no capture {capture_id}")
        if row.status == "succeeded":
            return Done(row.views)
        if row.status == "failed":
            return Failed(row.code or E.CAPTURE_GAVE_UP)
        pin = await self._store.pin(row.study_id)
        if pin is None or (pin.address is None and not pin.has_point):
            return await self._fail(row, E.CAPTURE_NO_PLACE, "the study has no address or pin")
        now = self._clock()
        if not await self._store.acquire_lease(self._holder, LEASE_TTL, now):
            until = now + TURN_RETRY
            await self._store.wait_turn(row.id, messages.WAITING_TURN, until)
            return Waiting(until, None)
        try:
            return await self._capture(row, pin)
        except _Stop as stop:
            return stop.outcome
        finally:
            await self._store.release_lease(self._holder)

    # ------------------------------------------------------------------ the capture
    async def _capture(self, row: CaptureRow, pin: PinRow) -> Outcome:
        await self._admit(row, "load")
        await self._store.start(row.id, self._clock())
        have = await self._store.labels(row.id)
        self._undated_noted = False
        try:
            async with self._browser() as browser:
                centre = await self._locate(row, pin, browser)
                stored = len(have)
                planned = plan(centre, self._options.layout)
                for view in planned:
                    if view.label in have:
                        continue
                    await self._take(row, browser, view)
                    stored += 1
                    await self._progress(view.label)
                # A resumed capture does not repeat a part already done or already noted as
                # not working: the walk stored its dates, or there was nothing to walk.
                noted = {n.get("code") for n in (await self._current(row)).notes}
                if self._options.history and not (
                    any(label.startswith(hist.LABEL_PREFIX) for label in have)
                    or E.CAPTURE_HISTORY_UNAVAILABLE in noted
                ):
                    stored += await self._history(row, browser, planned[0], have)
                if self._options.street_view and not (
                    noted & {E.CAPTURE_STREET_NO_COVERAGE, E.CAPTURE_STREET_REFUSED}
                ):
                    stored += await self._street(row, browser, centre, have)
        except _Stop:
            raise
        except TimeoutError:
            return await self._fail(row, E.CAPTURE_TIMEOUT, "a load or the imagery timed out")
        except BrowserCrashed as exc:
            return await self._fail(row, E.CAPTURE_BROWSER_CRASH, str(exc)[:300] or "crashed")
        await self._store.complete(row.id, views=stored, now=self._clock())
        logger.info(
            "capture completed",
            extra={
                "error_code": E.CAPTURE_COMPLETED,
                "capture_id": str(row.id),
                "study_id": str(row.study_id),
                "views": stored,
            },
        )
        return Done(stored)

    async def _current(self, row: CaptureRow) -> CaptureRow:
        return await self._store.capture(row.id) or row

    async def _progress(self, step: str) -> None:
        await self._store.renew_lease(self._holder, LEASE_TTL, self._clock())
        self._heartbeat(step)

    async def _locate(self, row: CaptureRow, pin: PinRow, browser: CaptureBrowser) -> Camera:
        """The first page load: a search for the pin's point (``lat,lon``) or its address.
        The camera the page lands on gives the ground altitude; the views are centred on the
        engineer's point when there is one, else on the place the search found.

        A point is searched rather than loaded as a camera address because a camera address
        needs the ground's altitude, which only the page knows: run 3 of EVAL_REPORT 98
        loaded ``...,0a,...`` and every view's camera then sat below the ground, the viewer
        raised it, and the recorded range and pixel size were 28 % off."""
        if pin.has_point:
            assert pin.lat is not None and pin.lon is not None
            url = search_address(f"{pin.lat:.7f},{pin.lon:.7f}", self._options.origin)
        else:
            assert pin.address is not None
            url = search_address(pin.address, self._options.origin)
        await self._navigate(row, browser, "load", url)
        await browser.settle(self._settle_s)
        landed = await browser.current()
        await self._check(row, browser, landed)
        camera = parse(landed.url)
        if camera is None or (not pin.has_point and found_nothing(camera)):
            await self._stop_failed(
                row, browser, E.CAPTURE_NO_PLACE, "the page named no place", landed
            )
        assert camera is not None
        if pin.has_point:
            assert pin.lat is not None and pin.lon is not None
            # Keep the engineer's point exactly; take the ground altitude the page found.
            return Camera(pin.lat, pin.lon, camera.alt_m, START_RANGE_M, DEFAULT_FOV_DEG)
        label = await browser.place_label()
        await self._store.place_pin(row.id, camera.lat, camera.lon, label, self._clock())
        return Camera(camera.lat, camera.lon, camera.alt_m, START_RANGE_M, camera.fov_deg)

    async def _take(self, row: CaptureRow, browser: CaptureBrowser, view: PlannedView) -> None:
        await self._navigate(row, browser, "move", view.camera.address(self._options.origin))
        await browser.settle(self._settle_s)
        png = await browser.screenshot()
        shot = await self._shoot(png, browser, top_down=view.top_down)
        if shot.frame.blank:
            await self._stop_failed(
                row,
                browser,
                E.CAPTURE_BLANK_RENDER,
                f"{view.label}: mean {shot.frame.mean:.1f}, spread {shot.frame.stddev:.1f}",
                await browser.current(),
                screenshot=png,
            )
        await self._store_view(row, view, shot)

    async def _shoot(self, png: bytes, browser: CaptureBrowser, *, top_down: bool) -> Shot:
        """Crop (clean views), measure the frame, read the date (OCR on a thread, or what
        the browser reports in EVAL_REPORT 98's capture), and measure the green share."""
        options = self._options
        latest = self._clock().year + 1
        cropped = await asyncio.to_thread(crop_view, png, options.layout)
        frame, _size = await asyncio.to_thread(_inspect, cropped.view, options.layout.margin == 0)
        if options.read_dates:
            reading = await asyncio.to_thread(
                read_status_date, cropped.status_strip, options.tesseract, latest_year=latest
            )
        else:
            reading = _browser_date(await browser.imagery_date(), latest)
        green = await asyncio.to_thread(leafs.greenness, cropped.view) if top_down else None
        return Shot(
            cropped.view, cropped.width, cropped.height, frame, cropped.status_strip, reading, green
        )

    async def _store_view(
        self,
        row: CaptureRow,
        view: PlannedView,
        shot: Shot,
        *,
        label: str | None = None,
        camera_at: street.Pano | None = None,
    ) -> None:
        label = label or view.label
        current = await self._store.capture(row.id)
        pin_version = current.pin_version if current is not None else row.pin_version
        prefix = capture_prefix(self._bucket, row.study_id, row.id)
        uri = f"{prefix}{label}.png"
        await self._storage.put(uri, shot.view, "image/png")
        if self._options.read_dates and shot.strip:
            await self._storage.put(f"{prefix}{label}.status.png", shot.strip, "image/png")
        camera = view.camera
        layout = self._options.layout
        if not view.top_down or camera_at is not None:
            size = None
        elif layout.margin:
            size = layout.pixel_size_m(camera.range_m, camera.fov_deg)
        else:
            size = pixel_size_m(camera.range_m, camera.fov_deg, shot.height)
        reading = shot.date
        leaf, _why = leafs.by_month(reading.on, camera.lat)
        if reading.on is None and self._options.read_dates and view.kind != "street":
            await self._undated(row, label, reading.note)
        await self._store.add_view(
            NewView(
                study_id=row.study_id,
                capture_id=row.id,
                pin_version=pin_version,
                kind=view.kind,
                label=label,
                lat=camera.lat,
                lon=camera.lon,
                ground_alt_m=camera.alt_m,
                range_m=camera.range_m,
                fov_deg=camera.fov_deg,
                heading_deg=camera.heading_deg,
                # A street-level camera looks at the horizon (90 from straight down).
                tilt_deg=90.0 if view.kind == "street" else camera.tilt_deg,
                pixel_size_m=size,
                imagery_date=reading.text,
                width=shot.width,
                height=shot.height,
                uri=uri,
                sha256=hashlib.sha256(shot.view).hexdigest(),
                bytes=len(shot.view),
                source=self._street_source if view.kind == "street" else self._source,
                captured_at=self._clock(),
                imagery_on=reading.on,
                imagery_date_note=reading.note,
                leaf=leaf if reading.on is not None else "unknown",
                green_share=None if shot.green is None else shot.green.share,
                camera_lat=None if camera_at is None else camera_at.lat,
                camera_lon=None if camera_at is None else camera_at.lon,
                pano_id=None if camera_at is None else camera_at.pano_id,
            )
        )

    async def _undated(self, row: CaptureRow, label: str, why: str) -> None:
        """One note and incident per capture for unread dates; a log line for each view."""
        logger.warning(
            "capture date unread",
            extra={
                "error_code": E.CAPTURE_DATE_UNREAD,
                "capture_id": str(row.id),
                "view": label,
                "detail": why,
            },
        )
        if self._undated_noted:
            return
        self._undated_noted = True
        await self._part_failed(row, "date", E.CAPTURE_DATE_UNREAD, f"{label}: {why}")

    async def _part_failed(
        self,
        row: CaptureRow,
        part: str,
        code: str,
        detail: str,
        *,
        browser: CaptureBrowser | None = None,
        page: PageRecord | None = None,
        screenshot: bytes | None = None,
    ) -> None:
        evidence = None
        if browser is not None and page is not None:
            evidence = await self._evidence(row, browser, code, None, page, screenshot=screenshot)
        incident = Incident(
            code=code,
            at=self._clock(),
            study_id=row.study_id,
            capture_id=row.id,
            url=None if page is None else page.url,
            http_status=None if page is None else page.status,
            detail=detail[:500],
            evidence=evidence,
        )
        await self._store.part_failed(row.id, Note(part, code, messages.part(code)), incident)
        logger.warning(
            "capture part failed",
            extra={
                "error_code": code,
                "capture_id": str(row.id),
                "study_id": str(row.study_id),
                "part": part,
                "detail": detail[:500],
                "evidence": evidence,
            },
        )

    # ------------------------------------------------------------------ history
    async def _history(
        self, row: CaptureRow, browser: CaptureBrowser, close: PlannedView, have: set[str]
    ) -> int:
        """Walk the dated history of ``aerial-close``'s camera and store the dates the plan
        keeps (EVAL_REPORT 99); the number stored."""
        options = self._options
        await self._navigate(row, browser, "move", close.camera.address(options.origin))
        await browser.settle(self._settle_s)
        page = await self._press(row, browser, browser.history_on)
        if page is None:
            await self._part_failed(
                row,
                "history",
                E.CAPTURE_HISTORY_UNAVAILABLE,
                "the viewer showed no history control",
                browser=browser,
                page=await browser.current(),
            )
            return 0
        await browser.settle(self._settle_s)
        found: dict[date, Shot] = {}
        unread = 0
        repeats = 0
        steps = 0
        while True:
            png = await browser.screenshot()
            shot = await self._shoot(png, browser, top_down=True)
            if self._options.read_dates:
                checked = agreed(shot.date, address_date((await browser.current()).url))
                shot = Shot(
                    shot.view, shot.width, shot.height, shot.frame, shot.strip, checked, shot.green
                )
            when = shot.date.on
            if when is None:
                unread += 1
                logger.info(
                    "capture history date unread",
                    extra={
                        "error_code": E.CAPTURE_DATE_UNREAD,
                        "capture_id": str(row.id),
                        "step": steps,
                        "detail": shot.date.note,
                    },
                )
            elif when in found:
                repeats += 1
            else:
                repeats = 0
                if shot.frame.blank:
                    logger.info(
                        "capture history date skipped: blank frame",
                        extra={"capture_id": str(row.id), "date": when.isoformat()},
                    )
                else:
                    found[when] = shot
            self._heartbeat(f"history {when}")
            if steps >= options.history_max_steps or repeats >= 2:
                break
            page = await self._press(row, browser, browser.history_older)
            if page is None:
                break
            steps += 1
            await browser.settle(self._settle_s)
        if not found:
            await self._part_failed(
                row,
                "history",
                E.CAPTURE_HISTORY_UNAVAILABLE,
                f"stepping read no date ({steps} steps, {unread} unread)",
                browser=browser,
                page=await browser.current(),
            )
            return 0
        reference = await self._store.reference_date(row.study_id)
        candidates = [hist.Candidate(on, leafs.by_month(on, close.camera.lat)[0]) for on in found]
        picks = hist.select(
            candidates,
            reference=None if reference is None else reference[0],
            window_days=options.history_window_days,
            max_views=options.history_max_views,
        )
        logger.info(
            "capture history walked",
            extra={
                "capture_id": str(row.id),
                "dates": sorted(d.isoformat() for d in found),
                "kept": [p.label for p in picks],
                "reference": None if reference is None else reference[0].isoformat(),
                "steps": steps,
                "unread": unread,
            },
        )
        stored = 0
        view = PlannedView(close.label, "history", close.camera)
        for pick in picks:
            if pick.label in have:
                continue
            await self._store_view(row, view, found[pick.on], label=pick.label)
            stored += 1
            await self._progress(pick.label)
        return stored

    async def _press(
        self,
        row: CaptureRow,
        browser: CaptureBrowser,
        control: Callable[[], Awaitable[PageRecord | None]],
    ) -> PageRecord | None:
        """A press of the viewer's history control: a camera move for the governor, the
        spacing and the block check, like :meth:`_navigate`."""
        await self._admit(row, "move")
        page = await control()
        if page is None:
            return None
        await self._store.navigated("move", row.id, self._clock())
        await self._check(row, browser, page)
        return page

    # ------------------------------------------------------------------ street level
    async def _street(
        self, row: CaptureRow, browser: CaptureBrowser, centre: Camera, have: set[str]
    ) -> int:
        """One street-level picture of the front (:mod:`basis.capture.street`); 1 when
        stored, 0 when there was none or it was refused (a note, no retry)."""
        label = "street-front"
        if label in have:
            return 0
        options = self._options
        url = street.pano_lookup_address(centre.lat, centre.lon, options.street_origin)
        await self._navigate(row, browser, "load", url)
        await browser.settle(self._settle_s, dismiss=False)
        landed = await browser.current()
        pano = street.parse_pano(landed.url)
        if pano is None:
            why = street.refused(landed)
            code = E.CAPTURE_STREET_REFUSED if why else E.CAPTURE_STREET_NO_COVERAGE
            await self._part_failed(
                row,
                "street",
                code,
                why or "the page named no panorama near the property",
                browser=browser,
                page=landed,
            )
            return 0
        if street.contributed(pano.pano_id):
            await self._part_failed(
                row,
                "street",
                E.CAPTURE_STREET_NO_COVERAGE,
                "the nearest panorama is a visitor's photo, not street imagery",
                browser=browser,
                page=landed,
            )
            return 0
        heading = street.heading_deg(pano.lat, pano.lon, centre.lat, centre.lon)
        aim = street.pano_address(pano.pano_id, pano.lat, pano.lon, heading, options.street_origin)
        page = await self._navigate(row, browser, "move", aim)
        await browser.settle(self._settle_s, dismiss=False)
        png = await browser.screenshot()
        current = await browser.current()
        refused = street.refused(page) or street.refused(current)
        frame, _size = await asyncio.to_thread(_inspect, png, True)
        if refused or frame.blank:
            why = refused or f"black frame: mean {frame.mean:.1f}, spread {frame.stddev:.1f}"
            await self._part_failed(
                row,
                "street",
                E.CAPTURE_STREET_REFUSED,
                why,
                browser=browser,
                page=current,
                screenshot=png,
            )
            return 0
        distance = street.distance_m(pano.lat, pano.lon, centre.lat, centre.lon)
        camera = Camera(
            centre.lat, centre.lon, centre.alt_m, max(distance, 0.1), street.STREET_FOV_DEG, heading
        )
        view_png, width, height = await asyncio.to_thread(street.crop_street, png)
        shot = Shot(view_png, width, height, frame, b"", DateReading(None, None, "not read"), None)
        view = PlannedView(label, "street", camera)
        await self._store_view(row, view, shot, camera_at=pano)
        await self._progress(label)
        return 1

    # ------------------------------------------------------------------ navigation
    async def _admit(self, row: CaptureRow, kind: NavigationKind) -> None:
        now = self._clock()
        state, usage = await self._store.governor(now)
        decision = gov.admit(state, usage, self._config, now, kind)
        if isinstance(decision, gov.Wait):
            await self._pause(
                row,
                Incident(
                    code=decision.code,
                    at=now,
                    study_id=row.study_id,
                    capture_id=row.id,
                    detail=decision.why,
                ),
                resume_at=decision.until,
                block=False,
            )
        wait = gov.spacing_s(state.last_navigation_at, now, self._config, self._rng)
        if wait > 0:
            await self._sleep(wait)

    async def _navigate(
        self, row: CaptureRow, browser: CaptureBrowser, kind: NavigationKind, url: str
    ) -> PageRecord:
        await self._admit(row, kind)
        page = await (browser.load(url) if kind == "load" else browser.move(url))
        await self._store.navigated(kind, row.id, self._clock())
        await self._check(row, browser, page)
        return page

    async def _check(self, row: CaptureRow, browser: CaptureBrowser, page: PageRecord) -> None:
        signal = detect(page)
        if signal is None:
            return
        now = self._clock()
        evidence = await self._evidence(row, browser, signal.code, signal, page)
        await self._pause(
            row,
            Incident(
                code=signal.code,
                at=now,
                study_id=row.study_id,
                capture_id=row.id,
                url=signal.url,
                http_status=signal.status,
                detail=signal.detail,
                evidence=evidence,
            ),
            resume_at=now,
            block=True,
        )

    # ------------------------------------------------------------------ outcomes
    async def _pause(
        self, row: CaptureRow, incident: Incident, *, resume_at: datetime, block: bool
    ) -> None:
        trip = await self._store.pause(
            row.id,
            reason=messages.PAUSED,
            resume_at=resume_at,
            incident=incident,
            config=self._config,
            block=block,
        )
        until = resume_at
        if trip is not None and trip.state.breaker_open_until is not None:
            until = max(until, trip.state.breaker_open_until)
        logger.warning(
            "capture signal" if block else "capture paused",
            extra={
                "error_code": incident.code,
                "capture_id": str(row.id),
                "study_id": str(row.study_id),
                "detail": incident.detail,
                "url": incident.url,
                "http_status": incident.http_status,
                "evidence": incident.evidence,
                "resume_at": until.isoformat(),
                "breaker_trip": None if trip is None else trip.number,
                "breaker_new_trip": None if trip is None else trip.new,
            },
        )
        if trip is not None and trip.new:
            logger.warning(
                "capture breaker opened",
                extra={
                    "error_code": E.ALERT_CAPTURE_BLOCKED,
                    "signal_code": incident.code,
                    "breaker_trip": trip.number,
                    "open_until": until.isoformat(),
                },
            )
        raise _Stop(Waiting(until, incident.code))

    async def _fail(
        self,
        row: CaptureRow,
        code: str,
        detail: str,
        *,
        evidence: str | None = None,
        page: PageRecord | None = None,
    ) -> Failed:
        incident = Incident(
            code=code,
            at=self._clock(),
            study_id=row.study_id,
            capture_id=row.id,
            url=None if page is None else page.url,
            http_status=None if page is None else page.status,
            detail=detail,
            evidence=evidence,
        )
        await self._store.fail(row.id, reason=messages.failed(code), incident=incident)
        logger.error(
            "capture failed",
            extra={
                "error_code": code,
                "capture_id": str(row.id),
                "study_id": str(row.study_id),
                "detail": detail,
                "evidence": evidence,
            },
        )
        return Failed(code)

    async def _stop_failed(
        self,
        row: CaptureRow,
        browser: CaptureBrowser,
        code: str,
        detail: str,
        page: PageRecord,
        *,
        screenshot: bytes | None = None,
    ) -> None:
        evidence = await self._evidence(row, browser, code, None, page, screenshot=screenshot)
        raise _Stop(await self._fail(row, code, detail, evidence=evidence, page=page))

    async def _evidence(
        self,
        row: CaptureRow,
        browser: CaptureBrowser,
        code: str,
        signal: Signal | None,
        page: PageRecord,
        *,
        screenshot: bytes | None = None,
    ) -> str | None:
        """Keep the page as it was at the signal: screenshot, HTML and a page record. Best
        effort: the evidence must never hide the signal itself."""
        at = self._clock()
        prefix = (
            f"{capture_prefix(self._bucket, row.study_id, row.id)}evidence/"
            f"{at.strftime('%Y%m%dT%H%M%S')}-{code}/"
        )
        try:
            current = await browser.current()
        except Exception:  # the page may be gone (a crash); keep what the signal saw
            current = page
        html = current.html or page.html
        record = {
            "code": code,
            "at": at.isoformat(),
            "url": page.url,
            "status": page.status,
            "title": current.title or page.title,
            "failed": [{"status": r.status, "url": r.url} for r in page.failed],
            "signal": None
            if signal is None
            else {"detail": signal.detail, "url": signal.url, "status": signal.status},
        }
        try:
            if screenshot is None:
                screenshot = await browser.screenshot()
            await self._storage.put(prefix + "page.png", screenshot, "image/png")
        except Exception:
            logger.warning("capture evidence: no screenshot", exc_info=True)
        try:
            await self._storage.put(prefix + "page.html", html.encode(), "text/html")
            await self._storage.put(
                prefix + "page.json", json.dumps(record, indent=1).encode(), "application/json"
            )
        except Exception:
            logger.warning("capture evidence could not be stored", exc_info=True)
            return None
        return prefix


def _inspect(png: bytes, full: bool = True) -> tuple[Frame, tuple[int, int]]:
    """The frame's statistics and the image's size (on a thread): over the map area without
    the toolbar and the status bar for a ``full`` (uncropped) screenshot, over the whole of
    a view already cropped clean."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    if not full:
        return frame_stats(png), (width, height)
    region = (0, min(TOOLBAR_PX, height - 1), width, max(TOOLBAR_PX + 1, height - STATUS_BAR_PX))
    return frame_stats(png, region=region), (width, height)


def _browser_date(text: str | None, latest_year: int) -> DateReading:
    """The date as the browser reported it (EVAL_REPORT 98's path)."""
    if text is None:
        return DateReading(None, None, "the page showed no date")
    found = parse_status_text(text, latest_year=latest_year)
    if found is None:
        return DateReading(text, None, "page text, not a date")
    return DateReading(found[0], found[1], "page text")
