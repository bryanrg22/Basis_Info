"""``evals.merge_photo_runs``: N decomposed per-photo runs -> one run's prediction files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals import merge_photo_runs


def component(
    label: str, count: int = 1, material: str = "unknown", conf: float = 0.5
) -> dict[str, Any]:
    return {
        "label": label,
        "note": None,
        "count": count,
        "material": material,
        "attachment": "permanent",
        "confidence": conf,
    }


def write_run(
    root: Path, name: str, prompt: str, records: list[dict[str, Any]], spent: float
) -> Path:
    run = root / name
    run.mkdir()
    (run / "photo_raw.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    (run / "run.json").write_text(json.dumps({"prompt": prompt, "spent_usd": spent}))
    return run


def record(
    file: str, repeat: int, room: str, components: list[dict[str, Any]], model: str = "m"
) -> dict[str, Any]:
    return {
        "file": file,
        "repeat": repeat,
        "analysis": {"room_type": room, "components": components, "floor": None},
        "model": model,
        "provider": "fixture",
        "attempts": 1,
        "errors": [],
        "seconds": 1.0,
        "image": {"width": 10, "height": 10},
    }


def test_merge_components_is_a_union_with_the_largest_count() -> None:
    merged = merge_photo_runs.merge_components(
        [
            [component("window", 2, "wood", 0.6), component("other"), component("carpet")],
            [component("window", 5, "vinyl", 0.9), component("other"), component("tile")],
        ]
    )
    by_label = {c["label"]: c for c in merged if c["label"] != "other"}
    assert set(by_label) == {"window", "carpet", "tile"}
    assert by_label["window"]["count"] == 5 and by_label["window"]["confidence"] == 0.9
    assert by_label["window"]["material"] == "wood"  # the first run's description stands
    assert sum(1 for c in merged if c["label"] == "other") == 2


def test_merge_runs_writes_the_harness_files(tmp_path: Path) -> None:
    a = write_run(
        tmp_path,
        "countables",
        "photo_countables/v1",
        [
            record("1.jpg", 1, "kitchen", [component("window", 2), component("sink")]),
            record("1.jpg", 2, "kitchen", [component("window", 3)]),
            record("2.jpg", 1, "bedroom", [component("window", 1)]),
            record("2.jpg", 2, "bedroom", []),
        ],
        0.30,
    )
    b = write_run(
        tmp_path,
        "surfaces",
        "photo_surfaces/v1",
        [
            record("1.jpg", 1, "other", [component("tile", 1, "ceramic")], model="n"),
            record("1.jpg", 2, "other", [component("tile", 1, "ceramic")], model="n"),
            record("2.jpg", 1, "bedroom", [component("carpet")], model="n"),
            # 2.jpg repeat 2 missing in the surfaces run
        ],
        0.25,
    )
    out = tmp_path / "merged"
    summary = merge_photo_runs.merge_runs([a, b], out)
    rooms = json.loads((out / "photo_rooms.json").read_text())
    assert rooms == {
        "1.jpg": {"repeats": ["kitchen", "kitchen"]},
        "2.jpg": {"repeats": ["bedroom", "bedroom"]},
    }
    components = json.loads((out / "photo_components.json").read_text())
    assert components["1.jpg"] == {"repeats": [["sink", "tile", "window"], ["tile", "window"]]}
    assert components["2.jpg"] == {"repeats": [["carpet", "window"], []]}
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    first = next(r for r in raw if r["file"] == "1.jpg" and r["repeat"] == 1)
    assert first["model"] == ["m", "n"] and first["sources"] == 2 and first["seconds"] == 2.0
    assert {c["label"]: c["count"] for c in first["analysis"]["components"]} == {
        "window": 2,
        "sink": 1,
        "tile": 1,
    }
    counts = json.loads((out / "photo_counts.json").read_text())
    assert counts["1.jpg"] == {
        "repeats": [{"window": 2, "sink": 1, "tile": 1}, {"window": 3, "tile": 1}]
    }
    assert summary["spent_usd"] == 0.55 and summary["missing_in_some_run"] == ["2.jpg#2"]
    assert [s["prompt"] for s in summary["merged_from"]] == [
        "photo_countables/v1",
        "photo_surfaces/v1",
    ]
    assert json.loads((out / "run.json").read_text()) == summary
    # The run record: a manifest naming the source runs, no model call, and the log.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.merge_photo_runs"
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [
        ("run1", str(a)),
        ("run2", str(b)),
    ]
    assert manifest["notes"]["sources"] == [
        {"run": str(a), "manifest_id": None},
        {"run": str(b), "manifest_id": None},
    ]
    assert manifest["summary"] == summary
    assert (out / "run.log").exists()
    assert merge_photo_runs.main(["--runs", str(a), str(b), "--out", str(tmp_path / "m2")]) == 0
    # A source run that has its own manifest is named by its id.
    second = merge_photo_runs.merge_runs([tmp_path / "m2", b], tmp_path / "m3")
    assert second["photos"] == 2
    m3 = json.loads((tmp_path / "m3" / "manifest.json").read_text())
    m2_id = json.loads((tmp_path / "m2" / "manifest.json").read_text())["id"]
    assert m3["notes"]["sources"][0] == {"run": str(tmp_path / "m2"), "manifest_id": m2_id}


def test_a_failed_merge_still_leaves_a_manifest(tmp_path: Path) -> None:
    a = write_run(tmp_path, "a", "p/a", [record("1.jpg", 1, "kitchen", [component("sink")])], 0.1)
    out = tmp_path / "merged"
    with pytest.raises(FileNotFoundError):
        merge_photo_runs.merge_runs([a, tmp_path / "missing"], out)
    manifest = json.loads((out / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert "FileNotFoundError" in manifest["error"]
    assert manifest["data"]["inputs"][1]["error"] == "not found"
    assert not (out / "run.json").exists()


def test_room_type_can_come_from_another_run(tmp_path: Path) -> None:
    a = write_run(tmp_path, "a", "p/a", [record("1.jpg", 1, "kitchen", [component("sink")])], 0.1)
    b = write_run(tmp_path, "b", "p/b", [record("1.jpg", 1, "other", [component("tile")])], 0.1)
    out = tmp_path / "merged"
    summary = merge_photo_runs.merge_runs([a, b], out, room_from=2)
    assert json.loads((out / "photo_rooms.json").read_text()) == {"1.jpg": "other"}
    raw = json.loads((out / "photo_raw.jsonl").read_text().splitlines()[0])
    assert raw["analysis"]["room_type"] == "other"
    assert {c["label"] for c in raw["analysis"]["components"]} == {"sink", "tile"}
    assert summary["room_from"] == str(b)
    assert json.loads((out / "manifest.json").read_text())["notes"]["room_from"] == 2
    with pytest.raises(SystemExit):
        merge_photo_runs.parse_args(["--runs", str(a), "--out", "x", "--room-from", "2"])
