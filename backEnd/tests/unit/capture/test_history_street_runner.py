"""The capture's EVAL_REPORT 99 parts through :class:`~basis.capture.runner.CaptureRunner`
with a scripted browser and the in-memory store (no network, no Chromium):

* history: the walk presses the history switch once and the older arrow once per date, each
  a camera move for the governor and the spacing; the plan keeps the newest date, the dates
  near the reference date and the leaf-off dates, up to the cap; each kept date is a
  ``history`` view with ``aerial-close``'s camera and pixel size, its date and leaf label;
* history that cannot be taken (no control, no readable date) leaves a note, an incident,
  a ``capture.part_failed`` event and evidence, and the capture still succeeds; a black
  date is skipped; a block signal on a history press pauses like any other;
* street level: a panorama is one more load and one move, aimed at the pin, stored with
  where the camera stood; no panorama, a refused one (403 tiles, 500 lookup) and a black
  one each leave their own code, without a retry and without failing the capture;
* clean views: the viewport is taller, the stored view is 1440 x 900, the range is
  lengthened and the pixel size is EVAL_REPORT 98's;
* dates read by the text reader: none installed leaves the dates null with the reason and
  one note per capture;
* the regression: the default options take EVAL_REPORT 98's capture exactly.
"""

from __future__ import annotations

import io
import random
import uuid
from datetime import date

import pytest
from capturetest_support import Clock, FakeBrowser, MemoryStorage, black_png, factory, map_png
from PIL import Image

from basis.capture.camera import parse
from basis.capture.frame import Layout
from basis.capture.governor import GovernorConfig
from basis.capture.runner import CaptureOptions, CaptureRunner, Done, Waiting
from basis.capture.signals import PageRecord, Response
from basis.capture.store import MemoryCaptureStore, PinRow
from basis.errors.codes import E

BUCKET = "bucket"
STUDY = uuid.UUID("00000000-0000-4000-8000-000000000002")
# The thirteen dates the probe property's history offered (EVAL_REPORT 99), newest first, in
# the page's own format.
DATES = [
    "8/4/2026",
    "6/20/2023",
    "3/12/2021",
    "5/3/2020",
    "4/22/2017",
    "10/8/2013",
    "11/9/2010",
    "9/9/2008",
    "9/14/2006",
    "12/31/2005",
    "9/3/2005",
    "5/5/1992",
    "12/31/1985",
]
PANO = "AbCdEfGhIjKlMnOpQrStUv"
LANDING = (
    "https://www.google.com/maps/@43.8505000,-103.1999300,3a,75y,90t/data=!3m5!1e1!3m3!1s"
    + PANO
    + "!2e0!6shttps:%2F%2Fexample"
)
# EVAL_REPORT 99.1: the panorama nearest a pin can be a visitor's photo (id CIHM0og...).
CONTRIBUTED = LANDING.replace(PANO, "CIHM0ogKEICAgIDextra12")
NO_PANO = (
    "https://www.google.com/maps/@43.8500000,-103.2000000,0a,90y,90t/data=!3m3!1e1!3m1!6shttps:"
    "%2F%2Fstreetviewpixels-pa.clients6.google.com%2Fv1%2Fthumbnail%3Fpanoid%26yaw%3D0"
)
TILE_403 = Response(
    403, "https://streetviewpixels-pa.clients6.google.com/v1/tile?panoid=x&x=0&y=0&zoom=0"
)
LOOKUP_500 = Response(500, "https://www.google.com/maps/photometa/si/v1?pb=x")


def pin() -> PinRow:
    return PinRow(
        study_id=STUDY,
        address=None,
        lat=43.8500000,
        lon=-103.2000000,
        source="engineer",
        version=1,
        confirmed=True,
    )


