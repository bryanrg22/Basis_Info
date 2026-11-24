"""The pure parts of EVAL_REPORT 99 (no browser, no network):

* clean views (:mod:`basis.capture.frame`): the layout's viewport, crop and lengthened range
  keep EVAL_REPORT 98's ground pixel size; a known point maps to the same lat/lon on the
  uncropped screenshot and on the stored crop; the crop is the band between the margins and
  the status strip is the screenshot's bottom left; a too-small screenshot is refused;
* the imagery date (:mod:`basis.capture.imagery_date`): the page's formats, impossible dates
  and years refused, the history range's last date taken, the address's date decoded, the
  check of the two; tesseract missing or failing leaves the reason; synthetic status strips
  (generated, no property shown) read right where tesseract is installed;
* leaf-off (:mod:`basis.capture.leaf`): every window edge at 44° N, the latitude shift,
  31 December, out of range; the hand-checked key's six dates; the green share on synthetic
  frames; the combined rule; the view to measure on;
* history (:mod:`basis.capture.history`): priorities, window edges, duplicates, the cap;
* street level (:mod:`basis.capture.street`): addresses, the panorama in an address, the
  heading and distance, a refusal in the failed responses.
"""

from __future__ import annotations

import io
import math
import shutil
from datetime import date

import pytest
from PIL import Image, ImageDraw, ImageFont

from basis.capture import history, leaf, street
from basis.capture.frame import Layout, crop_view, ground_offset_m, lonlat_at
from basis.capture.imagery_date import (
    DateReading,
    address_date,
    agreed,
    parse_status_text,
    read_status_date,
    tesseract_path,
)
from basis.capture.signals import PageRecord, Response

TAN = math.tan(math.radians(17.5))


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------- frame


def test_the_clean_layout_keeps_the_ground_pixel_size() -> None:
    layout = Layout(margin=170)
    assert layout.viewport == {"width": 1440, "height": 1240}
    assert layout.crop_box == (0, 170, 1440, 1070)
    assert layout.range_for(120) == pytest.approx(165.333, abs=1e-3)
    uncropped = 2 * 120 * TAN / 900
    assert layout.pixel_size_m(layout.range_for(120), 35) == pytest.approx(uncropped, rel=1e-12)
    assert Layout().range_for(120) == 120  # no margin: EVAL_REPORT 98 exactly
    with pytest.raises(ValueError):
        Layout(margin=-1)
    with pytest.raises(ValueError):
        layout.pixel_size_m(0, 35)


def test_a_known_point_maps_to_the_same_lat_lon_before_and_after_the_crop() -> None:
    layout = Layout(margin=170)
    size = layout.pixel_size_m(layout.range_for(120), 35)
    lat, lon = 43.85, -103.20
    for x, y in [(720, 450), (0, 0), (1439, 899), (200, 700), (1300, 100)]:
        cropped = lonlat_at(lat, lon, x, y, width=1440, height=900, pixel_size_m=size)
        full = lonlat_at(lat, lon, x, y + 170, width=1440, height=1240, pixel_size_m=size)
        assert cropped == pytest.approx(full, abs=1e-12)
    # The centre is the point the camera looks at; 100 px east is 100 pixel sizes east.
    assert lonlat_at(lat, lon, 720, 450, width=1440, height=900, pixel_size_m=size) == (lat, lon)
    east, north = ground_offset_m(820, 350, width=1440, height=900, pixel_size_m=size)
    assert (east, north) == pytest.approx((100 * size, 100 * size))


def test_crop_view_stores_the_band_and_keeps_the_status_strip() -> None:
    layout = Layout(margin=170)
    image = Image.new("RGB", (1440, 1240), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 170, 1439, 1069), fill=(10, 120, 30))  # the band
    draw.rectangle((230, 1212, 559, 1239), fill=(200, 0, 0))  # the status strip
    cropped = crop_view(png(image), layout)
    view = Image.open(io.BytesIO(cropped.view))
    assert view.size == (1440, 900) and (cropped.width, cropped.height) == (1440, 900)
    assert view.getpixel((0, 0)) == (10, 120, 30) and view.getpixel((1439, 899)) == (10, 120, 30)
    strip = Image.open(io.BytesIO(cropped.status_strip))
    assert strip.size == (330, 28) and strip.getpixel((5, 5)) == (200, 0, 0)
    with pytest.raises(ValueError, match="smaller"):
        crop_view(png(Image.new("RGB", (1440, 900))), layout)
    same = crop_view(png(image), Layout())
    assert same.view == png(image) and same.height == 1240


