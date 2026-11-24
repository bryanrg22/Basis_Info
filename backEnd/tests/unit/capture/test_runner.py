"""One capture through :class:`~basis.capture.runner.CaptureRunner` with a scripted browser and
the in-memory store (EVAL_REPORT 98): every failure code's path and what it leaves behind.

* The ordinary path: one page load (a search, or straight to the engineer's point), six camera
  moves, six views stored with their camera; top-down views carry the pixel size; a search
  puts the found point on the pin (unconfirmed) and the views carry the new pin version; the
  spacing is waited between navigations; the breaker's count resets.
* Each block signal (unusual-traffic page, reCAPTCHA, consent wall, sign-in wall, 429 on the
  page, 429 on an imagery host) pauses the capture until the breaker closes, writes one
  incident, one ``capture.paused`` timeline event with the code and the evidence path, the
  evidence itself (screenshot, HTML, page record), one structured log line with
  ``error_code``, and exactly one alert per trip, however many signals the trip sees.
* A 429 from the feedback/survey endpoint is not a signal.
* A pause keeps the views taken; the resumed capture takes only the missing ones.
* Caps and an open breaker pause without an alert; a blank render, no place, a timeout and a
  crash fail the capture without opening the breaker; another worker's lease makes it wait
  its turn with nothing recorded as a failure.
* Nothing the engineer reads names the provider.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import timedelta

import pytest
from capturetest_support import (
    PLACE,
    Clock,
    FakeBrowser,
    MemoryStorage,
    factory,
    failed_page,
)

from basis.capture.browser import BrowserCrashed
from basis.capture.camera import parse
from basis.capture.governor import GovernorConfig
from basis.capture.runner import CaptureRunner, Done, Failed, Waiting
from basis.capture.store import MemoryCaptureStore, PinRow
from basis.errors.codes import E

BUCKET = "bucket"
STUDY = uuid.UUID("00000000-0000-4000-8000-000000000001")
CONFIG = GovernorConfig()


def address_pin(version: int = 1) -> PinRow:
    return PinRow(
        study_id=STUDY,
        address="1 Main St, Anytown, IA",
        lat=None,
        lon=None,
        source="engineer",
        version=version,
    )


def point_pin() -> PinRow:
    return PinRow(
        study_id=STUDY,
        address=None,
        lat=41.60,
        lon=-93.60,
        source="engineer",
        version=3,
        confirmed=True,
    )


class Rig:
    def __init__(self, browser: FakeBrowser | None = None, *, pin: PinRow | None = None) -> None:
        self.store = MemoryCaptureStore()
        self.storage = MemoryStorage()
        self.clock = Clock()
        self.browser = browser or FakeBrowser()
        self.capture = self.store.add_capture(STUDY, pin or address_pin())

    def runner(self, browser: FakeBrowser | None = None, holder: str = "w1") -> CaptureRunner:
        return CaptureRunner(
            self.store,
            self.storage,  # type: ignore[arg-type]
            BUCKET,
            factory(browser or self.browser),  # type: ignore[arg-type]
            config=CONFIG,
            holder=holder,
            settle_s=0,
            source_text="Aerial imagery",
            clock=self.clock,
            sleep=self.clock.sleep,
            rng=random.Random(7),
        )

    async def run(self, browser: FakeBrowser | None = None) -> Done | Waiting | Failed:
        return await self.runner(browser).run(self.capture.id)

    @property
    def row(self):  # noqa: ANN201 - test helper
        return self.store.captures[self.capture.id]

    def events(self, event_type: str) -> list[dict]:
        return [e for e in self.store.events if e["event_type"] == event_type]


# ---------------------------------------------------------------------- the ordinary path


async def test_a_search_takes_one_load_six_moves_and_stores_six_views() -> None:
    rig = Rig()
    outcome = await rig.run()
    assert outcome == Done(6)
    assert len(rig.browser.loads) == 1 and "/web/search/1+Main+St" in rig.browser.loads[0]
    assert len(rig.browser.moves) == 6
    assert rig.row.status == "succeeded" and rig.row.views == 6
    labels = [v.label for v in rig.store.views]
    assert labels == [
        "aerial-close",
        "aerial-wide",
        "side-south",
        "side-west",
        "side-north",
        "side-east",
    ]
    close = rig.store.views[0]
    assert close.tilt_deg == 0 and close.range_m == 120
    assert close.pixel_size_m == pytest.approx(2 * 120 * 0.31529878 / 900, rel=1e-6)
    assert close.lat == pytest.approx(41.600000) and close.ground_alt_m == pytest.approx(250.10)
    assert close.imagery_date == "8/4/2026" and close.source == "Aerial imagery"
    assert all(v.pixel_size_m is None for v in rig.store.views if v.kind == "oblique")
    assert {v.heading_deg for v in rig.store.views if v.kind == "oblique"} == {0, 90, 180, 270}
    # The search placed the point on the pin, unconfirmed; the views carry that version.
    pin = rig.store.pins[STUDY]
    assert (pin.lat, pin.lon, pin.source, pin.confirmed, pin.version) == (
        41.600000,
        -93.6000000,
        "search",
        False,
        2,
    )
    assert pin.place_label == "1 Main St, Anytown, IA"
    assert {v.pin_version for v in rig.store.views} == {2}
    # Stored under the study's prefix, so the retention purge takes them with its files.
    prefix = f"gs://{BUCKET}/studies/{STUDY}/captures/{rig.capture.id}/"
    assert sorted(rig.storage.objects) == sorted(f"{prefix}{label}.png" for label in labels)
    assert [e["event_type"] for e in rig.store.events] == [
        "capture.started",
        "property.pin_placed",
        "capture.completed",
    ]
    assert rig.events("capture.completed")[0]["code"] == E.CAPTURE_COMPLETED
    assert rig.store.alerts == {} and rig.store.incidents == {}
    assert rig.store.lease_holder is None  # released


async def test_the_spacing_is_waited_between_navigations() -> None:
    rig = Rig()
    await rig.run()
    # Seven navigations: the first has no predecessor; the six others wait 8..20 s each.
    assert len(rig.clock.slept) == 6
    assert all(8 <= s <= 20 for s in rig.clock.slept)
    assert len({round(s, 3) for s in rig.clock.slept}) > 1  # not a fixed rhythm


async def test_an_engineers_point_is_searched_for_its_ground_and_kept_exactly() -> None:
    """The point is searched (``lat,lon``), not loaded as a camera address: the page knows the
    ground's altitude and a camera address needs it (run 3 of EVAL_REPORT 98)."""
    rig = Rig(pin=point_pin())
    assert await rig.run() == Done(6)
    assert rig.browser.loads == ["https://earth.google.com/web/search/41.6000000%2C-93.6000000"]
    assert rig.store.pins[STUDY] == point_pin()  # untouched: no search placement
    camera = parse(rig.browser.moves[0])
    assert camera is not None and (camera.lat, camera.lon) == (41.60, -93.60)
    assert camera.alt_m == pytest.approx(250.10)  # the ground the page landed on
    assert all(v.ground_alt_m == pytest.approx(250.10) for v in rig.store.views)
    assert {v.pin_version for v in rig.store.views} == {3}


