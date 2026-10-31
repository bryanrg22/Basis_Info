"""``If-Match`` and the intake's entity tag (RFC 9110 section 13.1.1; basis.api.preconditions).

The tag is the version in quotes; If-Match uses the strong comparison, so a weak tag or an
unquoted number never matches; ``*`` holds only when an intake exists; an absent or empty
field is no condition at all.
"""

from __future__ import annotations

import pytest

from basis.api.preconditions import IfMatch, intake_etag, parse_if_match


def test_the_etag_is_the_version_in_quotes() -> None:
    assert intake_etag(0) == '"0"'
    assert intake_etag(17) == '"17"'


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_field_is_no_condition(value: str | None) -> None:
    assert parse_if_match(value) is None


@pytest.mark.parametrize(
    ("value", "version", "exists", "allowed"),
    [
        ('"3"', 3, True, True),
        ('"3"', 4, True, False),
        ('"0"', 0, False, True),  # creating the first intake while none exists
        ('"0"', 1, True, False),
        (' "2" , "3" ', 3, True, True),  # a list: any member may match
        ('"2", "4"', 3, True, False),
        ('W/"3"', 3, True, False),  # weak tags never match (strong comparison)
        ('W/"3", "3"', 3, True, True),
        ("3", 3, True, False),  # not a quoted tag
        ('"3" "4"', 3, True, False),  # not a list
        ('garbage, "3"', 3, True, True),  # a malformed member is skipped, the next one read
        ("*", 7, True, True),
        ("*", 0, False, False),  # RFC 9110 13.1.1: false with no current representation
        (" * ", 2, True, True),
    ],
)
def test_the_condition(value: str, version: int, exists: bool, allowed: bool) -> None:
    condition = parse_if_match(value)
    assert condition is not None
    assert condition.allows(version, exists) is allowed


def test_star_is_kept_apart_from_tags() -> None:
    assert parse_if_match("*") == IfMatch(any_current=True, tags=frozenset())
    assert parse_if_match('"1", W/"2"') == IfMatch(any_current=False, tags=frozenset({'"1"'}))
