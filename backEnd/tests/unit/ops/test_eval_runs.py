"""``basis.ops.eval_runs``: the API's read of the evaluation registry. The pipeline's
prompts agree with the worker's model list; a run's standing against the pipeline is
computed from its prompts and models; run ids and cursors round-trip and refuse anything
not issued here; redaction masks keys, credential-named fields, the host name and every
local path; a directory the registry cannot read is listed with its error."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from basis.config import Settings
from basis.llm import prompts
from basis.ops import eval_runs
from basis.ops.eval_runs import Filters, PromptUse, Selection
from basis.worker.main import pipeline_models, room_config
from evals import record

SHA = "a" * 64


def _variants(settings: Settings) -> dict[str, Settings]:
    return {
        "single": settings.model_copy(
            update={"vision_photo_mode": "single", "room_reconcile": False}
        ),
        "grounded": settings.model_copy(
            update={
                "vision_photo_mode": "single",
                "room_reconcile": False,
                "extraction_method": "grounded",
            }
        ),
        "every": settings.model_copy(
            update={
                "vision_photo_mode": "decomposed",
                "vision_count_fusion": "per_class",
                "room_reconcile": True,
                "room_step_mode": "room_call",
                "materials_second_look": "surface_crops",
                "room_materials_model": "look-model",
                "extraction_method": "grounded",
            }
        ),
        "no_second_look": settings.model_copy(
            update={
                "vision_photo_mode": "decomposed",
                "room_reconcile": True,
                "room_materials": "none",
                "room_materials_model": None,
            }
        ),
    }


def test_pipeline_prompts_name_the_settings_pipeline_models_names(settings: Settings) -> None:
    """The two readings of the pipeline (which prompts, which models) agree on which settings
    run, so neither can drift from the activities alone."""
    for name, chosen in _variants(settings).items():
        uses = eval_runs.pipeline_prompts(chosen)
        by_setting = {u.setting: u.model for u in uses}
        models = pipeline_models(chosen, room_config(chosen))
        expected = {s: m for m, ss in models.items() for s in ss}
        assert by_setting == expected, name
        for use in uses:
            assert use.version in prompts.versions(use.name), (name, use)


def test_pipeline_prompts_follow_the_switches(settings: Settings) -> None:
    every = _variants(settings)["every"]
    names = {(u.name, u.version, u.setting) for u in eval_runs.pipeline_prompts(every)}
    assert ("photo_countables", "v3", "VISION_MODEL") in names
    assert ("photo_countables", "v3", "VISION_FUSION_MODEL") in names
    assert ("photo_surfaces", "v2", "VISION_SURFACES_MODEL") in names
    assert ("room_inventory", every.room_prompt_version, "ROOM_MODEL") in names
    assert ("surface_material", "v3", "ROOM_MATERIALS_MODEL") in names
    assert ("room_photos_inventory", "v2", "ROOM_CALL_MODEL") in names
    assert ("surface_crop_material", "v1", "SURFACE_CROP_MODEL") in names
    assert ("appraisal_fields_grounded", "v2", "EXTRACTION_GROUNDED_MODEL") in names
    assert ("appraisal_field_fallback", "v1", "TEXT_MODEL") in names
    assert ("classify", "v1", "CLASSIFY_MODEL") in names
    single = {
        (u.name, u.setting) for u in eval_runs.pipeline_prompts(_variants(settings)["single"])
    }
    assert single == {
        ("photo_analysis", "VISION_MODEL"),
        ("appraisal_field_fallback", "TEXT_MODEL"),
        ("appraisal_field_fallback_uad36", "TEXT_MODEL"),
        ("classify", "CLASSIFY_MODEL"),
    }
    look = {u.setting for u in eval_runs.pipeline_prompts(_variants(settings)["no_second_look"])}
    assert "ROOM_MATERIALS_MODEL" not in look and "ROOM_MODEL" in look


PIPELINE = [
    PromptUse("photo_countables", "v3", "VISION_MODEL", "gpt-6.1-sol"),
    PromptUse("photo_countables", "v3", "VISION_FUSION_MODEL", "claude-opus-5-5"),
    PromptUse("photo_surfaces", "v2", "VISION_SURFACES_MODEL", "gpt-6-luna"),
    PromptUse("classify", "v1", "CLASSIFY_MODEL", "claude-sonnet-5-5"),
]


def _prompt(name: str, version: str | None) -> dict[str, Any]:
    return {"name": name, "version": version, "sha256": SHA, "calls": 1}


@pytest.mark.parametrize(
    ("prompts_used", "models", "status", "needle"),
    [
        ([_prompt("photo_countables", "v3")], ["gpt-6.1-sol"], "current", "VISION_MODEL"),
        (
            [_prompt("photo_countables", "v3")],
            ["claude-opus-5-5"],
            "current",
            "VISION_FUSION_MODEL",
        ),
        (
            [_prompt("photo_countables", "v2")],
            ["gpt-6.1-sol"],
            "alternative",
            "the pipeline runs v3",
        ),
        ([_prompt("photo_countables", "v3")], ["claude-haiku-5-5"], "alternative", "the run asked"),
        ([_prompt("photo_marks", "v1")], ["gpt-6.1-sol"], "not_in_pipeline", "no such prompt"),
        (
            [_prompt("photo_countables", "v3"), _prompt("photo_marks", "v1")],
            ["gpt-6.1-sol"],
            "alternative",
            "photo_marks: the pipeline runs no such prompt",
        ),
        ([], ["gpt-6.1-sol"], "unknown", "names no prompt"),
        (
            [_prompt("photo_countables", None)],
            ["gpt-6.1-sol"],
            "unknown",
            "version is not recorded",
        ),
        ([_prompt("photo_countables", "v3")], [], "unknown", "model is not recorded"),
        ([_prompt("photo_countables", "unknown")], [], "unknown", "version is not recorded"),
    ],
)
def test_selection_against_the_pipeline(
    prompts_used: list[dict[str, Any]], models: list[str], status: str, needle: str
) -> None:
    chosen = eval_runs.selection(prompts_used, models, PIPELINE)
    assert chosen.status == status, chosen
    assert any(needle in reason for reason in chosen.reasons), chosen


def test_run_ids_round_trip_and_refuse_what_was_not_issued() -> None:
    for path in (
        "predictions/arch26_x",
        "appraisal/runs/sweep/llm_all@gpt-6-luna",
        "verification/a/v1/r",
    ):
        assert eval_runs.decode_run_id(eval_runs.run_id(path)) == path
        assert "/" not in eval_runs.run_id(path) and "=" not in eval_runs.run_id(path)
    for bad in ("", "/etc/passwd", "predictions/../x", "..", "a//b", "a/./b", "a\\b", "a\x00b"):
        assert eval_runs.decode_run_id(eval_runs.run_id(bad)) is None, bad
    assert eval_runs.decode_run_id("not base64!") is None
    assert eval_runs.decode_run_id("x" * 600) is None
    assert eval_runs.decode_run_id(eval_runs.run_id("predictions/x")[:-1] + "\xff") is None


def _record(name: str, started: str | None, path: str | None = None) -> eval_runs.RunRecord:
    return eval_runs.RunRecord(
        run_id=eval_runs.run_id(path or f"predictions/{name}"),
        name=name,
        path=path or f"predictions/{name}",
        kind="predictions",
        directory=Path("/nowhere"),
        entry=None,
        started_at=started,
    )


def test_cursors_round_trip_and_pages_are_newest_first() -> None:
    items = sorted(
        [
            _record("a", "2026-10-20T10:00:00+00:00"),
            _record("b", "2026-10-21T10:00:00+00:00"),
            _record("b", "2026-10-21T10:00:00+00:00", path="verification/x/v1/b"),
            _record("c", None),
            _record("d", "2026-10-19T10:00:00+00:00"),
        ],
        key=lambda r: r.sort_key,
        reverse=True,
    )
    first, cursor = eval_runs.page(items, after=None, limit=2)
    assert [i.path for i in first] == ["verification/x/v1/b", "predictions/b"]
    assert cursor is not None and eval_runs.decode_cursor(cursor) == first[-1].sort_key
    second, cursor2 = eval_runs.page(items, after=eval_runs.decode_cursor(cursor), limit=2)
    assert [i.name for i in second] == ["a", "d"]
    third, cursor3 = eval_runs.page(items, after=eval_runs.decode_cursor(cursor2 or ""), limit=2)
    assert [i.name for i in third] == ["c"] and cursor3 is None
    assert eval_runs.page(items, after=None, limit=5) == (items, None)
    assert eval_runs.page([], after=None, limit=5) == ([], None)
    for bad in ("", "!!", "eyJhIjogMX0", "x" * 2000):
        assert eval_runs.decode_cursor(bad) is None, bad


def test_redaction_masks_keys_fields_host_and_paths(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    text = (
        f"read {root}/predictions/x and {root.resolve()}/labels.json; token sk-ABCDEFGHIJKLMNOP; "
        "home /Users/someone/basis-eval-data/x and /home/ci/checkout/backEnd; "
        "relative predictions/x stays; "
        "sha 858a2493980dcc75995c62f24728f98264810fed0848d644c804002f17a6d58a"
    )
    out = eval_runs.redact_text(text, root)
    assert str(root) not in out and str(root.resolve()) not in out
    assert "<data dir>/predictions/x" in out and "<data dir>/labels.json" in out
    assert "sk-ABCDEFGHIJKLMNOP" not in out and "[redacted]" in out
    assert "/Users/someone" not in out and "~/basis-eval-data/x" in out
    assert "/home/ci" not in out and "~/checkout/backEnd" in out
    assert "predictions/x stays" in out
    assert "858a2493980dcc75995c62f24728f98264810fed0848d644c804002f17a6d58a" in out
    value = {
        "hostname": "my-laptop.local",
        "api_key": "sk-ABCDEFGHIJKLMNOP",
        "nested": {
            "Authorization": "Bearer x",
            "path": f"{root}/a",
            "ok": [1, float("nan"), "r8_ABCDEFGHIJ"],
        },
        "score": float("inf"),
    }
    redacted = eval_runs.redact_value(value, root)
    assert redacted["hostname"] == "***" and redacted["api_key"] == "***"
    assert redacted["nested"]["Authorization"] == "***"
    assert redacted["nested"]["path"] == "<data dir>/a"
    assert redacted["nested"]["ok"] == [1, None, "[redacted]"]
    assert redacted["score"] is None
    assert json.dumps(redacted)  # nothing non-finite survives


def _manifest(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "format": 1,
        "kind": "eval_run",
        "name": name,
        "harness": {"module": "evals.predict_photos", "command": [], "options": {}},
        "started_at": "2026-10-20T10:00:00+00:00",
        "finished_at": "2026-10-20T10:10:00+00:00",
        "status": "ok",
        "git": {"sha": "b" * 40, "dirty": False, "release": "v0.9.0"},
        "components": {
            "prompts": [{"name": "photo_countables", "version": "v3", "sha256": SHA, "calls": 1}],
            "models": {"requested": ["gpt-6.1-sol"], "answered": ["gpt-6.1-sol"]},
        },
        "data": {"labels": {}, "split": "dev", "repeats": 3},
        "calls": {"count": 1, "cost_usd": 0.5, "input_tokens": 10, "output_tokens": 2},
        "files": {},
        "tree": {"component": "photo_countables", "version": "v3"},
        **extra,
    }


def test_scan_lists_a_broken_directory_with_its_error_and_the_rest(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions"
    (predictions / "good").mkdir(parents=True)
    (predictions / "good" / "manifest.json").write_text(json.dumps(_manifest("good")))
    (predictions / "types").mkdir()
    (predictions / "types" / "manifest.json").write_text(
        json.dumps(_manifest("types", components="not a mapping"))
    )
    (predictions / "types" / "console.log").write_text(f"ran in {tmp_path}\n")
    (predictions / "syntax").mkdir()
    (predictions / "syntax" / "manifest.json").write_text("{not json")
    (predictions / "syntax" / "run.json").write_text(json.dumps({"spent_usd": 0.1}))
    records = eval_runs.scan(tmp_path, PIPELINE)
    by_name = {r.name: r for r in records}
    assert set(by_name) == {"good", "types", "syntax"}
    good = by_name["good"]
    assert good.component == "photo_countables" and good.selection.status == "current"
    assert good.tokens == {"input": 10, "output": 2}
    assert (good.cost_usd, good.cost_source) == (0.5, "ledger")
    assert good.split == "dev" and good.repeats == 3
    assert good.files == {"manifest": True, "report": False, "ledger": False, "console": False}
    types = by_name["types"]
    assert types.entry is None and types.record_error is not None
    assert "AttributeError" in types.record_error and str(tmp_path) not in types.record_error
    assert types.manifest is True and types.component is None and types.files["console"] is True
    assert types.selection == Selection("unknown", ["the record could not be read"])
    syntax = by_name["syntax"]
    assert syntax.manifest is False and syntax.record_error is not None
    assert "not valid JSON" in syntax.record_error
    assert (syntax.cost_usd, syntax.cost_source) == (0.1, "summary")
    # A broken record matches no filter; the others match as the registry does.
    assert not eval_runs.matches(types, Filters(components=("photo_countables",)))
    assert eval_runs.matches(good, Filters(components=("nothing", "photo_countables/v3")))
    assert eval_runs.matches(good, Filters(model="gpt-6*", split="dev", status="ok"))
    assert not eval_runs.matches(good, Filters(split="held_out"))
    assert eval_runs.matches(types, Filters()) and eval_runs.matches(
        syntax, Filters(status="unknown")
    )


def test_read_file_redacts_and_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "predictions" / "r"
    run_dir.mkdir(parents=True)
    manifest = _manifest(
        "r",
        environment={"hostname": "laptop.local", "basis_version": "0.12.0"},
        data={"data_dir": str(tmp_path), "labels": {}, "split": "dev", "repeats": 1},
    )
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    rows = [
        {
            "id": "c1",
            "model": "gpt-6.1-sol",
            "error": None,
            "request_params": {"api_key": "sk-ABCDEFGHIJKLMNOP"},
        },
        {
            "id": "c2",
            "model": "gpt-6.1-sol",
            "error": f"HTTP 500 at {tmp_path}/x Bearer sk-ZYXWVUTSRQPONML",
        },
    ]
    (run_dir / record.LEDGER_FILE).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json {\n"
    )
    (run_dir / record.LOG_FILE).write_text(
        f"start {tmp_path}/predictions/r\nkey sk-ABCDEFGHIJKLMNOP\n"
    )
    (run_dir / "eval.md").write_text("# report\n")
    [item] = eval_runs.scan(tmp_path, PIPELINE)
    manifest_file = eval_runs.read_file(item, tmp_path, "manifest")
    assert manifest_file is not None and manifest_file.media_type == "application/json"
    document = json.loads(manifest_file.body)
    assert document["environment"]["hostname"] == "***"
    assert document["data"]["data_dir"] == "<data dir>"
    assert manifest_file.filename == "r.manifest.json"
    ledger = eval_runs.read_file(item, tmp_path, "ledger")
    assert ledger is not None
    lines = ledger.body.decode().splitlines()
    assert json.loads(lines[0])["request_params"]["api_key"] == "***"
    assert json.loads(lines[1])["error"] == "HTTP 500 at <data dir>/x Bearer [redacted]"
    assert lines[2] == "not json {" and str(tmp_path) not in ledger.body.decode()
    console = eval_runs.read_file(item, tmp_path, "console")
    assert (
        console is not None
        and console.body.decode() == "start <data dir>/predictions/r\nkey [redacted]\n"
    )
    report = eval_runs.read_file(item, tmp_path, "report")
    assert report is not None and report.body == b"# report\n" and report.filename == "r.report.md"
    assert item.files == {"manifest": True, "report": True, "ledger": True, "console": True}
    monkeypatch.setattr(eval_runs, "MAX_FILE_BYTES", 10)
    with pytest.raises(eval_runs.FileTooLarge):
        eval_runs.read_file(item, tmp_path, "console")
    # A link out of the data directory is not a run file.
    (run_dir / "report.md").symlink_to(Path(__file__))
    assert eval_runs._file_present(run_dir, tmp_path, item.entry, "report") == run_dir / "eval.md"


def test_data_root_says_why_there_is_nothing(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unset = eval_runs.data_root(settings.model_copy(update={"eval_data_dir": None}))
    assert (
        isinstance(unset, eval_runs.Unavailable)
        and "BASIS_EVAL_DATA_DIR is not set" in unset.detail
    )
    missing = eval_runs.data_root(settings.model_copy(update={"eval_data_dir": tmp_path / "none"}))
    assert isinstance(missing, eval_runs.Unavailable) and "does not exist" in missing.detail
    assert eval_runs.data_root(settings.model_copy(update={"eval_data_dir": tmp_path})) == tmp_path
    monkeypatch.setattr(eval_runs, "TOOLING_INSTALLED", False)
    absent = eval_runs.data_root(settings.model_copy(update={"eval_data_dir": tmp_path}))
    assert isinstance(absent, eval_runs.Unavailable) and "not installed" in absent.detail


def test_non_finite_scores_become_null() -> None:
    assert eval_runs._finite(float("nan")) is None and eval_runs._finite(math.inf) is None
    assert eval_runs._finite(True) is None and eval_runs._finite(3) == 3.0
