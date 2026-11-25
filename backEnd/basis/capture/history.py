"""Which historical dates of the straight-down view to keep (EVAL_REPORT 99).

The viewer lists a property's dated imagery behind its history control and steps through it
with its "Older images" arrow. The capture walks every date once (one camera move each, no
page load: EVAL_REPORT 99 compared this with setting the date in the page address, which
needs one move per date too but cannot list the dates, and shows the newest imagery at or
before whatever date it is given), reads each date from the status bar, and keeps:

1. the newest date;
2. every date within ``window_days`` of the study's reference date (the intake's
   placed-in-service date, else the appraisal's effective date), nearest first: what
   existed when the property was placed in service, and before and after a renovation;
3. the leaf-off dates, newest first: pictures where trees do not hide the pavement;

up to ``max_views`` in that order of priority. Each kept date is a top-down view with the
same camera, and so the same georeference, as ``aerial-close``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from basis.capture.leaf import Leaf

Why = Literal["newest", "reference", "leaf-off"]
LABEL_PREFIX: Final = "history-"


def label_for(on: date) -> str:
    return f"{LABEL_PREFIX}{on.isoformat()}"


@dataclass(frozen=True, slots=True)
class Candidate:
    on: date
    leaf: Leaf


@dataclass(frozen=True, slots=True)
class Pick:
    on: date
    why: Why

    @property
    def label(self) -> str:
        return label_for(self.on)


def select(
    found: Sequence[Candidate],
    *,
    reference: date | None,
    window_days: int,
    max_views: int,
) -> list[Pick]:
    """The dates to keep, in priority order, at most ``max_views``; each date once."""
    if max_views <= 0 or not found:
        return []
    by_date = {c.on: c for c in found}
    newest = max(by_date)
    picks: list[Pick] = [Pick(newest, "newest")]
    taken = {newest}
    if reference is not None:
        near = sorted(
            (d for d in by_date if abs((d - reference).days) <= window_days),
            key=lambda d: (abs((d - reference).days), d),
        )
        for when in near:
            if when not in taken:
                picks.append(Pick(when, "reference"))
                taken.add(when)
    for when in sorted((d for d, c in by_date.items() if c.leaf == "leaf-off"), reverse=True):
        if when not in taken:
            picks.append(Pick(when, "leaf-off"))
            taken.add(when)
    return picks[:max_views]
