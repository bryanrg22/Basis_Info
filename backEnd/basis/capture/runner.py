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
from datetime import UTC, datetime, timedelta
from typing import Final

from basis.capture import governor as gov
from basis.capture import messages
from basis.capture.browser import BrowserCrashed, BrowserFactory, CaptureBrowser
from basis.capture.camera import Camera, parse, pixel_size_m, search_address
from basis.capture.governor import GovernorConfig, NavigationKind
from basis.capture.plan import PlannedView, plan
from basis.capture.signals import Frame, PageRecord, Signal, detect, frame_stats
from basis.capture.store import CaptureRow, CaptureStore, Incident, NewView, PinRow
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
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        heartbeat: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._bucket = bucket
        self._browser = browser
        self._config = config
        self._holder = holder
        self._settle_s = settle_s
        self._source = source_text
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.SystemRandom()
        self._heartbeat = heartbeat or (lambda _step: None)

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
        try:
            async with self._browser() as browser:
                centre = await self._locate(row, pin, browser)
                stored = len(have)
                for view in plan(centre):
                    if view.label in have:
                        continue
                    await self._take(row, browser, view)
                    stored += 1
                    await self._store.renew_lease(self._holder, LEASE_TTL, self._clock())
                    self._heartbeat(view.label)
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
            url = search_address(f"{pin.lat:.7f},{pin.lon:.7f}")
        else:
            assert pin.address is not None
            url = search_address(pin.address)
        await self._navigate(row, browser, "load", url)
        await browser.settle(self._settle_s)
        landed = await browser.current()
        await self._check(row, browser, landed)
        camera = parse(landed.url)
        if camera is None:
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
        await self._navigate(row, browser, "move", view.camera.address())
        await browser.settle(self._settle_s)
        png = await browser.screenshot()
        frame, size = await asyncio.to_thread(_inspect, png)
        if frame.blank:
            await self._stop_failed(
                row,
                browser,
                E.CAPTURE_BLANK_RENDER,
                f"{view.label}: mean {frame.mean:.1f}, spread {frame.stddev:.1f}",
                await browser.current(),
                screenshot=png,
            )
        width, height = size
        current = await self._store.capture(row.id)
        pin_version = current.pin_version if current is not None else row.pin_version
        uri = f"{capture_prefix(self._bucket, row.study_id, row.id)}{view.label}.png"
        await self._storage.put(uri, png, "image/png")
        camera = view.camera
        await self._store.add_view(
            NewView(
                study_id=row.study_id,
                capture_id=row.id,
                pin_version=pin_version,
                kind=view.kind,
                label=view.label,
                lat=camera.lat,
                lon=camera.lon,
                ground_alt_m=camera.alt_m,
                range_m=camera.range_m,
                fov_deg=camera.fov_deg,
                heading_deg=camera.heading_deg,
                tilt_deg=camera.tilt_deg,
                pixel_size_m=(
                    pixel_size_m(camera.range_m, camera.fov_deg, height) if view.top_down else None
                ),
                imagery_date=await browser.imagery_date(),
                width=width,
                height=height,
                uri=uri,
                sha256=hashlib.sha256(png).hexdigest(),
                bytes=len(png),
                source=self._source,
                captured_at=self._clock(),
            )
        )

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


def _inspect(png: bytes) -> tuple[Frame, tuple[int, int]]:
    """The frame's statistics over the map area and the image's size (on a thread)."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    region = (0, min(TOOLBAR_PX, height - 1), width, max(TOOLBAR_PX + 1, height - STATUS_BAR_PX))
    return frame_stats(png, region=region), (width, height)