class Rig:
    def __init__(self, browser: FakeBrowser | None = None, **options: object) -> None:
        self.store = MemoryCaptureStore()
        self.storage = MemoryStorage()
        self.clock = Clock()
        self.browser = browser or FakeBrowser()
        self.capture = self.store.add_capture(STUDY, pin())
        self.options = CaptureOptions(**options)  # type: ignore[arg-type]

    async def run(self) -> object:
        runner = CaptureRunner(
            self.store,
            self.storage,  # type: ignore[arg-type]
            BUCKET,
            factory(self.browser),  # type: ignore[arg-type]
            config=GovernorConfig(),
            holder="w1",
            settle_s=0,
            source_text="Aerial imagery",
            street_source_text="Street-level imagery",
            clock=self.clock,
            sleep=self.clock.sleep,
            rng=random.Random(3),
            options=self.options,
        )
        return await runner.run(self.capture.id)

    def views(self, kind: str) -> list:
        return [v for v in self.store.views if v.kind == kind]

    def events(self, event_type: str) -> list[dict]:
        return [e for e in self.store.events if e["event_type"] == event_type]

    @property
    def row(self):  # noqa: ANN201 - test helper
        return self.store.captures[self.capture.id]

    def moves_logged(self) -> int:
        return sum(1 for kind, _, _ in self.store.navigations if kind == "move")


# ---------------------------------------------------------------------- history


async def test_the_walk_keeps_newest_reference_and_leaf_off_dates_on_close_camera() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True)
    rig.store.references[STUDY] = (date(2017, 6, 30), "placed in service (intake)")
    assert await rig.run() == Done(9)
    history = rig.views("history")
    assert [v.label for v in history] == [
        "history-2026-08-04",  # the newest
        "history-2017-04-22",  # 69 days from the reference date (the only one within 730)
        "history-2021-03-12",  # leaf-off (2017-04-22, also leaf-off, is already kept)
    ]
    close = rig.store.views[0]
    assert close.label == "aerial-close"
    for view in history:
        assert (view.lat, view.lon, view.range_m, view.tilt_deg, view.heading_deg) == (
            close.lat,
            close.lon,
            close.range_m,
            0,
            0,
        )
        assert view.pixel_size_m == close.pixel_size_m
    assert [v.imagery_on for v in history] == [
        date(2026, 8, 4),
        date(2017, 4, 22),
        date(2021, 3, 12),
    ]
    assert [v.leaf for v in history] == ["leaf-on", "leaf-off", "leaf-off"]
    assert history[0].imagery_date == "8/4/2026"
    assert all(v.green_share is not None for v in history)
    # One load; six view moves, one move back to the close camera, the switch and 12 steps.
    assert len(rig.browser.loads) == 1
    assert rig.browser.history_presses == 13
    assert rig.moves_logged() == 6 + 1 + 13
    # Every press waits the spacing like a move: 6 + 1 + 13 after the first load, and one
    # more for the press that finds the arrow disabled at the oldest date (no navigation).
    assert len(rig.clock.slept) == 21 and all(8 <= s <= 20 for s in rig.clock.slept)
    assert rig.row.notes == () and rig.store.incidents == {}


async def test_the_cap_keeps_the_highest_priorities() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True, history_max_views=2)
    rig.store.references[STUDY] = (date(2017, 6, 30), "placed in service (intake)")
    assert await rig.run() == Done(8)
    assert [v.label for v in rig.views("history")] == ["history-2026-08-04", "history-2017-04-22"]


async def test_without_a_reference_date_newest_and_leaf_off_are_kept() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True)
    assert await rig.run() == Done(9)
    assert [v.label for v in rig.views("history")] == [
        "history-2026-08-04",
        "history-2021-03-12",
        "history-2017-04-22",
    ]


async def test_a_cap_of_zero_walks_but_keeps_nothing() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True, history_max_views=0)
    assert await rig.run() == Done(6)
    assert rig.views("history") == []


async def test_the_walk_stops_at_max_steps() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True, history_max_steps=3)
    assert await rig.run() == Done(8)
    assert rig.browser.history_presses == 1 + 3
    # Seen: 2026, 2023, 2021 (leaf-off), 2020: newest and the one leaf-off.
    assert [v.label for v in rig.views("history")] == ["history-2026-08-04", "history-2021-03-12"]


