"""``evals.reclassify_export``: a stored export re-run through the rules table with the
context key (CLASSIFICATION_CONVENTIONS.md section 7). Synthetic export only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basis.domain.rules import RULES_VERSION
from evals.reclassify_export import (
    ReclassifyError,
    change_table,
    main,
    property_class_of,
    reclassify,
    reclassify_asset,
)


def asset(label: str, bucket: str, method: str = "llm", **extra: Any) -> dict[str, Any]:
    return {
        "id": f"a-{label}-{extra.get('room_type', 'x')}-{extra.get('attachment', 'x')}",
        "label": label,
        "material": None,
        "attachment": None,
        "room_type": None,
        "bucket": bucket,
        "method": method,
        "category": "stored",
        "irs_section": "1245" if bucket in ("y5", "y7") else "1250",
        "recovery_period": "5" if bucket == "y5" else "39",
        "confidence": 0.5,
        "needs_review": False,
        "review_reason": None,
        "total": "10.00",
        **extra,
    }


def export(
    *assets: dict[str, Any], property_class: str | None = "nonresidential"
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": "basis-export/2",
        "study_id": "s",
        "revision": 1,
        "assets": list(assets),
    }
    if property_class is not None:
        document["summary"] = {"takeoff": {"property_class": property_class}}
    return document


def test_a_rule_hit_replaces_the_stored_classification_and_records_where_it_came_from() -> None:
    stored = asset("toilet", "y5", method="rule", room_type="bathroom", attachment="permanent")
    new = reclassify_asset(stored, "nonresidential")
    assert new is not None and new is not stored
    assert (new["bucket"], new["irs_section"], new["recovery_period"], new["method"]) == (
        "y39",
        "1250",
        "39",
        "rule",
    )
    assert new["reclassified"] == {
        "rules_version": RULES_VERSION,
        "rule_key": "toilet",
        "context": None,
        "stored_bucket": "y5",
        "stored_method": "rule",
    }
    assert stored["bucket"] == "y5"  # the input is not changed


def test_the_context_decides_the_variant() -> None:
    in_office = reclassify_asset(asset("furniture", "y7", room_type="office"), "nonresidential")
    in_bedroom = reclassify_asset(asset("furniture", "y7", room_type="bedroom"), "nonresidential")
    assert in_office is not None and (
        in_office["bucket"],
        in_office["reclassified"]["context"],
    ) == (
        "y7",
        "room=office",
    )
    assert in_bedroom is not None and (
        in_bedroom["bucket"],
        in_bedroom["reclassified"]["context"],
    ) == (
        "y5",
        None,
    )


def test_an_asset_no_rule_covers_keeps_its_stored_classification() -> None:
    stored = asset("electrical_outlet", "y39", method="llm")
    assert reclassify_asset(stored, "nonresidential") is None
    out, changes = reclassify(export(stored), "nonresidential")
    assert out["assets"] == [stored] and changes == []
    assert out["reclassified"]["rule_hits"] == 0 and out["reclassified"]["buckets_moved"] == 0


def test_a_contested_row_is_flagged_for_the_engineer() -> None:
    new = reclassify_asset(asset("hardwood_floor", "y5"), "residential_rental")
    assert new is not None and new["bucket"] == "y27_5"
    assert new["needs_review"] is True and new["review_reason"] == "rule_contested:hardwood_floor"


def test_reclassify_lists_only_the_assets_whose_bucket_moved() -> None:
    moved = asset("sink", "y5", method="rule", room_type="bathroom")
    same = asset("carpet", "y5", method="rule")
    out, changes = reclassify(export(moved, same), "nonresidential")
    assert out["reclassified"] == {
        "rules_version": RULES_VERSION,
        "property_class": "nonresidential",
        "assets": 2,
        "rule_hits": 2,
        "buckets_moved": 1,
    }
    assert [c["label"] for c in changes] == ["sink"]
    assert changes[0]["from"] == "y5" and changes[0]["to"] == "y39"
    assert "| sink | y5 | y39 | None | 1 |" in change_table(changes)


def test_the_property_class_comes_from_the_export_unless_overridden() -> None:
    assert property_class_of(export(), None) == "nonresidential"
    assert property_class_of(export(), "residential_rental") == "residential_rental"
    with pytest.raises(ReclassifyError):
        property_class_of(export(property_class=None), None)
    with pytest.raises(ReclassifyError):
        property_class_of(export(), "commercial")


def test_main_writes_the_new_export_and_the_change_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "rev-1.json"
    src.write_text(json.dumps(export(asset("toilet", "y5", method="rule"))), encoding="utf-8")
    out = tmp_path / "new.json"
    changes = tmp_path / "changes.json"
    assert main([str(src), "--out", str(out), "--changes", str(changes)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["assets"][0]["bucket"] == "y39"
    assert json.loads(changes.read_text(encoding="utf-8"))[0]["to"] == "y39"
    printed = capsys.readouterr().out
    assert "1 rule hits, 1 buckets moved" in printed and "| toilet | y5 | y39 |" in printed


def test_main_refuses_a_document_that_is_not_an_export(tmp_path: Path) -> None:
    src = tmp_path / "x.json"
    src.write_text(json.dumps({"format": "other", "assets": []}), encoding="utf-8")
    with pytest.raises(ReclassifyError):
        main([str(src), "--out", str(tmp_path / "o.json")])
