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
