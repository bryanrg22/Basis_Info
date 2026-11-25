"""The real :class:`~basis.capture.browser.CaptureBrowser`: headless Chromium through
Playwright (``playwright==1.64.1``, the release whose Chromium build is 1248; EVAL_REPORT 98).

* One persistent profile (``CAPTURE_PROFILE_DIR``) so the viewer's first-visit notices are
  dismissed once, as a person's browser would keep them; a 1440 x 900 viewport at one device
  pixel per CSS pixel, so a screenshot pixel is a viewport pixel (the pixel size depends on
  it); the browser's own user agent (no disguise).
* ``move`` changes the page's address with ``history.pushState`` and a ``popstate`` event,
  which the viewer follows without a page load (EVAL_REPORT 98 probe 1).
* The viewer draws its interface on a canvas (Flutter web): its text is reachable only after
  the accessibility tree is switched on (``flt-semantics-placeholder``), which
  :meth:`PlaywrightBrowser.texts` does once per page. The imagery date and the matched place
  are read from those texts; when the page shows none, they are None and nothing fails.
* Playwright's timeout becomes ``TimeoutError``; a crashed or closed page becomes
  :class:`~basis.capture.browser.BrowserCrashed`.
* History (EVAL_REPORT 99): the "Show historical imagery" switch, then the "Older images"
  button once per date; the button is disabled at the oldest date. While history is on,
  :meth:`PlaywrightBrowser.settle` clicks only the tips' Dismiss, never Close (the history
  panel's own Close would end history). After every click the pointer goes back to the
  view's centre, so no tooltip hangs over the view.
* The viewport is the capture's :class:`~basis.capture.frame.Layout`: taller than the stored
  view by its margins when the views are cropped clean (``CAPTURE_CLEAN_VIEWS``).
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

from basis.capture.browser import BrowserCrashed, BrowserFactory, CaptureBrowser
from basis.capture.frame import Layout
from basis.capture.signals import PageRecord, Response
from basis.config import Settings

VIEWPORT: Final = {"width": 1440, "height": 900}
HISTORY_SWITCH: Final = "Show historical imagery"
OLDER: Final = "Older images"
MAC_ARGS: Final = ("--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist")
_DATE: Final = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_PLACE: Final = re.compile(r"\b[A-Z]{2} \d{5}\b")
_MOVE_JS: Final = (
    "u => { history.pushState({}, '', u); dispatchEvent(new PopStateEvent('popstate', "
    "{state: {}})); }"
)
_SEMANTICS_JS: Final = """() => {
  const p = document.querySelector('flt-semantics-placeholder');
  if (p) { p.click(); return true; }
  return false;
}"""
_TEXTS_JS: Final = """() => {
  const out = [];
  for (const el of document.querySelectorAll('flt-semantics, [aria-label], [role]')) {
    const label = el.getAttribute('aria-label');
    const text = (el.childElementCount === 0 ? el.textContent : '') || '';
    const value = (label || text || '').trim();
    if (value && value.length < 200) out.push(value);
  }
  return [...new Set(out)];
}"""
_CRASH: Final = ("Target page, context or browser has been closed", "crash", "Target closed")


def chromium_args(settings: Settings) -> list[str]:
    if settings.capture_chromium_args is not None:
        return list(settings.capture_chromium_args)
    return list(MAC_ARGS) if sys.platform == "darwin" else []


class PlaywrightBrowser:
    def __init__(self, page: Any, *, timeout_s: float) -> None:
        self._page = page
        self._timeout_ms = timeout_s * 1000
        self._failed: list[Response] = []
        self._crashed = False
        self._semantics = False
        self._place: str | None = None
        self._loaded = False
        self._history = False
        page.on("response", self._on_response)
        page.on("crash", self._on_crash)

    def _on_response(self, response: Any) -> None:
        if response.status >= 400:
            self._failed.append(Response(response.status, response.url))

    def _on_crash(self, _page: Any) -> None:
        self._crashed = True

    def _drain(self) -> tuple[Response, ...]:
        failed, self._failed = tuple(self._failed), []
        return failed

    async def _guard(self, call: Any) -> Any:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        if self._crashed:
            raise BrowserCrashed("the page crashed")
        try:
            return await call
        except PlaywrightTimeout as exc:
            raise TimeoutError(str(exc)[:200]) from exc
        except PlaywrightError as exc:
            if self._crashed or any(marker in str(exc) for marker in _CRASH):
                raise BrowserCrashed(str(exc)[:200]) from exc
            raise

    async def _record(self, status: int | None) -> PageRecord:
        page = self._page
        return PageRecord(
            url=page.url,
            status=status,
            title=await self._guard(page.title()),
            html=await self._guard(page.content()),
            failed=self._drain(),
        )

    async def load(self, url: str) -> PageRecord:
        self._semantics = False
        self._loaded = True
        self._history = False
        response = await self._guard(
            self._page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        )
        return await self._record(None if response is None else response.status)

    async def move(self, url: str) -> PageRecord:
        await self._guard(self._page.evaluate(_MOVE_JS, url))
        await asyncio.sleep(0.5)
        return await self._record(None)

    async def settle(self, seconds: float, *, dismiss: bool = True) -> None:
        await asyncio.sleep(seconds)
        if not dismiss:
            self._loaded = False
            return
        await self._remember_place()
        await self._dismiss()

    async def _centre_pointer(self) -> None:
        size = self._page.viewport_size or VIEWPORT
        await self._guard(self._page.mouse.move(size["width"] / 2, size["height"] / 2))

    async def _press(self, role: str, name: str, *, enabled: bool) -> bool:
        await self._enable_semantics()
        target = self._page.get_by_role(role, name=name, exact=True)
        if await self._guard(target.count()) == 0:
            return False
        if enabled and not await self._guard(target.first.is_enabled()):
            return False
        await self._guard(target.first.click(timeout=5000))
        await asyncio.sleep(0.5)
        await self._centre_pointer()
        return True

    async def history_on(self) -> PageRecord | None:
        if self._history:
            return await self._record(None)
        if not await self._press("switch", HISTORY_SWITCH, enabled=False):
            return None
        self._history = True
        return await self._record(None)

    async def history_older(self) -> PageRecord | None:
        if not self._history:
            return None
        if not await self._press("button", OLDER, enabled=True):
            return None
        return await self._record(None)

    async def _remember_place(self) -> None:
        """The place panel a search opens names the match; read it before it is closed."""
        if self._place is None:
            for text in await self.texts():
                if _PLACE.search(text) and len(text) < 160:
                    self._place = " ".join(text.split())
                    break

    async def _dismiss(self) -> None:
        """Close what covers the map (EVAL_REPORT 98, probes 2 and 3, run 1). After a page
        load, one Escape closes the place panel the search opened. Never after a move: an
        Escape once the page's address was pushed takes the viewer back to its projects page
        (probe 2 and run 1 both ended there; probe 3, which pressed it before any move, did
        not). The first-visit notice's close button and each tip's Dismiss are clicked by
        their accessible names, a few times, since closing one tip shows the next. The runner
        also refuses a frame that is not the map (``Frame.blank``: a white page)."""
        if self._loaded:
            self._loaded = False
            await self._guard(self._page.keyboard.press("Escape"))
            await asyncio.sleep(0.5)
        await self._enable_semantics()
        labels = (
            ("Dismiss", "Dismiss") if self._history else ("Close", "Dismiss", "Dismiss", "Dismiss")
        )
        for label in labels:
            target = self._page.get_by_role("button", name=label, exact=True)
            if await self._guard(target.count()) == 0:
                continue
            try:
                await target.first.click(timeout=2000)
            except Exception:  # an unclickable leftover never fails the capture
                continue
            await asyncio.sleep(0.6)
        if self._history:
            await self._centre_pointer()

    async def _enable_semantics(self) -> None:
        if not self._semantics:
            self._semantics = bool(await self._guard(self._page.evaluate(_SEMANTICS_JS)))
            await asyncio.sleep(0.5)

    async def texts(self) -> Sequence[str]:
        await self._enable_semantics()
        found = await self._guard(self._page.evaluate(_TEXTS_JS))
        return [str(t) for t in found]

    async def current(self) -> PageRecord:
        return await self._record(None)

    async def screenshot(self) -> bytes:
        data = await self._guard(self._page.screenshot(type="png", timeout=self._timeout_ms))
        return bytes(data)

    async def imagery_date(self) -> str | None:
        """The imagery date, when the accessibility tree carries it. Probes 2 and 3 found the
        status bar (date, camera, coordinates) drawn on the canvas without an accessible
        text, so this is None on the viewer as it is; the date stays visible in the view's
        own status bar."""
        for text in await self.texts():
            found = _DATE.search(text)
            if found:
                return found.group(1)
        return None

    async def place_label(self) -> str | None:
        """The matched place's address line, as the place panel showed it after the search
        (read by :meth:`settle` before the panel is closed)."""
        return self._place


def layout_of(settings: Settings) -> Layout:
    """The viewport and stored band the settings ask for (``CAPTURE_CLEAN_VIEWS``)."""
    margin = settings.capture_frame_margin_px if settings.capture_clean_views else 0
    return Layout(VIEWPORT["width"], VIEWPORT["height"], margin)


def playwright_factory(settings: Settings) -> BrowserFactory:
    """Open a browser per capture and close it after, so a crash never outlives a capture."""

    @asynccontextmanager
    async def opened() -> AsyncIterator[CaptureBrowser]:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            options: dict[str, Any] = {
                "headless": settings.capture_headless,
                "args": chromium_args(settings),
                "viewport": layout_of(settings).viewport,
                "device_scale_factor": 1,
                "locale": "en-US",
            }
            if settings.capture_profile_dir:
                context = await playwright.chromium.launch_persistent_context(
                    settings.capture_profile_dir, **options
                )
                browser = None
            else:
                browser = await playwright.chromium.launch(
                    headless=options["headless"], args=options.pop("args")
                )
                options.pop("headless")
                context = await browser.new_context(**options)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                yield PlaywrightBrowser(page, timeout_s=settings.capture_page_timeout_s)
            finally:
                await context.close()
                if browser is not None:
                    await browser.close()

    return opened