async def test_no_history_control_leaves_a_note_and_the_capture_succeeds() -> None:
    rig = Rig(FakeBrowser(history_dates=None), history=True)
    assert await rig.run() == Done(6)
    assert rig.row.status == "succeeded"
    assert rig.row.notes == (
        {
            "part": "history",
            "code": E.CAPTURE_HISTORY_UNAVAILABLE,
            "message": "Earlier pictures of the property could not be taken this time; "
            "today's pictures are ready.",
        },
    )
    assert rig.store.incidents[E.CAPTURE_HISTORY_UNAVAILABLE].occurrences == 1
    (event,) = rig.events("capture.part_failed")
    assert event["code"] == E.CAPTURE_HISTORY_UNAVAILABLE and event["detail"]["part"] == "history"
    evidence = event["detail"]["evidence"]
    assert {u.rsplit("/", 1)[1] for u in rig.storage.objects if u.startswith(evidence)} == {
        "page.png",
        "page.html",
        "page.json",
    }
    assert rig.store.alerts == {} and rig.store.state.trips == 0  # not a block


async def test_history_with_no_readable_date_is_unavailable() -> None:
    browser = FakeBrowser(history_dates=list(DATES[:3]))

    async def no_date() -> None:
        return None

    browser.imagery_date = no_date  # type: ignore[method-assign]
    rig = Rig(browser, history=True)
    assert await rig.run() == Done(6)
    assert rig.views("history") == []
    assert rig.store.incidents[E.CAPTURE_HISTORY_UNAVAILABLE].last_detail == (
        "stepping read no date (2 steps, 3 unread)"
    )


async def test_a_black_date_is_skipped_not_failed() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES[:3]), history_blank={2}), history=True)
    assert await rig.run() == Done(7)
    # 2021-03-12 (index 2) rendered black: skipped, so only the newest is kept.
    assert [v.label for v in rig.views("history")] == ["history-2026-08-04"]
    assert rig.row.status == "succeeded" and E.CAPTURE_BLANK_RENDER not in rig.store.incidents


async def test_a_block_signal_on_a_history_press_pauses_and_keeps_the_views() -> None:
    browser = FakeBrowser(history_dates=list(DATES))
    blocked = PageRecord(
        url="https://earth.google.com/web/",
        status=None,
        failed=(Response(429, "https://kh.google.com/rt/earth/x"),),
    )
    calls = {"n": 0}
    original = browser.history_older

    async def older() -> PageRecord | None:
        calls["n"] += 1
        if calls["n"] == 2:
            return blocked
        return await original()

    browser.history_older = older  # type: ignore[method-assign]
    rig = Rig(browser, history=True)
    outcome = await rig.run()
    assert isinstance(outcome, Waiting) and outcome.code == E.CAPTURE_RATE_LIMITED
    assert rig.row.status == "waiting" and len(rig.store.views) == 6
    assert len(rig.store.alerts) == 1


def _running_again(rig: Rig) -> None:
    row = rig.row
    rig.store.captures[rig.capture.id] = row.__class__(
        **{**{f: getattr(row, f) for f in row.__dataclass_fields__}, "status": "running"}
    )


async def test_a_resumed_capture_does_not_walk_again_once_dates_are_stored() -> None:
    rig = Rig(FakeBrowser(history_dates=list(DATES)), history=True)
    assert await rig.run() == Done(9)
    _running_again(rig)
    rig.browser = FakeBrowser(history_dates=list(DATES))
    assert await rig.run() == Done(9)
    assert len(rig.store.views) == 9 and rig.browser.history_presses == 0


