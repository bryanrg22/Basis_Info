"""What a block looks like, and a blank render (EVAL_REPORT 98).

A *block signal* is the site saying it believes the traffic is automated or must be stopped:
its unusual-traffic page, a reCAPTCHA challenge, a consent or sign-in wall in place of the map,
or HTTP 429 on the main document or on a host the imagery comes from. Any of them opens the
breaker (:mod:`basis.capture.governor`); nothing ever answers a challenge or signs in.

What is *not* a block signal, because it says nothing about our traffic: a 429 from the site's
feedback and survey endpoints (``feedback-pa.clients6.google.com/.../survey/trigger/...``
answered 429 on the very first load of the probe, EVAL_REPORT 98: its quota is shared by every
anonymous visitor), and analytics or logging hosts.

A blank render (the canvas almost one colour after the wait for imagery) is a failure of the
capture, not of the site's patience: it fails the capture without opening the breaker.

Pure functions over what the browser saw (:class:`PageRecord`); the image check decodes a PNG,
so callers run it on a thread (``asyncio.to_thread``), never on the event loop.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from basis.errors.codes import E

# Codes that open the breaker.
BLOCK_CODES: Final = frozenset(
    {E.CAPTURE_UNUSUAL_TRAFFIC, E.CAPTURE_RECAPTCHA, E.CAPTURE_WALL, E.CAPTURE_RATE_LIMITED}
)
# Hosts whose 429 is a block signal: the viewer itself and where its imagery and data come
# from. Matched as the host or a subdomain of it.
WATCHED_HOSTS: Final = ("earth.google.com", "google.com", "googleapis.com", "gstatic.com")
# Requests whose 429 is ignored even on a watched host (host, path fragment): the feedback
# and survey endpoints, logging and analytics.
IGNORED: Final = (
    ("feedback-pa.clients6.google.com", ""),
    ("", "/survey/"),
    ("play.google.com", "/log"),
    ("www.google-analytics.com", ""),
    ("www.googletagmanager.com", ""),
)
_UNUSUAL: Final = re.compile(
    r"unusual traffic from your computer network|our systems have detected unusual traffic",
    re.I,
)
_RECAPTCHA: Final = re.compile(r"g-recaptcha|recaptcha/(?:api|enterprise)|recaptcha-checkbox", re.I)
_CONSENT_TEXT: Final = re.compile(r"Before you continue to Google", re.I)
_WALL_HOSTS: Final = ("consent.google.com", "accounts.google.com", "consent.youtube.com")
_WALL_PATHS: Final = ("/ServiceLogin", "/signin/", "/v3/signin")
# A blank or black frame: the downscaled grey image's spread and mean.
BLANK_STDDEV: Final = 4.0
BLACK_MEAN: Final = 8.0
# A white page with a little text (the viewer's projects page, probe 2: mean 252, spread 8)
# is not the map. The probes' imagery frames: mean 71 to 112, spread 36 to 76 (EVAL_REPORT 98).
PAGE_MEAN: Final = 235.0
PAGE_STDDEV: Final = 25.0


@dataclass(frozen=True, slots=True)
class Response:
    """A response the page received that was not a success (status >= 400)."""

    status: int
    url: str


@dataclass(frozen=True, slots=True)
class PageRecord:
    """What the browser saw after a navigation: the page's address now, the main document's
    HTTP status (None for a move, which loads no document), the title, the HTML, and the
    failed responses since the previous record."""

    url: str
    status: int | None
    title: str = ""
    html: str = ""
    failed: Sequence[Response] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Signal:
    """A block signal or a failure, with what triggered it (for the incident's evidence)."""

    code: str
    detail: str
    url: str
    status: int | None

    @property
    def blocks(self) -> bool:
        return self.code in BLOCK_CODES


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def watched(url: str) -> bool:
    """Whether a 429 from ``url`` is a block signal."""
    host = _host(url)
    path = urlsplit(url).path if host else ""
    for ignored_host, fragment in IGNORED:
        if (not ignored_host or host == ignored_host) and (not fragment or fragment in path):
            return False
    return any(_under(host, domain) for domain in WATCHED_HOSTS)


def detect(page: PageRecord) -> Signal | None:
    """The block signal ``page`` shows, None when it shows none. Checked in order: the
    unusual-traffic page, a reCAPTCHA, a wall, a 429 on the document, a 429 on a watched host.
    """
    host = _host(page.url)
    path = urlsplit(page.url).path if host else ""
    text = f"{page.title}\n{page.html}"
    if (_under(host, "google.com") and path.startswith("/sorry")) or _UNUSUAL.search(text):
        return Signal(E.CAPTURE_UNUSUAL_TRAFFIC, "the unusual-traffic page", page.url, page.status)
    if _RECAPTCHA.search(text):
        return Signal(E.CAPTURE_RECAPTCHA, "a reCAPTCHA challenge", page.url, page.status)
    if host in _WALL_HOSTS or any(p in path for p in _WALL_PATHS) or _CONSENT_TEXT.search(text):
        return Signal(E.CAPTURE_WALL, f"a consent or sign-in wall at {host}", page.url, page.status)
    if page.status == 429:
        return Signal(E.CAPTURE_RATE_LIMITED, "HTTP 429 on the page", page.url, 429)
    for response in page.failed:
        if response.status == 429 and watched(response.url):
            return Signal(
                E.CAPTURE_RATE_LIMITED,
                f"HTTP 429 from {_host(response.url)}",
                response.url,
                429,
            )
    return None


@dataclass(frozen=True, slots=True)
class Frame:
    """The grey statistics of a screenshot, downscaled."""

    mean: float
    stddev: float

    @property
    def blank(self) -> bool:
        """Blank, black, or a white page instead of the map."""
        return (
            self.stddev < BLANK_STDDEV
            or self.mean < BLACK_MEAN
            or (self.mean > PAGE_MEAN and self.stddev < PAGE_STDDEV)
        )


def frame_stats(png: bytes, *, region: tuple[int, int, int, int] | None = None) -> Frame:
    """Mean and spread of the screenshot's grey levels (``region`` crops first: the map,
    without the toolbars). CPU work: call it on a thread."""
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(png)) as image:
        grey = image.convert("L")
        if region is not None:
            grey = grey.crop(region)
        grey.thumbnail((96, 60))
        stat = ImageStat.Stat(grey)
    return Frame(mean=float(stat.mean[0]), stddev=float(stat.stddev[0]))
