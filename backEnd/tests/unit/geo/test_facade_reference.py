"""The browser's port of the facade metrology agrees with the server (EVAL_REPORT 111).

``web/tests/fixtures/facade-reference.json`` holds what :func:`basis.geo.facade.measure` and
:func:`basis.domain.traced.measure` (space ``facade``) compute on synthetic photographed walls
(no property's photo): every quantity, the expected errors, the refusals with their words, and
the records and problems of correction bodies. ``web/tests/measure-facade.test.cjs`` checks
``web/live/property-model.js`` against it. This test recomputes it here, so a change to the
server's metrology that the fixture does not carry fails on this side too; ``BASIS_WRITE_
FIXTURES=1`` rewrites it after a deliberate change (then the node test says whether the port
follows)."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from basis.domain import traced
from basis.errors.codes import E
from basis.geo import facade as fc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "domain"))
from test_traced_facade import body, focal35_for, photographed  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "facade-reference.json"
)
SIZE = (1024, 768)


def _facade_out(f: fc.Facade) -> dict[str, Any]:
    return {
        "code": f.code,
        "refusals": [list(r) for r in f.refusals],
        "height_m": f.height_m,
        "gross_m2": f.gross_m2,
        "openings_m2": f.openings_m2,
        "net_m2": f.net_m2,
        "trim_m": f.trim_m,
        "windows": f.windows,
        "doors": f.doors,
        "focal_px": f.focal_px,
        "focal_from": f.focal_from,
        "view_deg": f.view_deg,
        "skew_deg": f.skew_deg,
        "wall_px": f.wall_px,
        "expected_error_pct": f.expected_error_pct,
        "left_out": list(f.left_out),
        "openings": [
            {
                "kind": o.kind,
                "width_m": o.width_m,
                "height_m": o.height_m,
                "area_m2": o.area_m2,
                "perimeter_m": o.perimeter_m,
            }
            for o in f.openings
        ],
    }


def _quads(wall: list[list[float]], openings: list[dict[str, Any]]) -> tuple[Any, list[Any]]:
    return fc.Quad(*[tuple(p) for p in wall]), [
        fc.Opening(o["kind"], fc.Quad(*[tuple(p) for p in o["corners"]])) for o in openings
    ]


def metrology_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    poses = [
        (0.0, 0.0, 14.0, 70.0),
        (25.0, 8.0, 14.0, 70.0),
        (-40.0, 10.0, 12.0, 63.4),
        (55.0, 5.0, 16.0, 80.0),
        (15.0, -6.0, 15.0, 55.0),
    ]
    for yaw, pitch, distance, hfov in poses:
        wall, openings = photographed(yaw=yaw, pitch=pitch, distance=distance, hfov=hfov)
        for fov in (hfov, None):
            for length in (12.0, 9.5):
                cam = fc.Camera(SIZE[0], SIZE[1], fov)
                w, o = _quads(wall, openings)
                cases.append(
                    {
                        "camera": {"width": SIZE[0], "height": SIZE[1], "hfov": fov},
                        "wall": wall,
                        "openings": openings,
                        "length_m": length,
                        "facade": _facade_out(fc.measure(cam, w, length, o)),
                    }
                )
    # Refusals: a narrow wall, a wall seen too obliquely, parallel verticals with no lens, a
    # skewed quad under the lens, a quad that is not convex, an opening outside the wall.
    narrow, _ = photographed(yaw=75.0, distance=16.0, hfov=110.0)
    oblique, _ = photographed(yaw=74.0, distance=12.0, hfov=110.0)
    upright, upright_openings = photographed(pitch=0.0)
    skewed = [[100.0, 600.0], [700.0, 640.0], [760.0, 120.0], [60.0, 160.0]]
    bowtie = [[100.0, 600.0], [700.0, 100.0], [700.0, 600.0], [100.0, 100.0]]
    good, good_openings = photographed()
    outside = [
        *good_openings,
        {"kind": "window", "corners": [[1, 760], [40, 760], [40, 720], [1, 720]]},
    ]
    # Openings just inside and just outside the 4 % tolerance past the grade line.
    g = fc.square_to_quad(fc.Quad(*[tuple(p) for p in good]))

    def on_wall(u0: float, v0: float, u1: float, v1: float) -> list[list[float]]:
        out = []
        for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
            x, y, w = g @ [u, v, 1.0]
            out.append([float(x / w), float(y / w)])
        return out

    edge = [
        {"kind": "door", "corners": on_wall(0.30, -0.035, 0.38, 0.5)},
        {"kind": "door", "corners": on_wall(0.60, -0.045, 0.68, 0.5)},
    ]
    for wall, openings, fov in (
        (good, edge, 70.0),
        (narrow, [], 110.0),
        (oblique, [], 110.0),
        (upright, upright_openings, None),
        (skewed, [], 70.0),
        (bowtie, [], 70.0),
        (good, outside, 70.0),
    ):
        cam = fc.Camera(SIZE[0], SIZE[1], fov)
        w, o = _quads(wall, openings)
        cases.append(
            {
                "camera": {"width": SIZE[0], "height": SIZE[1], "hfov": fov},
                "wall": wall,
                "openings": openings,
                "length_m": 12.0,
                "facade": _facade_out(fc.measure(cam, w, 12.0, o)),
            }
        )
    return cases


def body_cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sy = body(unit="SY", drawn_by="engineer", verified=True)
    no_lens = body()
    del no_lens["facade"]["lens"]
    edit = body(drawn_by="engineer", verified=True, original=body(drawn_by="model:x@facade-v1"))
    edit["facade"]["wall"][2][1] -= 4
    wide = body(facade={"lens": {"focal_length_35mm": focal35_for(80.0), "from": "engineer"}})
    for value in (body(), sy, no_lens, edit, wide):
        rec = traced.measure(value)
        out.append({"body": value, "record": rec.record(), "formula": traced.formula(rec)})
    upright, openings = photographed(pitch=0.0)
    bad_lens = body(facade={"wall": upright, "openings": openings})
    del bad_lens["facade"]["lens"]
    flipped = body()
    wall = flipped["facade"]["wall"]
    flipped["facade"]["wall"] = [wall[3], wall[2], wall[1], wall[0]]
    for value in (
        bad_lens,
        flipped,
        body(unit="LF"),
        body(view={"kind": "capture", "id": "v", "width": 1024, "height": 768}),
        body(facade={"wall_length_ft": 0}),
        body(facade={"openings": [{"kind": "skylight", "corners": []}]}),
        body(facade={"wall": [[100, 800], [600, 800], [600, 100], [100, 100]]}),
    ):
        out.append({"body": value, "problems": traced.problems(value)})
    return out


def reference() -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {
                "about": "Reference values for web/tests/measure-facade.test.cjs from the server's "
                "basis.geo.facade.measure and basis.domain.traced (space facade), on synthetic "
                "walls photographed by a pinhole camera (no property's photo). Written by "
                "backEnd/tests/unit/geo/test_facade_reference.py (BASIS_WRITE_FIXTURES=1).",
                "metrology": metrology_cases(),
                "bodies": body_cases(),
            }
        )
    )


def _close(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            return [f"{path}: {a} != {b}"]
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{path}: keys {sorted(a)} != {sorted(b)}"]
        return [d for k in a for d in _close(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items != {len(b)}"]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for d in _close(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def test_the_reference_fixture_is_what_the_server_computes() -> None:
    now = reference()
    if os.environ.get("BASIS_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(json.dumps(now, indent=1) + "\n", encoding="utf-8")
    if not FIXTURE.is_file():
        pytest.fail(f"{FIXTURE} is missing: run with BASIS_WRITE_FIXTURES=1")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _close(now, stored) == []


def test_the_reference_covers_every_outcome() -> None:
    cases = reference()["metrology"]
    codes = {c["facade"]["code"] for c in cases}
    froms = {c["facade"]["focal_from"] for c in cases}
    assert codes == {
        None,
        E.GEO_FACADE_UNREADABLE,
        E.GEO_FACADE_NO_FOCAL,
        E.GEO_FACADE_OBLIQUE,
    }
    assert {"field of view", "vanishing points", "square-on"} <= froms
    assert any(c["facade"]["left_out"] for c in cases)