# ---------------------------------------------------------------------- imagery date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8/4/2026", ("8/4/2026", date(2026, 8, 4))),
        ("older–3/12/2021", ("3/12/2021", date(2021, 3, 12))),
        ("older-—12/31/1985", ("12/31/1985", date(1985, 12, 31))),
        ("1/2/2001–6/20/2023", ("6/20/2023", date(2023, 6, 20))),
        ("older-—9/1 4/2006", ("9/14/2006", date(2006, 9, 14))),  # run 2's reading
        ("older– 12/ 31/1985", ("12/31/1985", date(1985, 12, 31))),
        ("Data attribution 100%", None),
        ("13/40/2020", None),  # not a date
        ("2/31/1985", None),  # not a date either: a misread is refused, not guessed
        ("8/4/2031", None),  # after the latest year
        ("8/4/1901", None),  # before 1930
        ("18/4/20266", None),  # digits glued on: no match
    ],
)
def test_status_text(text: str, expected: tuple[str, date] | None) -> None:
    assert parse_status_text(text, latest_year=2027) == expected


def test_the_address_date_and_its_check() -> None:
    data = "ChYqEAgBEgoyMDE3LTA0LTIyGAFCAggBQgIIAEoNCP___________wEQAA"
    assert address_date(f"https://x/web/@1,2,3a,4d,35y,0h,0t,0r/data={data}") == date(2017, 4, 22)
    assert address_date("https://x/web/@1,2,3a,4d,35y,0h,0t,0r") is None
    assert address_date("https://x/web/@1/data=!!!") is None
    read = DateReading("4/22/2017", date(2017, 4, 22), "status-bar")
    assert agreed(read, date(2017, 4, 22)).note == "status-bar, matches the address"
    assert agreed(read, None) == read
    wrong = agreed(DateReading("1/9/2010", date(2010, 1, 9), "status-bar"), date(2010, 11, 9))
    assert wrong.on is None and "2010-11-09" in wrong.note
    unread = DateReading(None, None, "no date")
    assert agreed(unread, date(2010, 11, 9)) == unread


def test_without_or_with_a_broken_text_reader_the_reason_is_kept() -> None:
    strip = png(Image.new("RGB", (330, 28), (241, 243, 244)))
    assert read_status_date(strip, None, latest_year=2027) == DateReading(
        None, None, "no text reader (tesseract) is installed"
    )
    broken = read_status_date(strip, "/nonexistent/tesseract", latest_year=2027)
    assert broken.on is None and broken.note == "the text reader failed: FileNotFoundError"
    assert tesseract_path("/custom/tesseract") == "/custom/tesseract"


def _strip(text: str) -> bytes:
    """A status strip drawn here (the page's grey and its dark text); no property in it."""
    image = Image.new("RGB", (330, 28), (241, 243, 244))
    ImageDraw.Draw(image).text(
        (12, 7), text, fill=(60, 64, 67), font=ImageFont.load_default(size=13)
    )
    return png(image)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
@pytest.mark.parametrize(
    ("text", "on"),
    [
        ("8/4/2026", date(2026, 8, 4)),
        ("older–3/12/2021", date(2021, 3, 12)),
        ("older–6/20/2023", date(2023, 6, 20)),
        ("100%   Data attribution", None),
    ],
)
def test_synthetic_status_strips_read_with_tesseract(text: str, on: date | None) -> None:
    reading = read_status_date(_strip(text), tesseract_path(None), latest_year=2027)
    assert reading.on == on
    if on is None:
        assert reading.note.startswith("no date in the status bar")


# ---------------------------------------------------------------------- leaf-off


