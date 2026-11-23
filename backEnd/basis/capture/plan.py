"""The views a capture takes around the pin (EVAL_REPORT 98, PROPERTY_CAPTURE.md 3).

* ``aerial-close``: straight down at 120 m, about 8 cm a pixel over a 1440 x 900 frame (121 by
  76 m): the view to measure paved areas, walks and fences on.
* ``aerial-wide``: straight down at 350 m (about 25 cm a pixel, 353 by 221 m): the lot in its
  surroundings, the access and the parking.
* ``side-south``, ``side-west``, ``side-north``, ``side-east``: tilted 60 degrees at 150 m,
  looking north, east, south and west, so each shows the named face. Where the viewer has no
  3D model of the town (the probe's town has none, REMOTE_INSPECTION_AND_MEASUREMENT 11.1)
  these are the flat photo tilted: they help to read the site, not to measure.

Historical dates (purchase date, leaf-off) and street-level views are not in the plan yet
(EVAL_REPORT 98 says why).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from basis.capture.camera import Camera

ViewKind = Literal["aerial", "oblique", "history", "street"]
CLOSE_RANGE_M: Final = 120.0
WIDE_RANGE_M: Final = 350.0
SIDE_RANGE_M: Final = 150.0
SIDE_TILT_DEG: Final = 60.0
# The heading the camera looks along, and the face of the building it then sees.
SIDES: Final = ((0.0, "south"), (90.0, "west"), (180.0, "north"), (270.0, "east"))


@dataclass(frozen=True, slots=True)
class PlannedView:
    label: str
    kind: ViewKind
    camera: Camera

    @property
    def top_down(self) -> bool:
        return self.camera.tilt_deg == 0


def plan(centre: Camera) -> list[PlannedView]:
    views = [
        PlannedView("aerial-close", "aerial", centre.looking(range_m=CLOSE_RANGE_M)),
        PlannedView("aerial-wide", "aerial", centre.looking(range_m=WIDE_RANGE_M)),
    ]
    for heading, face in SIDES:
        views.append(
            PlannedView(
                f"side-{face}",
                "oblique",
                centre.looking(range_m=SIDE_RANGE_M, heading_deg=heading, tilt_deg=SIDE_TILT_DEG),
            )
        )
    return views
