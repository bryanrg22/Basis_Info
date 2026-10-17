"""tooling/load/plan.py: the synthetic study and the shared helpers (stdlib only; CI runs
``pytest tooling`` on Python 3.9 and 3.12 without the backend's dependencies)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import (  # noqa: E402
    APPRAISAL,
    FIXTURE_PHOTO,
    FIXTURES,
    ROOMS,
    TRAILER_MAGIC,
    emulator_token,
    percentile,
    photo_plan,
    scaled,
    unique_bytes,
)


def test_the_default_study_has_the_live_package_shape() -> None:
    photos = photo_plan()
    assert len(photos) == 52
    assert len(ROOMS) == 33
    assert sum(1 for r in ROOMS if r.photos > 1) == 11
    assert len({p.filename for p in photos}) == 52
    assert photos[0].filename == "01_Front Exterior photo 1.jpg"
    assert photos[10].filename == "11_Garage photo 1.jpg"
    assert photos[16].filename == "17_1st Floor Kitchen photo 4.jpg"
    assert photos[-1].filename == "IMG_0052.jpg" and photos[-1].room_type == "ambiguous"


def test_every_planned_photo_is_a_synthetic_fixture() -> None:
    for p in photo_plan():
        assert p.fixture == FIXTURE_PHOTO[p.room_type]
        assert (FIXTURES / "photos" / p.fixture).is_file()
    assert APPRAISAL.is_file()


def test_fewer_and_more_photos() -> None:
    assert [p.filename for p in photo_plan(3)] == [
        "01_Front Exterior photo 1.jpg",
        "02_Front Exterior photo 2.jpg",
        "03_Front Exterior photo 3.jpg",
    ]
    more = photo_plan(60)
    assert len(more) == 60 and len({p.filename for p in more}) == 60
    assert more[52].filename == "53_Front Exterior 2 photo 1.jpg"  # a second round of rooms
    with pytest.raises(ValueError):
        photo_plan(0)


def test_unique_bytes_differ_only_by_a_trailer() -> None:
    data = (FIXTURES / "photos" / "01_kitchen.jpg").read_bytes()
    a, b = unique_bytes(data, "study:1"), unique_bytes(data, "study:2")
    assert hashlib.sha256(a).digest() != hashlib.sha256(b).digest()
    assert a.startswith(data) and b.startswith(data)
    assert a[len(data):] == b"\n" + TRAILER_MAGIC + b"study:1\n"


def _claims(token: str) -> dict:
    header, payload, signature = token.split(".")
    assert signature == ""
    pad = "=" * (-len(payload) % 4)
    assert json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))["alg"] == "none"
    return json.loads(base64.urlsafe_b64decode(payload + pad))


def test_emulator_token_claims() -> None:
    claims = _claims(emulator_token("load-eng-3", "basis-load", now=1000, ttl=60))
    assert claims["sub"] == "load-eng-3"
    assert claims["aud"] == "basis-load"
    assert claims["iss"] == "https://securetoken.google.com/basis-load"
    assert (claims["iat"], claims["exp"]) == (1000, 1060)


def test_percentile_is_nearest_rank() -> None:
    values = [5, 1, 4, 2, 3]
    assert percentile(values, 50) == 3
    assert percentile(values, 95) == 5
    assert percentile(values, 0) == 1
    assert percentile([], 50) is None
    with pytest.raises(ValueError):
        percentile(values, 101)


def test_scaled_delays_never_reach_zero() -> None:
    assert scaled(5.0, 0.1) == pytest.approx(0.5)
    assert scaled(5.0, 0.0) == 0.05
