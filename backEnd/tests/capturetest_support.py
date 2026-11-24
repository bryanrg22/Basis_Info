"""A scripted browser, a clock that only moves when told, and PNGs that look like a map or
like nothing, for the capture tests (no network, no Chromium)."""

from __future__ import annotations

import io
import random
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from PIL import Image

from basis.capture.browser import BrowserCrashed, CaptureBrowser
from basis.capture.camera import parse
from basis.capture.signals import PageRecord, Response

START = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
PLACE = "https://earth.google.com/web/search/1+Main/@41.600000,-93.6000000,250.10a,589.16d,35y,0h,0t,0r/data=x"


def map_png(width: int = 1440, height: int = 900, seed: int = 1) -> bytes:
    """Coloured noise in 8 x 8 blocks: a frame with plenty of spread, like imagery."""
    rng = random.Random(seed)
    small = Image.new("RGB", (width // 8, height // 8))
    small.putdata(
        [
            (rng.randrange(40, 200), rng.randrange(60, 200), rng.randrange(30, 150))
            for _ in range((width // 8) * (height // 8))
        ]
    )
    buffer = io.BytesIO()
    small.resize((width, height), Image.Resampling.NEAREST).save(buffer, "PNG")
    return buffer.getvalue()


def white_page_png(width: int = 1440, height: int = 900) -> bytes:
    """A white page with a few dark lines of text, like the viewer's projects page."""
    from PIL import ImageDraw

    image = Image.new("RGB", (width, height), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    for y in range(120, 860, 90):
        draw.rectangle((320, y, 700, y + 2), fill=(40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def black_png(width: int = 1440, height: int = 900) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (2, 2, 3)).save(buffer, "PNG")
    return buffer.getvalue()


@dataclass
class Clock:
    now: datetime = START
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class FakeBrowser:
    """Answers ``load`` with :attr:`landing` (the search's result page) and ``move`` with the
    page at the address moved to. ``on_load`` / ``on_move`` replace the answer for the n-th
    call (0-based) with a page, an exception, or a callable."""

    landing: str = PLACE
    html: str = "<html><body>map</body></html>"
    png: bytes = field(default_factory=map_png)
    date: str | None = "8/4/2026"
    label: str | None = "1 Main St, Anytown, IA"
    on_load: dict[int, PageRecord | BaseException] = field(default_factory=dict)
    on_move: dict[int, PageRecord | BaseException] = field(default_factory=dict)
    blank_moves: set[int] = field(default_factory=set)
    loads: list[str] = field(default_factory=list)
    moves: list[str] = field(default_factory=list)
    url: str = "about:blank"
    closed: bool = False
    _last: PageRecord | None = None
    # EVAL_REPORT 99. History: the dates the viewer offers, newest first, as the page shows
    # them (None: no history control); ``history_blank`` the indexes that render black.
    history_dates: list[str] | None = None
    history_blank: set[int] = field(default_factory=set)
    history_index: int | None = None
    history_presses: int = 0
    # Street level: the address the panorama lookup lands on and its failed responses, and
    # the failed responses and picture after the camera turns to the house.
    street_landing: str | None = None
    street_failed: tuple[Response, ...] = ()
    street_aim_failed: tuple[Response, ...] = ()
    street_png: bytes | None = None
    # The screenshot's height (the clean layout's viewport is taller than 900).
    height: int = 900
    settles: list[tuple[float, bool]] = field(default_factory=list)

    async def load(self, url: str) -> PageRecord:
        n = len(self.loads)
        self.loads.append(url)
        scripted = self.on_load.get(n)
        if isinstance(scripted, BaseException):
            raise scripted
        if scripted is not None:
            self.url = scripted.url
            self._last = scripted
            return scripted
        self.history_index = None
        if "map_action=pano" in url and self.street_landing is not None:
            self.url = self.street_landing
            self._last = PageRecord(
                url=self.url, status=200, title="Maps", html=self.html, failed=self.street_failed
            )
            return self._last
        self.url = self.landing if "/search/" in url else url
        self._last = PageRecord(url=self.url, status=200, title="Google Earth", html=self.html)
        return self._last

    async def move(self, url: str) -> PageRecord:
        n = len(self.moves)
        self.moves.append(url)
        scripted = self.on_move.get(n)
        if isinstance(scripted, BaseException):
            raise scripted
        if scripted is not None:
            self._last = scripted
            return scripted
        self.url = url
        failed = self.street_aim_failed if "/maps/@" in url else ()
        self._last = PageRecord(
            url=url, status=None, title="Google Earth", html=self.html, failed=failed
        )
        return self._last

    async def settle(self, seconds: float, *, dismiss: bool = True) -> None:
        self.settles.append((seconds, dismiss))

    async def history_on(self) -> PageRecord | None:
        if self.history_dates is None:
            return None
        self.history_index = 0
        self.history_presses += 1
        return await self.current()

    async def history_older(self) -> PageRecord | None:
        if self.history_dates is None or self.history_index is None:
            return None
        if self.history_index + 1 >= len(self.history_dates):
            return None
        self.history_index += 1
        self.history_presses += 1
        return await self.current()

    async def current(self) -> PageRecord:
        if self._last is not None:
            return self._last
        return PageRecord(url=self.url, status=None, html=self.html)

    async def screenshot(self) -> bytes:
        if self.closed:
            raise BrowserCrashed("closed")
        if self.history_index is not None:
            if self.history_index in self.history_blank:
                return black_png(height=self.height)
            return map_png(height=self.height, seed=100 + self.history_index)
        if "/maps/@" in self.url and self.street_png is not None:
            return self.street_png
        if (len(self.moves) - 1) in self.blank_moves:
            return black_png(height=self.height)
        if self.height != 900:
            return map_png(height=self.height)
        return self.png

    async def imagery_date(self) -> str | None:
        if self.history_index is not None and self.history_dates is not None:
            return f"older-{self.history_dates[self.history_index]}"
        return self.date

    async def place_label(self) -> str | None:
        return self.label


def factory(browser: FakeBrowser) -> Callable[[], object]:
    @asynccontextmanager
    async def opened() -> AsyncIterator[CaptureBrowser]:
        try:
            yield browser
        finally:
            browser.closed = True

    return opened


def camera_of(url: str) -> tuple[float, float, float, float, float] | None:
    camera = parse(url)
    if camera is None:
        return None
    return camera.range_m, camera.heading_deg, camera.tilt_deg, camera.lat, camera.lon


@dataclass
class MemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    fail: bool = False

    async def put(self, uri: str, data: bytes, content_type: str) -> None:
        if self.fail:
            raise OSError("storage down")
        self.objects[uri] = (data, content_type)

    async def read(self, uri: str) -> bytes:
        return self.objects[uri][0]

    async def list(self, prefix: str) -> list[str]:
        return sorted(u for u in self.objects if u.startswith(prefix))


def failed_page(
    url: str, status: int | None, *failed: tuple[int, str], html: str = ""
) -> PageRecord:
    return PageRecord(
        url=url, status=status, html=html, failed=tuple(Response(s, u) for s, u in failed)
    )