async def test_a_resumed_capture_does_not_retry_a_part_noted_as_not_working() -> None:
    rig = Rig(
        FakeBrowser(history_dates=None, street_landing=NO_PANO), history=True, street_view=True
    )
    assert await rig.run() == Done(6)
    assert {n["code"] for n in rig.row.notes} == {
        E.CAPTURE_HISTORY_UNAVAILABLE,
        E.CAPTURE_STREET_NO_COVERAGE,
    }
    _running_again(rig)
    rig.browser = FakeBrowser(history_dates=list(DATES), street_landing=LANDING)
    assert await rig.run() == Done(6)
    assert rig.browser.history_presses == 0 and len(rig.browser.loads) == 1


# ---------------------------------------------------------------------- street level


async def test_a_panorama_is_aimed_at_the_pin_and_stored_with_where_it_stood() -> None:
    rig = Rig(FakeBrowser(street_landing=LANDING, street_png=map_png(seed=9)), street_view=True)
    assert await rig.run() == Done(7)
    (view,) = rig.views("street")
    assert view.label == "street-front" and view.pano_id == PANO
    assert (view.camera_lat, view.camera_lon) == (43.8505, -103.19993)
    assert (view.lat, view.lon) == (pin().lat, pin().lon)
    # The pin is 56 m south and 6 m west of the panorama: the camera looks a little west of
    # south, at the horizon; a street-level view has no pixel size.
    assert view.heading_deg == pytest.approx(185.8, abs=0.3)
    assert view.tilt_deg == 90 and view.pixel_size_m is None
    assert view.range_m == pytest.approx(55.8, abs=0.5)
    assert view.imagery_date is None and view.imagery_date_note == "not read"
    # Stored without the page's interface: 230 px off the top and 180 px off the bottom.
    assert (view.width, view.height) == (1440, 900 - 230 - 180)
    assert len(rig.browser.loads) == 2  # the viewer's search and the panorama
    assert "map_action=pano&viewpoint=43.8500000,-103.2000000" in rig.browser.loads[1]
    assert f"!1s{PANO}!2e0" in rig.browser.moves[-1] and "185.8h" in rig.browser.moves[-1]
    # The street-level page is never "dismissed": its Close leaves the panorama.
    assert rig.browser.settles[-2:] == [(0, False), (0, False)]
    assert rig.row.notes == ()


async def test_a_street_view_carries_the_street_level_source_and_the_aerials_keep_theirs() -> None:
    """The walkthrough of 2026-11-24 found the street picture labelled as aerial imagery: each
    view's provenance line names what it is."""
    rig = Rig(FakeBrowser(street_landing=LANDING, street_png=map_png(seed=9)), street_view=True)
    assert await rig.run() == Done(7)
    (street_view,) = rig.views("street")
    assert street_view.source == "Street-level imagery"
    others = [v for v in rig.store.views if v.kind != "street"]
    assert len(others) == 6 and {v.source for v in others} == {"Aerial imagery"}


@pytest.mark.parametrize(
    ("landing", "failed", "code", "detail"),
    [
        (NO_PANO, (), E.CAPTURE_STREET_NO_COVERAGE, "the page named no panorama near the property"),
        (NO_PANO, (LOOKUP_500,), E.CAPTURE_STREET_REFUSED, "the panorama lookup failed (HTTP 500)"),
        (
            CONTRIBUTED,
            (),
            E.CAPTURE_STREET_NO_COVERAGE,
            "the nearest panorama is a visitor's photo, not street imagery",
        ),
    ],
)
async def test_no_panorama_is_its_own_code_without_retry(
    landing: str, failed: tuple[Response, ...], code: str, detail: str
) -> None:
    rig = Rig(FakeBrowser(street_landing=landing, street_failed=failed), street_view=True)
    assert await rig.run() == Done(6)
    assert rig.row.status == "succeeded" and [n["code"] for n in rig.row.notes] == [code]
    assert rig.store.incidents[code].last_detail == detail
    assert len(rig.browser.loads) == 2 and len(rig.browser.moves) == 6  # no retry, no aim
    assert rig.store.state.trips == 0


