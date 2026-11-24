"""What the engineer reads about a capture: plain sentences that never name the imagery
provider (AGENTS.md: no provider names in anything a customer sees). The time a pause ends is
``resume_at`` on the capture, which the app shows in the engineer's own time zone; the
sentence does not carry it, because the server does not know that zone."""

from __future__ import annotations

from typing import Final

from basis.errors.codes import E

PAUSED: Final = "Property pictures are paused; they will resume on their own."
WAITING_TURN: Final = "Waiting for another property's pictures to finish; this one is next."
QUEUED: Final = "Property pictures are queued."
RUNNING: Final = "Taking property pictures."
COMPLETED: Final = "Property pictures are ready."

FAILED: Final[dict[str, str]] = {
    E.CAPTURE_NO_PLACE: "The address did not lead to a place. Check the address or drop the pin "
    "on the house, then take the pictures again.",
    E.CAPTURE_BLANK_RENDER: "The pictures came out blank. Take them again later.",
    E.CAPTURE_TIMEOUT: "Taking the pictures took too long. Take them again later.",
    E.CAPTURE_BROWSER_CRASH: "Taking the pictures stopped unexpectedly. Take them again later.",
    E.CAPTURE_GAVE_UP: "Property pictures stayed paused too long and stopped. Take them again "
    "later.",
    E.CAPTURE_UNAVAILABLE: "Property pictures could not start. Take them again in a minute.",
}
FAILED_OTHER: Final = "Property pictures could not be taken. Take them again later."


def failed(code: str) -> str:
    return FAILED.get(code, FAILED_OTHER)


# Parts that did not work while the capture went on (EVAL_REPORT 99): the capture's ``notes``.
PART: Final[dict[str, str]] = {
    E.CAPTURE_HISTORY_UNAVAILABLE: "Earlier pictures of the property could not be taken this "
    "time; today's pictures are ready.",
    E.CAPTURE_STREET_NO_COVERAGE: "There are no street-level pictures near this property.",
    E.CAPTURE_STREET_REFUSED: "Street-level pictures are not available yet.",
    E.CAPTURE_DATE_UNREAD: "The date of some pictures could not be read; they are marked undated.",
}


def part(code: str) -> str:
    return PART.get(code, FAILED_OTHER)
