"""``evals.trace_geometry`` (EVAL_REPORT 97): the answer key checks itself, the candidates
behave as the closed forms say, the image-space table, and the ``--dry-run`` run record
(no private data, no model call)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from basis.domain import traced
from evals import trace_geometry as tg


@pytest.mark.parametrize("lat", [25, 37, 49])
def test_the_closed_form_and_the_integration_agree(lat: int) -> None:
    _, dlat, dlon = tg.box(lat, 660.0, 660.0)
    closed = tg.box_area_closed_sf(lat, lat + dlat, dlon)
    # Sized with the radii at its south edge, the box is a few SF under 660 x 660 ft; the key
    # is exact for the box as built, whatever its size.
    assert closed == pytest.approx(660.0 * 660.0, rel=5e-5)
    assert tg.box_area_integrated_sf(lat, lat + dlat, dlon) == pytest.approx(closed, rel=1e-9)


def test_the_meridian_arc_agrees_with_helmerts_series() -> None:
    assert tg.meridian_arc_series_ft(25, 49) == pytest.approx(
        tg.meridian_arc_integrated_ft(25, 49), rel=1e-9
    )
    # A degree of latitude centred at 45 N: the standard series 111,132.954 - 559.822 cos 2phi
    # + 1.175 cos 4phi metres gives 111,131.779.
    assert tg.meridian_arc_integrated_ft(44.5, 45.5) / traced.FT_PER_M == pytest.approx(
        111_132.954 - 559.822 * math.cos(math.radians(90)) + 1.175 * math.cos(math.radians(180)),
        abs=0.01,
    )


def test_vincenty_direct_goes_the_distance_asked() -> None:
    lat2, lon2 = tg.vincenty_direct(43.0, -88.9, 0.0, 1000.0)
    assert lon2 == pytest.approx(-88.9)
    assert tg.meridian_arc_integrated_ft(43.0, lat2) / traced.FT_PER_M == pytest.approx(
        1000.0, abs=1e-6
    )
    lat3, lon3 = tg.vincenty_direct(43.0, -88.9, 90.0, 1000.0)
    assert lat3 < 43.0  # a geodesic heading east leaves the parallel toward the equator
    assert tg.parallel_arc_ft(43.0, lon3 + 88.9) / traced.FT_PER_M == pytest.approx(
        1000.0, rel=1e-6
    )


def test_the_web_mercator_baseline_is_off_by_the_secant_squared() -> None:
    ring, dlat, dlon = tg.box(49, 20.0, 50.0)
    key = tg.box_area_closed_sf(49, 49 + dlat, dlon)
    ratio = tg.mercator_area_sf(ring) / key
    assert ratio == pytest.approx(1 / math.cos(math.radians(49)) ** 2, rel=0.01)
    assert tg.sphere_area_sf(ring) == pytest.approx(key, rel=0.005)
    assert tg.plane_area_sf(ring) == pytest.approx(key, rel=1e-8)


def test_the_closing_edge_lens_is_tiny_and_poleward() -> None:
    _, _, dlon = tg.box(45, 660.0, 660.0)
    lens = tg.closing_edge_lens(45, dlon)
    assert 0 < lens < 660.0 * 660.0 * 1e-5


def test_the_image_table_is_the_closed_form() -> None:
    rows = {(r["ft_per_px"], r["clicked_off_px"]): r for r in tg.image_table()}
    row = rows[(0.5, 2)]  # a 34 ft reference is 68 px; 2 px off is 2.94 %
    assert (row["reference_px"], row["scale_error_pct"]) == (68.0, 2.94)
    assert row["driveway_error_pct"] == round(((1 + 2 / 68) ** 2 - 1) * 100, 2)
    assert row["fence_error_ft"] == round(150 * 2 / 68, 2)
    assert row["stated_driveway_pct"] == "13.38"  # 120 x 0.5 / 800 + 2 x 2 / 68


def test_a_dry_run_leaves_its_record_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    out = tmp_path / "run"
    assert tg.main(["--out", str(out), "--dry-run", "--repeats", "20"]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["tree"]["component"], manifest["tree"]["version"]) == (
        "traced_measurement",
        "v1",
    )
    assert manifest["results"]["passed"] == 4 and manifest["results"]["failed"] == 0
    summary = json.loads((out / "run.json").read_text())
    assert summary["shipped"] == "tangent_plane_wgs84" and summary["cost_usd"] == 0
    assert summary["worst_area_pct"]["tangent_plane_wgs84"] < 1e-4
    assert summary["worst_area_pct"]["web_mercator_pixels"] > 20
    result = json.loads((out / "trace_geometry.json").read_text())
    assert result["dry_run"] is True and result["latitudes"] == [25, 49]
    assert (
        "Local tangent plane on the WGS84 ellipsoid (shipped)"
        in (out / "trace_geometry.md").read_text()
    )