# ---------------------------------------------------------------------- block signals

SIGNALS = {
    E.CAPTURE_UNUSUAL_TRAFFIC: failed_page(
        "https://www.google.com/sorry/index?continue=https://earth.google.com/web/",
        429,
    ),
    "unusual-text": failed_page(
        "https://earth.google.com/web/",
        200,
        html="<p>Our systems have detected unusual traffic from your computer network.</p>",
    ),
    E.CAPTURE_RECAPTCHA: failed_page(
        "https://earth.google.com/web/",
        200,
        html='<div class="g-recaptcha" data-sitekey="x"></div>',
    ),
    E.CAPTURE_WALL: failed_page("https://consent.google.com/ml?continue=x", 200),
    "sign-in": failed_page("https://accounts.google.com/ServiceLogin?continue=x", 200),
    E.CAPTURE_RATE_LIMITED: failed_page("https://earth.google.com/web/search/x", 429),
    "429-imagery": failed_page(
        PLACE,
        200,
        (429, "https://kh.google.com/rt/earth/BulkMetadata/pb=!1m2"),
    ),
}
EXPECTED = {
    "unusual-text": E.CAPTURE_UNUSUAL_TRAFFIC,
    "sign-in": E.CAPTURE_WALL,
    "429-imagery": E.CAPTURE_RATE_LIMITED,
}


