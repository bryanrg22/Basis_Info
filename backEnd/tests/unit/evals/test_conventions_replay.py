"""``evals.conventions_replay``: a stored export replayed through the firm's conventions by
tier (CLASSIFICATION_CONVENTIONS.md section 9.5). Synthetic export and rows only."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from basis.classify.cache import cache_key
from basis.domain.rules import RULES_VERSION
from evals.conventions_replay import (
    conventions_from_export,
    load_conventions,
    main,
    regression_against_stored,
    render,
    replay,
)

FIRM = uuid.UUID("0000000a-0000-4000-8000-000000000001")
STUDY_A = uuid.UUID("0000000b-0000-4000-8000-000000000001")
STUDY_B = uuid.UUID("0000000b-0000-4000-8000-000000000002")
PC = "nonresidential"


def asset(
    label: str,
    bucket: str,
    *,
    method: str = "llm",
    material: str | None = None,
    attachment: str | None = "permanent",
    room_type: str | None = "living_room",
    convention: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": f"a-{label}-{material}-{room_type}",
        "component_id": f"c-{label}-{material}-{room_type}",
        "label": label,
        "material": material,
        "attachment": attachment,
        "room_type": room_type,
        "bucket": bucket,
        "method": method,
        "category": "stored",
        "irs_section": "1245" if bucket in ("y5", "y7") else "1250",
        "recovery_period": "5.0" if bucket == "y5" else "39.0",
        "confidence": 0.5,
        "needs_review": False,
        "review_reason": None,
        "total": "10.00",
        "citations": [],
        "convention": convention,
        **extra,
    }


def row(
    key: str,
    bucket: str,
    *,
    method: str = "manual",
    approvals: int = 1,
    study: uuid.UUID = STUDY_A,
    citations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "org_id": str(FIRM),
        "key": key,
        "result": {
            "category": f"ruling {key.split('|', 1)[0]}",
            "section": "1245" if bucket == "y5" else "1250",
            "bucket": bucket,
            "recovery_period": "5.0" if bucket == "y5" else "39.0",
            "asset_class": None,
            "citations": citations or [],
            "rationale": "the firm's position" if method == "manual" else "",
            "method": method,
            "rule_bucket": None,
        },
        "source": "approved_asset:x",
        "study_id": str(study),
        "reviewer_id": "0000000c-0000-4000-8000-000000000001",
        "approved_at": "2026-10-30T04:06:39.407407+00:00",
        "approvals": approvals,
        "rule_key": None,
        "agrees_with_rules": None,
        "rules_version": "2026-10-29.1",
        "hits": 0,
    }


def old_key(label: str, material: str | None, attachment: str | None, room: str | None) -> str:
    return cache_key(label, material, attachment, PC, room, rules_version="2026-10-29.1")


def export(*assets: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "basis-export/2",
        "study_id": str(STUDY_B),
        "revision": 1,
        "assets": list(assets),
        "summary": {"takeoff": {"property_class": PC}},
    }


FIREPLACE_RULING = old_key("fireplace", "wood", "permanent", "living_room")


def test_rows_are_rekeyed_to_the_running_rules_version_and_filtered_by_study() -> None:
    rows = [
        row(FIREPLACE_RULING, "y5", approvals=2),
        row(old_key("baseboard", "wood", "permanent", "bathroom"), "y5", study=STUDY_B),
        {"key": "malformed", "result": {"bucket": "y5", "category": "x"}, "org_id": str(FIRM)},
    ]
    conventions, ruling_labels = load_conventions(rows, as_of_study=STUDY_A)
    [fireplace] = conventions
    assert fireplace.key == cache_key("fireplace", "wood", "permanent", PC, "living_room")
    assert fireplace.key.endswith(RULES_VERSION) and fireplace.approvals == 1
    assert ruling_labels == frozenset({"fireplace"})
    kept, _ = load_conventions(rows, rules_version=None)
    assert [c.key.rsplit("|", 1)[-1] for c in kept] == ["2026-10-29.1", "2026-10-29.1"]
    assert [c.approvals for c in kept] == [2, 1]


def test_the_exact_policy_reproduces_the_stored_cache_assets() -> None:
    served = asset(
        "fireplace",
        "y5",
        method="cache",
        material="wood",
        convention={"key": cache_key("fireplace", "wood", "permanent", PC, "living_room")},
    )
    unserved = asset("fireplace", "y39", material=None)
    conventions, labels = load_conventions([row(FIREPLACE_RULING, "y5")])
    exact = replay(export(served, unserved), conventions, PC, FIRM, "exact", labels)
    assert exact.served_by_tier == {"exact": 1} and exact.moved == []
    assert regression_against_stored(export(served, unserved), exact) == []
    assert exact.unruled == {"fireplace@living_room": 1}
    # A stored cache asset the rows cannot explain is a regression difference.
    stale = asset("fireplace", "y5", method="cache", material="brick", convention={})
    stale_run = replay(export(stale), conventions, PC, FIRM, "exact", labels)
    assert regression_against_stored(export(stale), stale_run) == [stale["id"]]


def test_the_tiered_policy_serves_the_coarser_tiers_through_the_production_router() -> None:
    conventions, labels = load_conventions(
        [
            row(FIREPLACE_RULING, "y5"),
            row(old_key("sink", "ceramic", "permanent", "kitchen"), "y5"),
            row(old_key("other", "wood", "freestanding", "exterior"), "y15", method="llm"),
        ]
    )
    assert labels == frozenset({"fireplace", "sink"})
    document = export(
        asset("fireplace", "y39", material=None),  # the same room, no material
        asset("fireplace", "y39", material="brick", room_type="bedroom"),  # another room
        asset("sink", "y39", method="rule", material="porcelain", room_type="bathroom"),
        asset("sink", "y39", method="rule", material="porcelain", room_type="kitchen"),
        asset("other", "y7", material="fabric", room_type="exterior"),
    )
    tiered = replay(document, conventions, PC, FIRM, "tiered", labels)
    by_id = {a["id"]: a for a in tiered.export["assets"]}
    living = by_id["a-fireplace-None-living_room"]
    assert (living["method"], living["bucket"], living["convention"]["matched_tier"]) == (
        "cache",
        "y5",
        "label_room",
    )
    assert living["convention"]["key"] == conventions[0].key
    assert "matched: label and room" in living["replayed"]["rationale"]
    assert by_id["a-fireplace-brick-bedroom"]["convention"]["matched_tier"] == "label"
    # The bathroom sink keeps its uncontested rule row; the kitchen sink is served.
    assert by_id["a-sink-porcelain-bathroom"]["method"] == "rule"
    assert by_id["a-sink-porcelain-kitchen"]["method"] == "cache"
    # The catch-all label is not generalized.
    assert by_id["a-other-fabric-exterior"]["method"] == "llm"
    assert tiered.served_by_tier == {"label_room": 2, "label": 1}
    assert tiered.blocked == {"sink": 1} and tiered.newly_served == 3
    assert [d.label for d in tiered.moved] == ["fireplace", "fireplace", "sink"]
    assert tiered.unruled == {"sink@bathroom": 1}
    assert tiered.export["conventions_replay"]["buckets_moved"] == 3

    # The plain policy has no guards: the bathroom sink and the awning are served too.
    plain = replay(document, conventions, PC, FIRM, "plain", labels)
    assert plain.served_by_tier == {"label_room": 3, "label": 2} and plain.blocked == {}
    plain_by_id = {a["id"]: a for a in plain.export["assets"]}
    assert plain_by_id["a-sink-porcelain-bathroom"]["bucket"] == "y5"
    assert plain_by_id["a-other-fabric-exterior"]["bucket"] == "y15"


def test_a_conflict_keeps_the_stored_classification_and_flags_it() -> None:
    conventions, labels = load_conventions(
        [
            row(old_key("carpet", "wool", "permanent", "bedroom"), "y39"),
            row(old_key("carpet", "nylon", "removable", "bedroom"), "y5"),
        ]
    )
    stored = asset("carpet", "y5", method="rule", material="polyester", room_type="bedroom")
    tiered = replay(export(stored), conventions, PC, FIRM, "tiered", labels)
    [out] = tiered.export["assets"]
    assert (out["bucket"], out["method"], out["needs_review"]) == ("y5", "rule", True)
    assert out["review_reason"] == "convention_conflict:label_room"
    assert sorted(out["replayed"]["conflict"]) == sorted(c.key for c in conventions)
    assert tiered.conflicts == {"carpet@label_room": 1} and tiered.moved == []


def test_conventions_can_be_rebuilt_from_an_approved_export() -> None:
    cited = [{"chunk_id": "c1", "excerpt": "a passage", "corpus": "reference"}]
    seed = export(
        asset("fireplace", "y5", method="manual", material="wood"),
        asset("fireplace", "y39", method="manual", material="wood"),  # disagrees: skipped
        asset("window", "y39", method="llm", material="glass", citations=cited),
        asset("window", "y39", method="llm", material="vinyl"),  # uncited: not promoted
        asset("toilet", "y39", method="rule", material="porcelain", room_type="bathroom"),
    )
    rows = conventions_from_export(seed, PC, FIRM)
    assert [r["key"].split("|")[:2] for r in rows] == [["window", "glass"]]
    [window] = rows
    assert window["result"]["citations"] == [{"chunk_id": "c1", "excerpt": "a passage"}]
    assert window["study_id"] == str(STUDY_B) and window["approvals"] == 1
    conventions, labels = load_conventions(rows)
    assert len(conventions) == 1 and labels == frozenset()


def test_main_writes_the_tiered_export_the_report_and_the_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows_path = tmp_path / "cache.json"
    rows_path.write_text(json.dumps([row(FIREPLACE_RULING, "y5")]), encoding="utf-8")
    export_path = tmp_path / "rev-1.json"
    export_path.write_text(json.dumps(export(asset("fireplace", "y39"))), encoding="utf-8")
    out, report, result = tmp_path / "tiered.json", tmp_path / "replay.md", tmp_path / "r.json"
    code = main(
        [
            str(export_path),
            "--conventions",
            str(rows_path),
            "--as-of-study",
            str(STUDY_A),
            "--out",
            str(out),
            "--report",
            str(report),
            "--json",
            str(result),
            "--no-score",
        ]
    )
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["conventions_replay"]["policy"] == "tiered"
    assert written["assets"][0]["convention"]["matched_tier"] == "label_room"
    text = report.read_text(encoding="utf-8")
    assert "| tiered | 0 | 1 | 0 | 0 | 0 | 1 | 1 |" in text
    assert "made under rules 2026-10-29.1" in text
    decisions = json.loads(result.read_text(encoding="utf-8"))
    assert decisions["regression_differences"] == []
    assert decisions["policies"]["plain"]["served_by_tier"] == {"label_room": 1}
    assert "tiered: served {'label_room': 1}" in capsys.readouterr().err
    # The report renders without a score.
    assert render({}, None, [], 0).startswith("# Conventions replay")


def test_main_refuses_a_file_that_is_not_an_export_or_an_empty_table(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    rows_path = tmp_path / "cache.json"
    rows_path.write_text("[]", encoding="utf-8")
    assert main([str(bad), "--conventions", str(rows_path), "--no-score"]) == 2
    good = tmp_path / "rev-1.json"
    good.write_text(json.dumps(export(asset("fireplace", "y39"))), encoding="utf-8")
    assert main([str(good), "--conventions", str(rows_path), "--no-score"]) == 2
