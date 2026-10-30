"""``evals.combine_rooms``: rooms of one run replaced by another run's (EVAL_REPORT 26)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from evals import combine_rooms as cr


def _run(path: Path, inventory: dict[str, object], raw: list[dict[str, object]]) -> Path:
    path.mkdir()
    (path / "room_inventory.json").write_text(json.dumps(inventory))
    (path / "room_raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in raw))
    return path


def test_exterior_rooms_come_from_the_override_repeat_by_repeat(tmp_path: Path) -> None:
    base = _run(
        tmp_path / "base",
        {
            "1_kitchen": {"repeats": [{"window": 2}, {"window": 3}]},
            "x_front": {"repeats": [{"siding": 1}, {"siding": 1}]},
        },
        [
            {"room_id": "1_kitchen", "repeat": 1, "objects": [{"label": "window"}]},
            {"room_id": "1_kitchen", "repeat": 2, "objects": [{"label": "window"}]},
            {"room_id": "x_front", "repeat": 1, "objects": [{"label": "siding"}]},
        ],
    )
    other = _run(
        tmp_path / "other",
        {"x_front": {"retaining_wall": 1, "siding": 1}, "1_kitchen": {"window": 9}},
        [{"room_id": "x_front", "repeat": 1, "objects": [{"label": "retaining_wall"}]}],
    )
    out = tmp_path / "out"
    args = argparse.Namespace(base=base, override=other, rooms=[], exterior=True, out=out)
    assert cr.combine(args) == 0
    inventory = json.loads((out / "room_inventory.json").read_text())
    assert inventory["1_kitchen"] == {"repeats": [{"window": 2}, {"window": 3}]}
    # one repeat in the override is cycled to the base's two
    assert inventory["x_front"] == {"repeats": [{"retaining_wall": 1, "siding": 1}] * 2}
    raw = [json.loads(line) for line in (out / "room_raw.jsonl").read_text().splitlines()]
    front = [r for r in raw if r["room_id"] == "x_front"]
    assert [r["repeat"] for r in front] == [1, 2]
    assert all(r["objects"] == [{"label": "retaining_wall"}] for r in front)
    summary = json.loads((out / "run.json").read_text())
    assert summary == {"base": str(base), "override": str(other), "rooms": ["x_front"]}
    # The run record: a manifest naming both runs, no model call, and the log.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.combine_rooms"
    assert manifest["harness"]["options"]["exterior"] is True
    assert manifest["calls"]["count"] == 0
    assert [(i["name"], i["path"]) for i in manifest["data"]["inputs"]] == [
        ("base", str(base)),
        ("override", str(other)),
    ]
    assert manifest["notes"] == {"exterior": True}
    assert manifest["summary"] == summary
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 0)
    assert (out / "run.log").exists()


def test_a_room_missing_from_the_override_is_an_error(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", {"x_front": {"siding": 1}}, [])
    other = _run(tmp_path / "other", {}, [])
    args = argparse.Namespace(
        base=base, override=other, rooms=["x_front"], exterior=False, out=tmp_path / "out"
    )
    assert cr.combine(args) == 2
    # No run.json for a refused combination, but the manifest records the exit.
    assert not (tmp_path / "out" / "run.json").exists()
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 2)
    assert manifest["summary"] is None
    assert "rooms not in" in (tmp_path / "out" / "run.log").read_text()


def test_a_missing_base_run_still_leaves_a_manifest(tmp_path: Path) -> None:
    args = argparse.Namespace(
        base=tmp_path / "nope", override=tmp_path / "nope", rooms=[], exterior=True,
        out=tmp_path / "out",
    )  # fmt: skip
    with pytest.raises(FileNotFoundError):
        cr.combine(args)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("error", 1)
    assert manifest["data"]["inputs"][0]["error"] == "not found"
