"""``evals.sketch_outline`` (EVAL_REPORT 100) on fabricated PDFs: the survey of a vector
sketch, an image sketch and a document with no table; the three checks counted on the same
``(row, loop)`` pairs; the comparison to a hand-checked reference; the public report carries
no dimension; the run leaves a record. No model call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from evals import sketch_outline as so

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extraction"))
from sketch_pdf import (  # noqa: E402
    GROUND_PERIMETER,
    THIN,
    SketchPage,
    build,
    l_house_page,
    rectangle,
)


def vector_pdf() -> bytes:
    page = l_house_page()
    # Thin boxes that are neither the house nor the garage: 12 x 21 ft lands on whole feet
    # at the scale that would make it the garage's 440 SF... it does not; 22 x 20 does.
    page.walls(rectangle(-30, 0, 11, 10), width=THIN)  # the garage's shape at half size
    upper = SketchPage(scale=9.0).walls(rectangle(0, 0, 30, 25))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    return build(page, upper)


def image_pdf() -> bytes:
    page = SketchPage().picture(Image.new("RGB", (420, 380), "white"))
    page.section("Living Area").row("First Floor", "1008 Sq ft", "40 x 18 = 720", "24 x 12 = 288")
    return build(page)


@pytest.fixture
def pdfs(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name, data in (
        ("vector", vector_pdf()),
        ("image", image_pdf()),
        ("plain", build(SketchPage(heading=""))),
    ):
        out[name] = tmp_path / f"{name}.pdf"
        out[name].write_bytes(data)
    return out


def test_the_survey_of_a_vector_sketch(pdfs: dict[str, Path]) -> None:
    found = so.survey("vector", pdfs["vector"], None)
    assert (found["status"], found["table_source"], found["table_in_text_layer"]) == (
        "read",
        "regex",
        True,
    )
    assert found["sketch_pages"] == [1, 2]
    assert found["values"] == {
        "sketch.ground_perimeter_ft": f"{GROUND_PERIMETER}.0",
        "sketch.upper_perimeter_ft": "110.0",
    }
    first, second = found["page_detail"]
    assert (first["status"], first["loops"], len(first["matched"])) == ("read", 3, 2)
    # Three loops and two rows: six pairs. The half-size copy of the garage has the garage's
    # shape, so both single-pair checks pass it at its own scale; only the page's one scale
    # (two rows reproduced together) sets it aside.
    assert first["checks"] == {
        "pairs": 6,
        "walls_on_printed_precision": 3,
        "printed_dimensions": 3,
    }
    assert second["checks"] == {
        "pairs": 1,
        "walls_on_printed_precision": 1,
        "printed_dimensions": 1,
    }


def test_the_survey_of_an_image_sketch_and_of_a_document_with_no_table(
    pdfs: dict[str, Path],
) -> None:
    image = so.survey("image", pdfs["image"], None)
    assert (image["status"], image["fields"]) == ("not_read", [])
    assert [p["status"] for p in image["page_detail"]] == ["raster"]
    plain = so.survey("plain", pdfs["plain"], None)
    assert (plain["status"], plain["sketch_pages"], plain["page_detail"]) == ("no_table", [], [])


def test_the_table_can_come_from_a_stored_analysis(pdfs: dict[str, Path], tmp_path: Path) -> None:
    lines = ["Area Calculations Summary", "Living Area", "First Floor", "1008 Sq ft"]
    lines += ["40 x 18 = 720", "24 x 12 = 288"]
    azure = tmp_path / "azure_di.json"
    azure.write_text(json.dumps({"pages": [{"pageNumber": 1, "lines": lines}]}))
    found = so.survey("image", pdfs["image"], azure)
    assert (found["table_source"], found["table_in_text_layer"]) == ("azure", True)
    assert [p["status"] for p in found["page_detail"]] == ["raster"]


def reference(**changed: Any) -> dict[str, Any]:
    loops = {
        "house": {"printed_area_sf": 1008.0, "perimeter_ft": 140.0, "wall_lengths_ft": [0] * 6},
        # A hand tracing that counts the garage wall the house's corner sits on as two.
        "garage": {"printed_area_sf": 440.0, "perimeter_ft": 84.0, "wall_lengths_ft": [0] * 5},
        "upper": {"printed_area_sf": 750.0, "perimeter_ft": 110.0, "wall_lengths_ft": [0] * 4},
    }
    base = {
        "ground_perimeter_ft": float(GROUND_PERIMETER),
        "upper_perimeter_ft": 110.0,
        "shared_wall_ft": 18.0,
        "checks": {"page": {"scale_pt_per_ft": 6.0}, **loops},
    }
    return {**base, **changed}


def test_the_reader_is_compared_to_a_hand_checked_measurement(pdfs: dict[str, Path]) -> None:
    found = so.survey("vector", pdfs["vector"], None)
    same = so.against_reference(found, reference())
    assert same["all_equal"] and set(same["equal"].values()) == {True}
    assert (same["loops_matched"], same["loops_in_reference"]) == (3, 3)
    assert (same["walls_read"], same["walls_in_reference"]) == (14, 15)
    assert same["loops_on_printed_precision"] == 3
    off = so.against_reference(found, reference(ground_perimeter_ft=190.0))
    assert not off["all_equal"] and off["equal"]["ground_perimeter"] is False
    assert off["equal"]["upper_perimeter"] is True


def test_the_public_report_prints_no_dimension(pdfs: dict[str, Path]) -> None:
    found = [so.survey("vector", pdfs["vector"], None), so.survey("image", pdfs["image"], None)]
    compared = so.against_reference(found[0], reference())
    public = so.render_markdown(found, compared, private=False)
    private = so.render_markdown(found, compared, private=True)
    for figure in ("188", "140", "1008", "pt/ft", "110.0"):
        assert figure in private and figure not in public, figure
    assert (
        "| vector | 2 | regex | yes | 2 | 2 | 0 | ground_perimeter_ft, upper_perimeter_ft |"
        in public
    )
    assert "| image | 1 | regex | yes | 1 | 0 | 1 | none |" in public
    assert "- ground_perimeter: equal" in public


def test_a_run_leaves_a_record_and_the_three_files(
    pdfs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path / "data"))
    ref = tmp_path / "outline.json"
    ref.write_text(json.dumps(reference()))
    out = tmp_path / "run"
    code = so.main(
        [
            "--document", f"a vector sketch={pdfs['vector']}",
            "--document", f"an image sketch={pdfs['image']}",
            "--reference", str(ref),
            "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    assert {p.name for p in out.iterdir()} >= {
        "outline.json",
        "outline.md",
        "outline_public.md",
        "manifest.json",
        "results.json",
    }
    results = json.loads((out / "results.json").read_text())
    cases = {case["id"]: case["outcome"] for case in results["cases"]}
    assert cases == {
        "survey:a vector sketch": "passed",
        "survey:an image sketch": "passed",
        "reference:reproduced": "passed",
    }
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "ok" and manifest["tree"]["component"] == "sketch_outline"
    assert manifest["tree"]["version"] == "v1"
