"""Conditional requests (RFC 9110 section 13) for the study intake.

The intake's entity tag is its version, ``studies.intake_version`` (migration ``0018``):
incremented by every intake write, ``0`` while the study has none. It is sent as a strong
entity tag, the decimal version in quotes (``ETag: "3"``), on ``GET`` and ``PUT
/v1/studies/{id}/intake``.

``If-Match`` (section 13.1.1) on the ``PUT`` makes the write conditional, so two engineers
editing the same intake cannot overwrite each other unknowingly (the "lost update" problem,
section 13.1.1's own example):

* ``"<version>"`` (or a list of tags): the write proceeds only when one of the tags is the
  current one. If-Match uses the *strong* comparison (section 8.8.3.2), so a weak tag
  (``W/"3"``) never matches, and neither does anything that is not a quoted tag.
* ``*``: the write proceeds only when the study has an intake ("a current representation");
  on a study with none it fails.

The comparison against the current version is made by the repository under the study's row
lock (:meth:`basis.db.repositories.study.StudyRepository.write_intake`), so of two concurrent
writes carrying the same tag exactly one succeeds; the route answers the other ``412``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

IF_MATCH_HEADER: Final = "If-Match"
ETAG_HEADER: Final = "ETag"
# One entity tag of a list: optional weak prefix, then an opaque quoted tag (section 8.8.3).
_ENTITY_TAG: Final = re.compile(r'\s*(W/)?"([^"]*)"\s*(?:,|$)')


def intake_etag(version: int) -> str:
    """The strong entity tag of an intake version: ``"3"``."""
    return f'"{version}"'


@dataclass(frozen=True, slots=True)
class IfMatch:
    """A parsed ``If-Match`` field: ``*`` or the strong tags it lists (weak and malformed
    members are kept out, so they never match)."""

    any_current: bool
    tags: frozenset[str]

    def allows(self, version: int, exists: bool) -> bool:
        """Whether the condition holds for the intake at ``version`` (``exists``: the study
        has an intake)."""
        if self.any_current:
            return exists
        return intake_etag(version) in self.tags


def parse_if_match(value: str | None) -> IfMatch | None:
    """``None`` when the field is absent (or empty: no condition was sent)."""
    if value is None or not value.strip():
        return None
    if value.strip() == "*":
        return IfMatch(any_current=True, tags=frozenset())
    tags: set[str] = set()
    position = 0
    while position < len(value):
        found = _ENTITY_TAG.match(value, position)
        if found is None:
            # Not a list of entity tags (an unquoted "3", stray text): skip to the next comma
            # and keep reading; nothing of it can match.
            comma = value.find(",", position)
            if comma < 0:
                break
            position = comma + 1
            continue
        if found.group(1) is None:
            tags.add(f'"{found.group(2)}"')
        position = found.end()
    return IfMatch(any_current=False, tags=frozenset(tags))