@pytest.mark.parametrize("name", sorted(SIGNALS))
async def test_each_block_signal_pauses_and_leaves_one_of_everything(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    code = EXPECTED.get(name, name)
    rig = Rig(FakeBrowser(on_load={0: SIGNALS[name]}))
    with caplog.at_level(logging.WARNING, logger="basis.capture.runner"):
        outcome = await rig.run()
    until = rig.clock.now + timedelta(minutes=60)
    assert outcome == Waiting(until, code)
    assert (rig.row.status, rig.row.code, rig.row.resume_at) == ("waiting", code, until)
    assert rig.row.reason == "Property pictures are paused; they will resume on their own."
    # The breaker: open an hour, first trip.
    assert rig.store.state.breaker_open_until == until and rig.store.state.trips == 1
    assert rig.store.state.breaker_code == code
    # One incident for the code.
    incident = rig.store.incidents[code]
    assert incident.occurrences == 1 and incident.last_study_id == STUDY
    # The address that carried the signal: the page, or the imagery request that got 429.
    signalled = [r.url for r in SIGNALS[name].failed if r.status == 429]
    assert incident.last_url == (signalled[0] if signalled else SIGNALS[name].url)
    # One timeline event with the code, the reason and the evidence.
    (event,) = rig.events("capture.paused")
    assert event["code"] == code and event["detail"]["evidence"] == incident.last_evidence
    assert event["detail"]["resume_at"] == until.isoformat()
    # The evidence: screenshot, HTML, page record, with the capture's files.
    prefix = incident.last_evidence
    assert prefix is not None and prefix.startswith(
        f"gs://{BUCKET}/studies/{STUDY}/captures/{rig.capture.id}/evidence/"
    )
    assert {u.removeprefix(prefix) for u in rig.storage.objects if u.startswith(prefix)} == {
        "page.png",
        "page.html",
        "page.json",
    }
    record = json.loads(rig.storage.objects[prefix + "page.json"][0])
    assert record["code"] == code and record["url"] == SIGNALS[name].url
    assert record["status"] == SIGNALS[name].status
    # Exactly one alert for the trip.
    (alert,) = rig.store.alerts.values()
    assert alert["kind"] == "capture_blocked" and alert["trip"] == 1 and alert["code"] == code
    # One structured log line carrying the code.
    lines = [r for r in caplog.records if r.getMessage() == "capture signal"]
    assert len(lines) == 1 and lines[0].error_code == code  # type: ignore[attr-defined]
    assert rig.store.views == []
    assert rig.store.lease_holder is None


async def test_a_429_from_the_survey_endpoint_is_not_a_signal() -> None:
    page = failed_page(
        PLACE,
        200,
        (429, "https://feedback-pa.clients6.google.com/v1/survey/trigger/trigger_anonymous"),
        (404, "https://earth.google.com/favicon-missing"),
    )
    rig = Rig(FakeBrowser(on_load={0: page}))
    assert await rig.run() == Done(6)
    assert rig.store.alerts == {} and rig.store.incidents == {}


async def test_a_signal_on_a_move_keeps_the_views_and_the_resume_takes_only_the_rest() -> None:
    blocked = failed_page("https://www.google.com/sorry/index", 429)
    rig = Rig(FakeBrowser(on_move={2: blocked}))
    outcome = await rig.run()
    assert isinstance(outcome, Waiting) and outcome.code == E.CAPTURE_UNUSUAL_TRAFFIC
    assert [v.label for v in rig.store.views] == ["aerial-close", "aerial-wide"]
    # Before the breaker lapses another try waits again, with no new trip and no new alert.
    rig.clock.advance(minutes=30)
    again = await rig.run(FakeBrowser())
    assert isinstance(again, Waiting) and again.code == E.CAPTURE_BREAKER_OPEN
    assert len(rig.store.alerts) == 1 and rig.store.state.trips == 1
    assert rig.store.incidents[E.CAPTURE_BREAKER_OPEN].occurrences == 1
    # After it lapses, the capture resumes and takes the four missing views only.
    rig.clock.advance(minutes=31)
    resumed = FakeBrowser()
    assert await rig.run(resumed) == Done(6)
    assert len(resumed.moves) == 4
    assert [v.label for v in rig.store.views][2:] == [
        "side-south",
        "side-west",
        "side-north",
        "side-east",
    ]
    assert rig.store.state.trips == 0 and rig.store.state.breaker_open_until is None


async def test_each_trip_raises_one_alert_and_doubles_the_wait() -> None:
    blocked = failed_page("https://www.google.com/sorry/index", 429)
    rig = Rig(FakeBrowser(on_load={0: blocked}))
    first = await rig.run()
    assert isinstance(first, Waiting)
    # While it is open, a try loads nothing and raises nothing.
    rig.clock.advance(minutes=59)
    waiting = FakeBrowser(on_load={0: blocked})
    assert (await rig.run(waiting)).code == E.CAPTURE_BREAKER_OPEN  # type: ignore[union-attr]
    assert waiting.loads == [] and len(rig.store.alerts) == 1
    rig.clock.advance(minutes=2)  # lapsed: the next capture is the trial (half-open)
    second = await rig.run(FakeBrowser(on_load={0: blocked}))
    assert isinstance(second, Waiting)
    assert second.until == rig.clock.now + timedelta(hours=2)  # doubled
    assert rig.store.state.trips == 2
    assert sorted(a["trip"] for a in rig.store.alerts.values()) == [1, 2]
    assert rig.store.incidents[E.CAPTURE_UNUSUAL_TRAFFIC].occurrences == 2


async def test_a_signal_while_the_breaker_is_open_is_the_same_trip() -> None:
    rig = Rig()
    other = rig.store.add_capture(uuid.uuid4(), address_pin())
    blocked = failed_page("https://consent.google.com/ml", 200)
    await rig.run(FakeBrowser(on_load={0: blocked}))
    # A pause the store sees while open (two workers racing) does not count a second trip.
    from basis.capture.store import Incident

    trip = await rig.store.pause(
        other.id,
        reason="x",
        resume_at=rig.clock.now,
        incident=Incident(E.CAPTURE_WALL, rig.clock.now, other.study_id, other.id),
        config=CONFIG,
        block=True,
    )
    assert trip is not None and not trip.new and trip.number == 1
    assert len(rig.store.alerts) == 1
    assert rig.store.incidents[E.CAPTURE_WALL].occurrences == 2


async def test_evidence_that_cannot_be_stored_never_hides_the_signal() -> None:
    rig = Rig(FakeBrowser(on_load={0: failed_page("https://www.google.com/sorry/x", 429)}))
    rig.storage.fail = True
    outcome = await rig.run()
    assert isinstance(outcome, Waiting) and outcome.code == E.CAPTURE_UNUSUAL_TRAFFIC
    assert rig.store.incidents[E.CAPTURE_UNUSUAL_TRAFFIC].last_evidence is None
    assert len(rig.store.alerts) == 1


# ---------------------------------------------------------------------- caps and turns


async def test_spent_load_caps_pause_until_the_window_frees_without_an_alert() -> None:
    rig = Rig()
    for minutes in range(6):  # six loads in the last hour: the hourly cap
        rig.store.navigations.append(
            ("load", None, rig.clock.now - timedelta(minutes=50 - minutes))
        )
    outcome = await rig.run()
    oldest = rig.clock.now - timedelta(minutes=50)
    assert outcome == Waiting(oldest + timedelta(hours=1), E.CAPTURE_CAPS_SPENT)
    assert rig.row.status == "waiting" and rig.row.code == E.CAPTURE_CAPS_SPENT
    assert rig.store.alerts == {} and rig.store.state.trips == 0
    assert rig.store.incidents[E.CAPTURE_CAPS_SPENT].occurrences == 1
    (event,) = rig.events("capture.paused")
    assert "evidence" not in event["detail"]  # no page was involved
    assert rig.browser.loads == []  # nothing was loaded


async def test_the_daily_cap_counts_a_whole_day() -> None:
    rig = Rig()
    for hours in range(30):
        rig.store.navigations.append(
            ("load", None, rig.clock.now - timedelta(hours=23.5, minutes=-hours))
        )
    outcome = await rig.run()
    assert isinstance(outcome, Waiting) and outcome.code == E.CAPTURE_CAPS_SPENT


async def test_spent_move_caps_pause_mid_capture_and_keep_the_views() -> None:
    rig = Rig()
    for n in range(118):
        rig.store.navigations.append(
            ("move", None, rig.clock.now - timedelta(minutes=10, seconds=n))
        )
    outcome = await rig.run()
    assert isinstance(outcome, Waiting) and outcome.code == E.CAPTURE_CAPS_SPENT
    assert len(rig.store.views) == 2  # two moves fit under the cap of 120


async def test_another_workers_lease_makes_it_wait_its_turn_with_nothing_failed() -> None:
    rig = Rig()
    await rig.store.acquire_lease("w2", timedelta(minutes=10), rig.clock.now)
    outcome = await rig.run()
    assert outcome == Waiting(rig.clock.now + timedelta(minutes=2), None)
    assert rig.row.status == "waiting" and rig.row.code is None
    assert rig.store.incidents == {} and rig.store.events == []
    assert rig.store.lease_holder == "w2"  # not released by the waiter
    # A lapsed lease (its worker died) is taken over.
    rig.clock.advance(minutes=11)
    assert await rig.run() == Done(6)


# ---------------------------------------------------------------------- failures


async def test_a_blank_render_fails_the_capture_without_opening_the_breaker() -> None:
    rig = Rig(FakeBrowser(blank_moves={1}))
    outcome = await rig.run()
    assert outcome == Failed(E.CAPTURE_BLANK_RENDER)
    assert rig.row.status == "failed" and rig.row.finished_at == rig.clock.now
    assert rig.row.reason == "The pictures came out blank. Take them again later."
    assert rig.store.state.trips == 0 and rig.store.alerts == {}
    incident = rig.store.incidents[E.CAPTURE_BLANK_RENDER]
    assert incident.last_evidence is not None
    assert rig.storage.objects[incident.last_evidence + "page.png"][0][:8] == b"\x89PNG\r\n\x1a\n"
    (event,) = rig.events("capture.failed")
    assert event["code"] == E.CAPTURE_BLANK_RENDER
    assert [v.label for v in rig.store.views] == ["aerial-close"]


async def test_a_search_that_finds_no_place_fails_with_its_code() -> None:
    rig = Rig(FakeBrowser(landing="https://earth.google.com/web/search/nowhere"))
    assert await rig.run() == Failed(E.CAPTURE_NO_PLACE)
    assert "drop the pin on the house" in (rig.row.reason or "")
    assert rig.store.pins[STUDY].lat is None  # nothing placed
    assert rig.store.incidents[E.CAPTURE_NO_PLACE].last_evidence is not None


async def test_a_search_left_at_the_no_place_spot_places_nothing() -> None:
    # The live run of 2026-11-24: the address matched nothing and the camera sat at 0, -0.2.
    landing = (
        "https://earth.google.com/web/search/nowhere/@0.0,-0.2049,0a,165.4d,35y,0h,0t,0r"
        "/data=CiwiJgokCQ"
    )
    rig = Rig(FakeBrowser(landing=landing))
    assert await rig.run() == Failed(E.CAPTURE_NO_PLACE)
    assert rig.store.pins[STUDY].lat is None  # nothing placed
    assert rig.store.views == []
    assert rig.events("property.pin_placed") == []


async def test_a_timeout_fails_the_capture() -> None:
    rig = Rig(FakeBrowser(on_load={0: TimeoutError("load")}))
    assert await rig.run() == Failed(E.CAPTURE_TIMEOUT)
    assert rig.store.incidents[E.CAPTURE_TIMEOUT].occurrences == 1
    assert rig.store.state.trips == 0 and rig.store.lease_holder is None


async def test_a_browser_crash_fails_the_capture() -> None:
    rig = Rig(
        FakeBrowser(on_move={3: BrowserCrashed("Target page, context or browser has been closed")})
    )
    assert await rig.run() == Failed(E.CAPTURE_BROWSER_CRASH)
    assert rig.store.incidents[E.CAPTURE_BROWSER_CRASH].last_detail is not None
    assert len(rig.store.views) == 3


async def test_an_ended_capture_is_not_run_again() -> None:
    rig = Rig()
    assert await rig.run() == Done(6)
    browser = FakeBrowser()
    assert await rig.run(browser) == Done(6)
    assert browser.loads == []


async def test_nothing_the_engineer_reads_names_the_provider() -> None:
    rigs = [
        Rig(FakeBrowser(on_load={0: failed_page("https://www.google.com/sorry/x", 429)})),
        Rig(FakeBrowser(blank_moves={0})),
        Rig(FakeBrowser(landing="https://earth.google.com/web/search/x")),
        Rig(FakeBrowser(on_load={0: TimeoutError()})),
    ]
    for rig in rigs:
        await rig.run()
        text = (rig.row.reason or "") + json.dumps(
            [e["detail"].get("reason") for e in rig.store.events if e["detail"].get("reason")]
        )
        assert "google" not in text.lower() and "earth" not in text.lower()