@pytest.mark.parametrize(
    ("on", "label"),
    [
        (date(2021, 4, 25), "leaf-off"),
        (date(2021, 4, 26), "unknown"),
        (date(2021, 5, 31), "unknown"),
        (date(2021, 6, 1), "leaf-on"),
        (date(2021, 10, 15), "leaf-on"),
        (date(2021, 10, 16), "unknown"),
        (date(2021, 11, 9), "unknown"),
        (date(2021, 11, 10), "leaf-off"),
        (date(2021, 1, 15), "leaf-off"),
        (date(2005, 12, 31), "unknown"),  # dated to the year only
    ],
)
def test_month_rule_edges_at_44_north(on: date, label: str) -> None:
    assert leaf.by_month(on, 44.0)[0] == label


def test_month_rule_moves_with_latitude_and_stays_in_its_range() -> None:
    # 2 degrees north: spring 5.3 days later (25 April -> 30 April), autumn 4 days earlier.
    assert leaf.by_month(date(2021, 4, 30), 46.0)[0] == "leaf-off"
    assert leaf.by_month(date(2021, 5, 1), 46.0)[0] == "unknown"
    assert leaf.by_month(date(2021, 10, 11), 46.0)[0] == "leaf-on"
    assert leaf.by_month(date(2021, 10, 12), 46.0)[0] == "unknown"
    # 4 degrees south: spring 10.6 days earlier (25 April -> 14 April).
    assert leaf.by_month(date(2021, 4, 15), 40.0)[0] == "unknown"
    assert leaf.by_month(date(2021, 1, 15), 30.0) == (
        "unknown",
        "the month rule covers 37 to 49.5° N",
    )
    assert leaf.by_month(None, 44.0) == ("unknown", "no imagery date")


def test_month_rule_on_the_hand_checked_key() -> None:
    """The probe property (43.85° N): the hand check of EVAL_REPORT 99."""
    key = {
        date(2021, 3, 12): "leaf-off",
        date(2017, 4, 22): "leaf-off",
        date(2026, 8, 4): "leaf-on",
        date(2023, 6, 20): "leaf-on",
        date(2013, 10, 8): "leaf-on",
        date(2020, 5, 3): "unknown",  # early spring, partly leafed
    }
    assert {on: leaf.by_month(on, 43.85)[0] for on in key} == key


def _frame(colour: tuple[int, int, int], share: float) -> bytes:
    image = Image.new("RGB", (100, 100), (120, 100, 80))  # brown
    ImageDraw.Draw(image).rectangle((0, 0, 99, round(100 * share) - 1), fill=colour)
    return png(image)


def test_green_share_and_the_picture_rule() -> None:
    green = leaf.greenness(_frame((40, 120, 40), 0.8))
    assert green.share == pytest.approx(0.8, abs=0.01)
    assert leaf.by_picture(green)[0] == "leaf-on"
    bare = leaf.greenness(_frame((40, 120, 40), 0.1))
    assert leaf.by_picture(bare)[0] == "leaf-off"
    middle = leaf.greenness(_frame((40, 120, 40), 0.4))
    assert leaf.by_picture(middle)[0] == "unknown"
    grey = leaf.greenness(png(Image.new("RGB", (50, 50), (128, 128, 128))))
    assert grey.share is None and leaf.by_picture(grey) == ("unknown", "a black-and-white picture")
    assert leaf.combined("leaf-off", "leaf-off") == "leaf-off"
    assert leaf.combined("leaf-off", "unknown") == "unknown"
    assert leaf.combined("leaf-on", "leaf-off") == "unknown"


def test_the_view_to_measure_on() -> None:
    dated = leaf.Dated
    views = [
        dated("aerial-close", date(2026, 8, 4), "leaf-on", 0.084),
        dated("history-2021-03-12", date(2021, 3, 12), "leaf-off", 0.084),
        dated("history-2017-04-22", date(2017, 4, 22), "leaf-off", 0.084),
        dated("side-south", date(2026, 8, 4), "leaf-on", None),
    ]
    assert leaf.measure_on(views) == "history-2021-03-12"
    assert leaf.measure_on([v for v in views if v.leaf != "leaf-off"]) == "aerial-close"
    undated = [dated("aerial-close", None, "unknown", 0.084), views[3]]
    assert leaf.measure_on(undated) == "aerial-close"
    assert leaf.measure_on([views[3]]) is None


