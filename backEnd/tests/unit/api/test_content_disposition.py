"""``Content-Disposition`` on the document downloads (``basis.api.routes.documents``).

A registered filename never carries a quote, a backslash, a separator or a control character
(``FILENAME_PATTERN``), but the name of the report PDF inside an uploaded UAD 3.6 ZIP is
whatever the ZIP says. The header built from either must stay one header: no CR or LF, a
quoted-string with no raw quote or backslash, the original name only percent-encoded in
``filename*`` (RFC 6266, RFC 8187).
"""

from __future__ import annotations

import re

import pytest

from basis.api.routes.documents import _content_disposition

HEADER = re.compile(
    r'^inline; filename="(?P<ascii>[^"\\\x00-\x1f\x7f]*)"; '
    r"filename\*=UTF-8''(?P<star>[A-Za-z0-9_.~%-]*)$"
)


@pytest.mark.parametrize(
    ("name", "ascii_fallback", "star"),
    [
        ("kitchen 01.jpg", "kitchen 01.jpg", "kitchen%2001.jpg"),
        ("tasación.pdf", "tasaci?n.pdf", "tasaci%C3%B3n.pdf"),
        ('evil".pdf', "evil_.pdf", "evil%22.pdf"),
        ("a\\b.pdf", "a_b.pdf", "a%5Cb.pdf"),
        ("x\r\nX-Injected: 1.pdf", "x__X-Injected: 1.pdf", "x%0D%0AX-Injected%3A%201.pdf"),
        ("\x00\x7f", "__", "%00%7F"),
        ("", "file", ""),
        ('"', "_", "%22"),
    ],
)
def test_the_header_stays_one_well_formed_header(name: str, ascii_fallback: str, star: str) -> None:
    value = _content_disposition(name)
    match = HEADER.fullmatch(value)
    assert match is not None, value
    assert match["ascii"] == ascii_fallback
    assert match["star"] == star
    assert "\r" not in value and "\n" not in value
    value.encode("latin-1")  # what Starlette must be able to send