async def test_refused_tiles_are_recorded_with_the_black_screenshot() -> None:
    rig = Rig(
        FakeBrowser(street_landing=LANDING, street_aim_failed=(TILE_403,), street_png=black_png()),
        street_view=True,
    )
    assert await rig.run() == Done(6)
    assert [n["code"] for n in rig.row.notes] == [E.CAPTURE_STREET_REFUSED]
    incident = rig.store.incidents[E.CAPTURE_STREET_REFUSED]
    assert incident.last_detail == "the panorama's imagery was refused (HTTP 403)"
    assert rig.storage.objects[incident.last_evidence + "page.png"][0] == black_png()
    assert rig.views("street") == [] and rig.store.alerts == {}


async def test_a_black_panorama_without_a_refusal_is_refused_too() -> None:
    rig = Rig(FakeBrowser(street_landing=LANDING, street_png=black_png()), street_view=True)
    assert await rig.run() == Done(6)
    assert rig.store.incidents[E.CAPTURE_STREET_REFUSED].last_detail.startswith("black frame")


# ---------------------------------------------------------------------- clean views and dates


async def test_clean_views_are_cropped_and_keep_the_ground_pixel_size() -> None:
    layout = Layout(margin=170)
    rig = Rig(FakeBrowser(height=layout.viewport_height), layout=layout)
    assert await rig.run() == Done(6)
    close = rig.store.views[0]
    assert (close.width, close.height) == (1440, 900)
    assert close.range_m == pytest.approx(120 * 1240 / 900)
    assert close.pixel_size_m == pytest.approx(2 * 120 * 0.31529878 / 900, rel=1e-6)
    camera = parse(rig.browser.moves[0])
    assert camera is not None and camera.range_m == pytest.approx(165.33, abs=0.01)
    data = rig.storage.objects[close.uri][0]
    assert Image.open(io.BytesIO(data)).size == (1440, 900)


async def test_without_a_text_reader_dates_are_null_with_the_reason_and_one_note() -> None:
    rig = Rig(read_dates=True, tesseract=None)
    assert await rig.run() == Done(6)
    assert all(v.imagery_date is None and v.imagery_on is None for v in rig.store.views)
    assert {v.imagery_date_note for v in rig.store.views} == {
        "no text reader (tesseract) is installed"
    }
    assert {v.leaf for v in rig.store.views} == {"unknown"}
    assert [n["code"] for n in rig.row.notes] == [E.CAPTURE_DATE_UNREAD]
    assert rig.store.incidents[E.CAPTURE_DATE_UNREAD].occurrences == 1
    # The status strip is kept beside each view as the date's evidence.
    assert sum(u.endswith(".status.png") for u in rig.storage.objects) == 6


async def test_the_default_options_take_the_section_98_capture_exactly() -> None:
    browser = FakeBrowser(history_dates=list(DATES), street_landing=LANDING)
    rig = Rig(browser)
    assert await rig.run() == Done(6)
    assert browser.history_presses == 0 and len(browser.loads) == 1 and len(browser.moves) == 6
    assert [v.label for v in rig.store.views] == [
        "aerial-close",
        "aerial-wide",
        "side-south",
        "side-west",
        "side-north",
        "side-east",
    ]
    assert len(rig.storage.objects) == 6 and not any(".status." in u for u in rig.storage.objects)
    assert rig.store.views[0].range_m == 120 and rig.store.views[0].height == 900
    assert rig.store.views[0].imagery_date == "8/4/2026"
    assert rig.row.notes == ()


async def test_a_street_view_keeps_no_status_strip_and_raises_no_date_note() -> None:
    rig = Rig(
        FakeBrowser(street_landing=LANDING, street_png=map_png(seed=9)),
        street_view=True,
        read_dates=True,
        tesseract=None,
    )
    assert await rig.run() == Done(7)
    assert not any(u.endswith("street-front.status.png") for u in rig.storage.objects)
    unread = rig.store.incidents[E.CAPTURE_DATE_UNREAD]
    assert unread.occurrences == 1 and not unread.last_detail.startswith("street")