# ---------------------------------------------------------------------- history plan


def _candidates(*pairs: tuple[date, leaf.Leaf]) -> list[history.Candidate]:
    return [history.Candidate(on, label) for on, label in pairs]


def test_the_plan_priorities_window_edges_and_cap() -> None:
    found = _candidates(
        (date(2026, 8, 4), "leaf-on"),
        (date(2023, 6, 20), "leaf-on"),
        (date(2021, 3, 12), "leaf-off"),
        (date(2020, 5, 3), "unknown"),
        (date(2017, 4, 22), "leaf-off"),
    )
    picks = history.select(found, reference=date(2021, 1, 1), window_days=730, max_views=6)
    # 2021-03-12 is 70 days from the reference, 2020-05-03 243; 2023-06-20 (900 days) and
    # 2017-04-22 are outside the window, and 2017-04-22 comes back as leaf-off.
    assert [(p.label, p.why) for p in picks] == [
        ("history-2026-08-04", "newest"),
        ("history-2021-03-12", "reference"),
        ("history-2020-05-03", "reference"),
        ("history-2017-04-22", "leaf-off"),
    ]
    # The window's edge is inclusive.
    edge = history.select(found, reference=date(2019, 5, 4), window_days=365, max_views=6)
    assert history.Pick(date(2020, 5, 3), "reference") in edge
    beyond = history.select(found, reference=date(2019, 5, 3), window_days=365, max_views=6)
    assert history.Pick(date(2020, 5, 3), "reference") not in beyond
    assert len(history.select(found, reference=None, window_days=730, max_views=2)) == 2
    assert history.select(found, reference=None, window_days=730, max_views=0) == []
    assert history.select([], reference=None, window_days=730, max_views=6) == []


def test_the_newest_is_never_listed_twice() -> None:
    found = _candidates((date(2021, 3, 12), "leaf-off"))
    picks = history.select(found, reference=date(2021, 3, 1), window_days=730, max_views=6)
    assert picks == [history.Pick(date(2021, 3, 12), "newest")]


# ---------------------------------------------------------------------- street level


def test_street_addresses_parse_and_heading() -> None:
    assert street.pano_lookup_address(43.85, -103.20) == (
        "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=43.8500000,-103.2000000"
    )
    aim = street.pano_address("AbCdEfGhIjKlMnOp", 43.8505, -103.2000, 185.64)
    assert aim.endswith("3a,75y,185.6h,90t/data=!3m4!1e1!3m2!1sAbCdEfGhIjKlMnOp!2e0")
    assert street.parse_pano(aim) == street.Pano("AbCdEfGhIjKlMnOp", 43.8505, -103.2000)
    empty = (
        "https://www.google.com/maps/@43.85,-103.20,0a,90y,90t/data=!3m3!1e1!3m1!6shttps:"
        "%2F%2Fstreetviewpixels-pa.clients6.google.com%2Fv1%2Fthumbnail%3Fpanoid%26yaw%3D0"
    )
    assert street.parse_pano(empty) is None
    assert street.heading_deg(0, 0, 1, 0) == pytest.approx(0)
    assert street.heading_deg(0, 0, 0, 1) == pytest.approx(90)
    assert street.heading_deg(0, 0, -1, 0) == pytest.approx(180)
    assert street.heading_deg(0, 0, 0, -1) == pytest.approx(270)
    assert street.distance_m(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-3)


def test_a_refusal_is_read_from_the_failed_responses() -> None:
    def page(*failed: Response) -> PageRecord:
        return PageRecord(url="https://www.google.com/maps/@x", status=200, failed=failed)

    tile = Response(403, "https://streetviewpixels-pa.clients6.google.com/v1/tile?x=0")
    lookup = Response(500, "https://www.google.com/maps/photometa/si/v1?pb=x")
    other = Response(404, "https://www.google.com/favicon.ico")
    assert street.refused(page(tile)) == "the panorama's imagery was refused (HTTP 403)"
    assert street.refused(page(lookup)) == "the panorama lookup failed (HTTP 500)"
    assert street.refused(page(other, tile, lookup)).startswith("the panorama's imagery")
    assert street.refused(page(other)) is None
