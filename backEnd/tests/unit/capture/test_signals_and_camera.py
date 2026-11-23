"""The pure parts of the capture (EVAL_REPORT 98): the camera address and the pixel size, what
counts as a block signal, and what counts as a blank render.

* An address round-trips through :func:`camera.parse`; a search without a place parses to None;
  bad cameras are refused.
* The pixel size at the probe's two cameras is 0.1499 and 0.4128 m (the page's own scale bars
  read 0.1538 and 0.4211: the formula is 2.0 to 2.6 % finer, recorded, not corrected).
* Each block page is recognised; a 429 is a signal on the page or on a watched host and not on
  the feedback, survey, logging or analytics endpoints or a foreign host; an ordinary page and
  other error statuses are not signals.
* A frame of one colour, or a black one, is blank; imagery is not.
"""

from __future__ import annotations

import pytest
from capturetest_support import black_png, map_png, white_page_png

from basis.capture import camera
from basis.capture.signals import PageRecord, Response, detect, frame_stats, watched
from basis.errors.codes import E

PROBE = "https://earth.google.com/web/search/x/@43.850952,-88.9637708,250.10037224a,589.16331713d,35y,0h,0t,0r/data=Cj0a"


def test_an_address_round_trips() -> None:
    parsed = camera.parse(PROBE)
    assert parsed == camera.Camera(43.850952, -88.9637708, 250.10037224, 589.16331713, 35, 0, 0, 0)
    again = camera.parse(parsed.address())
    assert again is not None
    assert again.lat == pytest.approx(parsed.lat) and again.range_m == pytest.approx(
        parsed.range_m, abs=0.01
    )
    side = parsed.looking(range_m=150, heading_deg=450, tilt_deg=60)
    assert (side.heading_deg, side.tilt_deg, side.range_m, side.fov_deg) == (90, 60, 150, 35)
    assert ",150.00d,35y,90h,60t,0r" in side.address()


def test_no_place_parses_to_none_and_bad_cameras_are_refused() -> None:
    assert camera.parse("https://earth.google.com/web/search/nowhere") is None
    assert camera.parse("https://earth.google.com/web/") is None
    with pytest.raises(ValueError):
        camera.Camera(91, 0, 0, 100)
    with pytest.raises(ValueError):
        camera.Camera(0, 0, 0, 0)
    with pytest.raises(ValueError):
        camera.Camera(0, 0, 0, 100, tilt_deg=90)
    with pytest.raises(ValueError):
        camera.search_address("   ")


def test_a_search_address_is_encoded() -> None:
    assert camera.search_address(" The Miller's  Daughter, WI ") == (
        "https://earth.google.com/web/search/The+Miller%27s+Daughter%2C+WI"
    )


def test_the_pixel_size_against_the_probes_scale_bars() -> None:
    assert camera.pixel_size_m(214, 35, 900) == pytest.approx(0.14994, abs=1e-5)
    assert camera.pixel_size_m(589.16331713, 35, 900) == pytest.approx(0.41281, abs=1e-5)
    # The page's bars: 20 m over 130 px and 80 m over 190 px.
    for range_m, bar_m, bar_px in ((214, 20, 130), (589.16331713, 80, 190)):
        ratio = camera.pixel_size_m(range_m, 35, 900) / (bar_m / bar_px)
        assert 0.97 < ratio < 0.985
    with pytest.raises(ValueError):
        camera.pixel_size_m(100, 35, 0)


@pytest.mark.parametrize(
    ("page", "code"),
    [
        (
            PageRecord("https://www.google.com/sorry/index?continue=x", 429),
            E.CAPTURE_UNUSUAL_TRAFFIC,
        ),
        (PageRecord("https://ipv4.google.com/sorry/index", 200), E.CAPTURE_UNUSUAL_TRAFFIC),
        (
            PageRecord(
                "https://earth.google.com/web/",
                200,
                html="Our systems have detected unusual traffic",
            ),
            E.CAPTURE_UNUSUAL_TRAFFIC,
        ),
        (
            PageRecord(
                "https://earth.google.com/web/",
                200,
                html='<script src="https://www.google.com/recaptcha/api.js">',
            ),
            E.CAPTURE_RECAPTCHA,
        ),
        (PageRecord("https://consent.google.com/ml?x", 200), E.CAPTURE_WALL),
        (PageRecord("https://accounts.google.com/v3/signin/identifier", 200), E.CAPTURE_WALL),
        (
            PageRecord("https://earth.google.com/web/", 200, title="Before you continue to Google"),
            E.CAPTURE_WALL,
        ),
        (PageRecord("https://earth.google.com/web/", 429), E.CAPTURE_RATE_LIMITED),
        (
            PageRecord(PROBE, None, failed=(Response(429, "https://kh.google.com/rt/earth/x"),)),
            E.CAPTURE_RATE_LIMITED,
        ),
        (
            PageRecord(PROBE, None, failed=(Response(429, "https://earth.googleapis.com/x"),)),
            E.CAPTURE_RATE_LIMITED,
        ),
    ],
)
def test_each_block_page_is_recognised(page: PageRecord, code: str) -> None:
    signal = detect(page)
    assert signal is not None and signal.code == code and signal.blocks


@pytest.mark.parametrize(
    "url",
    [
        "https://feedback-pa.clients6.google.com/v1/survey/trigger/trigger_anonymous?key=x",
        "https://www.google.com/survey/x",
        "https://play.google.com/log?format=json",
        "https://www.google-analytics.com/g/collect",
        "https://tiles.example.org/x.png",
        "https://evil-google.com/x",
    ],
)
def test_a_429_off_the_watched_hosts_is_not_a_signal(url: str) -> None:
    assert not watched(url)
    assert detect(PageRecord(PROBE, 200, failed=(Response(429, url),))) is None


def test_an_ordinary_page_and_other_errors_are_not_signals() -> None:
    page = PageRecord(
        PROBE,
        200,
        title="Google Earth",
        html="<html><body><flt-semantics-placeholder aria-label='Enable accessibility'>",
        failed=(
            Response(404, "https://earth.google.com/x"),
            Response(503, "https://kh.google.com/y"),
        ),
    )
    assert detect(page) is None


def test_blank_and_black_frames_are_blank_and_imagery_is_not() -> None:
    assert frame_stats(black_png()).blank
    assert not frame_stats(map_png()).blank
    # The map area only: a status bar on a black frame does not rescue it.
    assert frame_stats(black_png(), region=(0, 90, 1440, 868)).blank


def test_a_white_page_instead_of_the_map_is_refused() -> None:
    """Probe 2 ended on the viewer's projects page (mean 252, spread 8): not a view."""
    frame = frame_stats(white_page_png(), region=(0, 90, 1440, 868))
    assert frame.mean > 235 and frame.blank
