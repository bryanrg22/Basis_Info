"""The imagery date of a view, read from the viewer's status bar (EVAL_REPORT 99).

The viewer draws the date on its canvas, at the left of the status bar: ``8/4/2026`` for
today's imagery, ``older–3/12/2021`` in history mode (the newest imagery in the view is of
that date). EVAL_REPORT 99 compared four ways of reading it on every view taken, against the
dates read by hand:

* the accessibility tree once switched on: carries no date (the status bar and the history
  header have no accessible text);
* the date in the page address (history mode only): exact for a date the viewer offered,
  but it is whatever was asked for, not what is shown (an address asking for 2019-01-01
  showed the 2017-04-22 imagery), and it read 2022-01-01 for a moment after history was
  switched on;
* the history header (``Mar 12, 2021``), by OCR: history mode only;
* the status bar, by OCR (tesseract on the strip scaled up four times, one text line): read
  every view right, today's and history's, and it shows the imagery actually drawn. Shipped.

Tesseract is a system program (``CAPTURE_TESSERACT``, else ``tesseract`` on the path), not a
Python dependency. When it is missing, fails, or reads no date, the view keeps
``imagery_date`` null with the reason in ``imagery_date_note``; nothing fails. CPU work and a
subprocess: call :func:`read_status_date` on a thread.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Final

SCALE: Final = 4
TIMEOUT_S: Final = 20.0
EARLIEST_YEAR: Final = 1930
_SLASHED: Final = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_GAP: Final = re.compile(r"(?<=[\d/])\s+(?=[\d/])")
METHOD: Final = "status-bar"


@dataclass(frozen=True, slots=True)
class DateReading:
    """``text``: the date as the page shows it (``M/D/YYYY``); ``on``: the same as a date;
    ``note``: how it was read, or why it was not."""

    text: str | None
    on: date | None
    note: str


def parse_status_text(text: str, *, latest_year: int) -> tuple[str, date] | None:
    """The last ``M/D/YYYY`` in the status text that is a real date between 1930 and
    ``latest_year`` (the history range ``older–3/12/2021`` ends with the shown date). A space
    the reader put inside the date (``9/1 4/2006`` for ``9/14/2006``, EVAL_REPORT 99 run 2) is
    closed first: the strip holds nothing but the date and its ``older–`` prefix."""
    text = _GAP.sub("", text)
    for found in reversed(list(_SLASHED.finditer(text))):
        month, day, year = int(found[1]), int(found[2]), int(found[3])
        if not EARLIEST_YEAR <= year <= latest_year:
            continue
        try:
            when = date(year, month, day)
        except ValueError:
            continue
        return found.group(0), when
    return None


def tesseract_path(configured: str | None) -> str | None:
    """The program to run: the setting, else ``tesseract`` on the path, else None."""
    if configured:
        return configured
    return shutil.which("tesseract")


def _prepared(strip_png: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(strip_png)) as image:
        grey = image.convert("L")
        big = grey.resize((grey.width * SCALE, grey.height * SCALE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        big.save(buffer, "PNG")
        return buffer.getvalue()


def ocr_line(png: bytes, tesseract: str) -> str:
    """One line of text (``--psm 7``) from a PNG; raises OSError or SubprocessError."""
    done = subprocess.run(  # noqa: S603 - a configured program with fixed arguments
        [tesseract, "stdin", "stdout", "--psm", "7"],
        input=png,
        capture_output=True,
        timeout=TIMEOUT_S,
        check=True,
    )
    return done.stdout.decode(errors="replace").strip()


def read_status_date(strip_png: bytes, tesseract: str | None, *, latest_year: int) -> DateReading:
    """The imagery date in a status-bar strip (:func:`basis.capture.frame.crop_view`)."""
    if tesseract is None:
        return DateReading(None, None, "no text reader (tesseract) is installed")
    try:
        text = ocr_line(_prepared(strip_png), tesseract)
    except (OSError, subprocess.SubprocessError) as exc:
        return DateReading(None, None, f"the text reader failed: {type(exc).__name__}")
    found = parse_status_text(text, latest_year=latest_year)
    if found is None:
        shown = " ".join(text.split())[:60]
        return DateReading(None, None, f"no date in the status bar (read {shown!r})")
    return DateReading(found[0], found[1], METHOD)


_DATA: Final = re.compile(r"/data=([A-Za-z0-9_-]+)")
_ISO: Final = re.compile(rb"(\d{4})-(\d{2})-(\d{2})")


def address_date(address: str) -> date | None:
    """The history date the page address carries (history mode: an ISO date inside the
    base64url ``data=`` parameter, EVAL_REPORT 99), None when it carries none. It is the
    date asked for, which is the date shown only when it was reached with the viewer's own
    arrows: :func:`agreed` uses it to check the status bar's reading, never alone."""
    import base64
    import binascii

    found = _DATA.search(address)
    if found is None:
        return None
    raw = found.group(1)
    try:
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return None
    iso = _ISO.search(data)
    if iso is None:
        return None
    try:
        return date(int(iso[1]), int(iso[2]), int(iso[3]))
    except ValueError:
        return None


def agreed(reading: DateReading, address: date | None) -> DateReading:
    """A history date read off the status bar, checked against the page address's date
    (EVAL_REPORT 99): kept when they agree or the address has none; when they disagree the
    date is not trusted (a misread digit would otherwise become a wrong date)."""
    if address is None or reading.on is None or reading.on == address:
        if reading.on is not None and address is not None:
            return DateReading(reading.text, reading.on, f"{reading.note}, matches the address")
        return reading
    return DateReading(
        None,
        None,
        f"the status bar read {reading.text} but the page address says {address.isoformat()}",
    )
