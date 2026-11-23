"""The browser the runner drives, as a protocol, so the runner's every path can be tested with
a scripted fake (``tests/unit/capture/fakes.py``) and run for real with Playwright
(:mod:`basis.capture.playwright_browser`).

``load`` opens an address (a page load: counted against the load caps); ``move`` changes the
address of the open page without loading it (``history.pushState`` and a ``popstate`` event,
which the viewer follows: EVAL_REPORT 98), counted against the move cap. Both return what the
page looks like afterwards (:class:`~basis.capture.signals.PageRecord`), with the failed
responses since the previous call, so the runner can check for a block after every
navigation. A browser that died raises :class:`BrowserCrashed`; one that took too long raises
``TimeoutError``.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from basis.capture.signals import PageRecord


class BrowserCrashed(RuntimeError):
    """The browser or its page closed or crashed under the capture."""


class CaptureBrowser(Protocol):
    async def load(self, url: str) -> PageRecord: ...

    async def move(self, url: str) -> PageRecord: ...

    async def settle(self, seconds: float) -> None:
        """Wait for the imagery to stream and close whatever covers the view."""
        ...

    async def current(self) -> PageRecord:
        """The page as it is now, with its HTML (the evidence of a signal)."""
        ...

    async def screenshot(self) -> bytes:
        """The viewport as a PNG at one device pixel per CSS pixel."""
        ...

    async def imagery_date(self) -> str | None:
        """The date of the imagery as the page shows it, None when it shows none."""
        ...

    async def place_label(self) -> str | None:
        """The name and address of the place a search matched, when the page shows one."""
        ...


BrowserFactory = Callable[[], AbstractAsyncContextManager[CaptureBrowser]]
