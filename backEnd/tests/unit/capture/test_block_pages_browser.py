"""Block pages served locally to the real headless browser, through the capture's own code path
(EVAL_REPORT 99): ``playwright_factory``, ``PlaywrightBrowser`` and ``CaptureRunner`` with
``CAPTURE_VIEWER_ORIGIN`` pointed at a small HTTP server on 127.0.0.1. Nothing reaches the
real site. Each page is modelled on what the real site sends:

* the unusual-traffic page (``/sorry/index``, its sentence);
* a reCAPTCHA challenge (the ``g-recaptcha`` element and its iframe, served locally);
* a consent wall ("Before you continue to Google");
* HTTP 429 on the page;
* a page that renders black (the search lands on a camera address; the views are black).

Each block page must pause the capture with its code, store the evidence (screenshot, HTML,
page record), raise exactly one alert for the trip, write the ``capture.paused`` event and
show the breaker open in the ops health; the black page fails the capture with
``BASIS-CAPTURE-005``, its evidence, and no breaker. Runs where Playwright's Chromium is
installed and is skipped cleanly elsewhere.
"""

from __future__ import annotations

import json
import random
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from capturetest_support import MemoryStorage

from basis.capture.governor import GovernorConfig
from basis.capture.health import capture_health
from basis.capture.playwright_browser import playwright_factory
from basis.capture.runner import CaptureOptions, CaptureRunner, Failed, Waiting
from basis.capture.store import MemoryCaptureStore, PinRow
from basis.config import Settings
from basis.errors.codes import E


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:  # no driver or no browser: nothing to test here
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_installed(), reason="no Playwright Chromium on this machine"
)

CAMERA = "/web/@43.8500000,-103.2000000,250.00a,589.00d,35y,0h,0t,0r"
SORRY = (
    "<html><head><title>https://earth.google.com/web/</title></head><body><div>"
    "<b>About this page</b><br><br>Our systems have detected unusual traffic from your "
    "computer network. This page checks to see if it's really you sending the requests, and "
    "not a robot.</div></body></html>"
)
RECAPTCHA = (
    "<html><body><form id='captcha-form' action='index' method='post'>"
    "<script src='/recaptcha/api.js' async defer></script>"
    "<div id='recaptcha' class='g-recaptcha' data-sitekey='local-test'></div>"
    "<iframe title='reCAPTCHA' src='/recaptcha/api2/anchor?k=local-test'></iframe>"
    "</form></body></html>"
)
CONSENT = (
    "<html><head><title>Before you continue to Google</title></head><body><h1>Before you "
    "continue to Google</h1><button>Reject all</button><button>Accept all</button>"
    "</body></html>"
)
BLACK = "<html><body style='margin:0;background:#000'><canvas></canvas></body></html>"


class _Pages(BaseHTTPRequestHandler):
    mode = "sorry"

    def log_message(self, *args: object) -> None:  # quiet
        return None

    def _send(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        mode = type(self).mode
        if self.path.startswith("/web/search/"):
            if mode == "sorry":
                self._send(302, "", {"Location": "/sorry/index?continue=/web/"})
            elif mode == "recaptcha":
                self._send(200, RECAPTCHA)
            elif mode == "consent":
                self._send(200, CONSENT)
            elif mode == "429":
                self._send(429, "<html><body>Too Many Requests</body></html>")
            else:
                self._send(302, "", {"Location": CAMERA})
        elif self.path.startswith("/sorry/"):
            self._send(429, SORRY)
        elif self.path.startswith("/web/@"):
            self._send(200, BLACK)
        else:
            self._send(404, "")


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


STUDY = uuid.UUID("00000000-0000-4000-8000-000000000009")
CONFIG = GovernorConfig(spacing_min_s=0, spacing_max_s=0)


async def _run(origin: str, mode: str) -> tuple[object, MemoryCaptureStore, MemoryStorage]:
    _Pages.mode = mode
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="test",
        capture_viewer_origin=origin,
        capture_settle_s=0.3,
        capture_page_timeout_s=20,
        capture_history=False,
        capture_tesseract="/nonexistent/tesseract",
    )
    store = MemoryCaptureStore()
    storage = MemoryStorage()
    pin = PinRow(STUDY, "1 Main St, Town, WI", None, None, "engineer", 1)
    capture = store.add_capture(STUDY, pin)
    runner = CaptureRunner(
        store,
        storage,  # type: ignore[arg-type]
        "bucket",
        playwright_factory(settings),
        config=CONFIG,
        holder="test",
        settle_s=settings.capture_settle_s,
        rng=random.Random(1),
        options=CaptureOptions.of(settings),
    )
    return await runner.run(capture.id), store, storage


def _evidence(storage: MemoryStorage, prefix: str) -> dict[str, bytes]:
    return {
        uri.rsplit("/", 1)[1]: data
        for uri, (data, _type) in storage.objects.items()
        if uri.startswith(prefix)
    }


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("sorry", E.CAPTURE_UNUSUAL_TRAFFIC),
        ("recaptcha", E.CAPTURE_RECAPTCHA),
        ("consent", E.CAPTURE_WALL),
        ("429", E.CAPTURE_RATE_LIMITED),
    ],
)
async def test_a_served_block_page_pauses_with_its_code_evidence_alert_and_health(
    server: str, mode: str, code: str
) -> None:
    outcome, store, storage = await _run(server, mode)
    assert isinstance(outcome, Waiting) and outcome.code == code
    (incident,) = store.incidents.values()
    assert incident.code == code and incident.last_url.startswith(server)
    files = _evidence(storage, incident.last_evidence)
    assert set(files) == {"page.png", "page.html", "page.json"}
    assert files["page.png"].startswith(b"\x89PNG")
    record = json.loads(files["page.json"])
    assert record["code"] == code and record["url"].startswith(server)
    assert len(store.alerts) == 1
    (event,) = [e for e in store.events if e["event_type"] == "capture.paused"]
    assert event["code"] == code and event["detail"]["evidence"] == incident.last_evidence
    health = capture_health(
        await store.snapshot(incident.last_seen_at),
        enabled=True,
        config=CONFIG,
        now=incident.last_seen_at,
    )
    assert health["breaker"]["state"] == "open" and health["breaker"]["code"] == code
    assert store.views == []


async def test_a_page_that_renders_black_fails_the_capture_without_the_breaker(
    server: str,
) -> None:
    outcome, store, storage = await _run(server, "black")
    assert outcome == Failed(E.CAPTURE_BLANK_RENDER)
    incident = store.incidents[E.CAPTURE_BLANK_RENDER]
    assert set(_evidence(storage, incident.last_evidence)) == {
        "page.png",
        "page.html",
        "page.json",
    }
    assert store.alerts == {} and store.state.trips == 0
    assert [e["event_type"] for e in store.events][-1] == "capture.failed"
