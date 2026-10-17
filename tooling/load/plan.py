"""The synthetic study the load test uploads, and helpers the harness shares.

Standard library only and Python 3.9 compatible: ``pytest tooling`` runs in CI without
the backend's dependencies (``.github/workflows/ci.yml``, job ``tooling``).

The study has the live client package's shape (EVAL_REPORT section 27.8: 52 photos in 33
rooms, one appraisal) but no client data: every photo is one of the eight synthetic
fixture photos (``backEnd/tests/fixtures/synthetic/photos``, drawn by
``tooling/fixtures/make_fixtures.py``) under a file name that names its room the way the
engineer's package does (``NN_<floor> <Room> photo N.jpg``, the rule of
``basis.vision.rooms.name_hint``), and the appraisal is the synthetic URAR
``urar_sample.pdf``. Each photo gets a unique trailer after the JPEG end marker
(:func:`unique_bytes`): the API refuses a second document with the same sha256 in a study,
and decoders ignore bytes after the end marker, so the prepared image (and the fake
provider's recognition of it) is unchanged.
"""

from __future__ import annotations

import base64
import json
import math
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "backEnd" / "tests" / "fixtures" / "synthetic"
APPRAISAL = FIXTURES / "urar_sample.pdf"

# Room type -> the fixture photo that shows it.
FIXTURE_PHOTO: Dict[str, str] = {
    "kitchen": "01_kitchen.jpg",
    "bathroom": "02_bathroom.jpg",
    "bedroom": "03_bedroom.jpg",
    "living_room": "04_living_room.jpg",
    "garage": "05_garage.jpg",
    "exterior": "06_exterior_front.jpg",
    "mechanical": "07_ac_condenser.jpg",
    "ambiguous": "08_ambiguous.jpg",
}


class Room(NamedTuple):
    prefix: str  # "1st Floor ", "Basement " or "" (no floor), as the package writes it
    name: str
    room_type: str
    photos: int


# 33 rooms, 52 photos (10 rooms with several photos, 23 with one), like the live package.
ROOMS: Tuple[Room, ...] = (
    Room("", "Front Exterior", "exterior", 4),
    Room("", "Rear Exterior", "exterior", 3),
    Room("", "Left Side", "exterior", 1),
    Room("", "Right Side", "exterior", 1),
    Room("", "Driveway", "exterior", 1),
    Room("", "Garage", "garage", 3),
    Room("1st Floor ", "Kitchen", "kitchen", 4),
    Room("1st Floor ", "Living Room", "living_room", 3),
    Room("1st Floor ", "Dining Room", "living_room", 2),
    Room("1st Floor ", "Family Room", "living_room", 2),
    Room("1st Floor ", "Foyer", "living_room", 1),
    Room("1st Floor ", "Hall", "living_room", 1),
    Room("1st Floor ", "Half Bath", "bathroom", 1),
    Room("1st Floor ", "Office", "bedroom", 1),
    Room("1st Floor ", "Laundry", "mechanical", 1),
    Room("1st Floor ", "Pantry", "kitchen", 1),
    Room("2nd Floor ", "Primary Bedroom", "bedroom", 3),
    Room("2nd Floor ", "Primary Bath", "bathroom", 2),
    Room("2nd Floor ", "Bedroom 2", "bedroom", 1),
    Room("2nd Floor ", "Bedroom 3", "bedroom", 1),
    Room("2nd Floor ", "Bedroom 4", "bedroom", 1),
    Room("2nd Floor ", "Hall Bath", "bathroom", 1),
    Room("2nd Floor ", "Hall", "living_room", 1),
    Room("2nd Floor ", "Primary Closet", "bedroom", 1),
    Room("2nd Floor ", "Linen Closet", "bedroom", 1),
    Room("Basement ", "Rec Room", "living_room", 2),
    Room("Basement ", "Bath", "bathroom", 1),
    Room("Basement ", "Mechanicals", "mechanical", 2),
    Room("Basement ", "Storage", "garage", 1),
    Room("Basement ", "Laundry", "mechanical", 1),
    Room("Basement ", "Bedroom", "bedroom", 1),
    Room("Basement ", "Workshop", "garage", 1),
    # A photo whose name says nothing: the model's identity decides (ambiguous here, so
    # the photo is flagged and lands in Unassigned, as three live photos did).
    Room("", "", "ambiguous", 1),
)


class PlannedPhoto(NamedTuple):
    filename: str
    fixture: str  # file name under FIXTURES / "photos"
    room_type: str


def photo_plan(photos: int = 52) -> List[PlannedPhoto]:
    """The study's photos in upload order; ``photos`` other than 52 takes the first rooms'
    photos (fewer) or repeats the plan with a new floor of rooms (more)."""
    if photos < 1:
        raise ValueError("a study needs at least one photo")
    planned: List[PlannedPhoto] = []
    round_ = 0
    while len(planned) < photos:
        for room in ROOMS:
            for k in range(1, room.photos + 1):
                if len(planned) == photos:
                    return planned
                index = len(planned) + 1
                if room.name:
                    extra = f" {round_ + 1}" if round_ else ""
                    name = f"{index:02d}_{room.prefix}{room.name}{extra} photo {k}.jpg"
                else:
                    name = f"IMG_{index:04d}.jpg"
                planned.append(PlannedPhoto(name, FIXTURE_PHOTO[room.room_type], room.room_type))
        round_ += 1
    return planned


TRAILER_MAGIC = b"BASIS-LOAD:"


def unique_bytes(data: bytes, tag: str) -> bytes:
    """``data`` with a trailer naming ``tag`` after the JPEG end marker (or the PDF's last
    ``%%EOF``): a different sha256, the same decoded image."""
    return data + b"\n" + TRAILER_MAGIC + tag.encode("ascii") + b"\n"


def _segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def emulator_token(uid: str, project: str, *, now: Optional[int] = None, ttl: int = 3600) -> str:
    """An unsigned Firebase ID token the auth emulator accepts (``FIREBASE_AUTH_EMULATOR_HOST``
    set for the API; refused in deployed environments by ``ensure_emulator_not_deployed``)."""
    issued = int(time.time()) if now is None else now
    claims = {
        "iss": f"https://securetoken.google.com/{project}",
        "aud": project,
        "sub": uid,
        "iat": issued,
        "exp": issued + ttl,
        "auth_time": issued,
        "email": f"{uid}@example.test",
        "name": "Load Test Engineer",
    }
    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims)}."


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile (``q`` in [0, 100]); None for no values."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def scaled(seconds: float, scale: float) -> float:
    """A client-side delay (poll interval, gate think time) at the run's time scale."""
    return max(seconds * scale, 0.05)
