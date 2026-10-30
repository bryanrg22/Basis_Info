"""``evals.registry``: the run directories answer "which runs exercised component X at
version Y on model Z against label version W, with what score, cost and date" without a
model call. Synthetic run directories only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals import record, registry

SHA = "a" * 64


def _manifest(
    name: str,
    *,
    prompts: list[tuple[str, str]],
    model: str,
    release: str = "v0.9.0",
    started: str = "2026-10-20T10:00:00+00:00",
    effort: str = "low",
    harness: str = "evals.predict_photos",
    cost: float = 1.5,
    calls: int = 52,
    backfilled: bool = False,
    labels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format": 1,
        "kind": "eval_run",
        "id": f"id-{name}",
        "name": name,
        "backfilled": backfilled,
        "harness": {"module": harness, "sha256": SHA, "command": [], "options": {}},
        "started_at": started,
        "finished_at": started,
        "status": "ok",
        "git": {"sha": "b" * 40, "short": "bbbbbbb", "dirty": False, "release": release},
        "components": {
            "prompts": [{"name": n, "version": v, "sha256": SHA, "calls": 1} for n, v in prompts],
            "models": {"requested": [model], "answered": [model], "providers": ["openai"]},
            "request_params": {"reasoning_effort": [effort]},
            "embedder": {"model": "sentence-transformers/all-MiniLM-L6-v2", "revision": "x"},
        },
        "data": {
            "inputs": [],
            "datasets": [],
            "labels": labels or {},
            "split": "dev",
            "repeats": 3,
        },
        "calls": {"count": calls, "cost_usd": cost},
        "files": {"log": "run.log", "summary": "run.json", "ledger": "llm_calls.jsonl"},
        "summary": {"photos": 52, "spent_usd": cost},
    }


def _scores(
    dataset: str, metric: str, value: float, *, label_file: str, when: str
) -> dict[str, Any]:
    return {
        "format": 1,
        "kind": "scores",
        "scored_at": when,
        "git": {"sha": "c" * 40},
        "data": {
            "labels": {
                dataset: {
                    "file": label_file,
                    "version": record.label_version(label_file),
                    "sha256": SHA,
                }
            },
            "split": "dev",
            "repeats": 3,
        },
        "results": {
            dataset: {
                "system": "predictions",
                "skipped": False,
                "metrics": {metric: {"value": value, "n": 52, "stderr": None, "interval": None}},
            },
            "assets": {
                "system": "rules_table",  # a baseline, not this run's predictions
                "skipped": False,
                "metrics": {"bucket_accuracy": {"value": 0.1, "n": 46}},
            },
        },
    }


EVAL_MD = """# Basis evaluation report

- split: `dev`

## appraisal_fields (system: `printed_text`)

| metric | value | n | 95% interval | SE |
|---|---|---|---|---|
| exact_accuracy | 0.0000 | 88 | [0.0000, 0.0400] | 0.0000 |

## photo_components (system: `predictions`)

| metric | value | n | 95% interval | SE |
|---|---|---|---|---|
| precision | 0.7000 | 100 | [0.6, 0.8] | nan |
| f1 | 0.6500 | 120 | [0.6, 0.7] | 0.0200 |

Note: 'other' excluded

## photo_counts (system: `predictions`)

| metric | value | n | 95% interval | SE |
|---|---|---|---|---|
| count_mae | 0.2500 | 312 |  | 0.0300 |

## detection_boxes (system: `-`)

Skipped: no saved predictions.
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    # 1. a current run: manifest + scores.jsonl
    a = predictions / "countables_v3_sol"
    a.mkdir()
    (a / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "countables_v3_sol", prompts=[("photo_countables", "v3")], model="gpt-6.1-sol"
            )
        )
    )
    record.append_scores(
        a,
        _scores(
            "photo_components",
            "f1",
            0.60,
            label_file="photo_components_v1.json",
            when="2026-10-20T11:00:00+00:00",
        ),
    )
    record.append_scores(
        a,
        _scores(
            "photo_components",
            "f1",
            0.62,
            label_file="photo_components_v2.json",
            when="2026-10-21T11:00:00+00:00",
        ),
    )
    # 2. an older run with the same prompt on another model, scored by eval.md only (no manifest)
    b = predictions / "countables_v3_opus"
    b.mkdir()
    (b / "run.json").write_text(
        json.dumps(
            {
                "photos": 52,
                "repeats": 3,
                "model_requested": "claude-opus-5-5",
                "reasoning_effort": "medium",
                "models_reported": ["claude-opus-5-5"],
                "spent_usd": 4.2,
                "prompt": "photo_countables/v3",
            }
        )
    )
    (b / "eval.md").write_text(EVAL_MD)
    # 3. a backfilled run of a different prompt version
    c = predictions / "countables_v2_sol"
    c.mkdir()
    (c / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "countables_v2_sol",
                prompts=[("photo_countables", "v2")],
                model="gpt-6.1-sol",
                release="v0.7.0",
                started="2026-10-16T03:00:00-07:00",
                backfilled=True,
                labels={
                    "photo_components": {
                        "file": "photo_components_v1.json",
                        "version": "v1",
                        "sha256": "unknown",
                    }
                },
            )
            | {"report_sections": [11, 15]}
        )
    )
    # 4. a stopped run with no prompt and no model
    d = predictions / "stopped"
    d.mkdir()
    (d / "run.json").write_text(
        json.dumps({"complete": False, "stopped": {"reason": "x"}, "spent_usd": 0.01})
    )
    # 5. an appraisal run with score.json
    e = tmp_path / "appraisal" / "runs" / "sweep" / "llm_all@gpt-6-luna"
    e.mkdir(parents=True)
    (e / "run.json").write_text(
        json.dumps(
            {
                "mode": "replay",
                "models": ["gpt-6-luna"],
                "prompt_versions": ["appraisal_fields_grounded/v2"],
                "cost_usd": 0.0,
            }
        )
    )
    (e / "score.json").write_text(
        json.dumps({"field_accuracy": 0.91, "by_tier": {"critical": 1.0}, "keys": ["a"]})
    )
    (e / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "llm_all@gpt-6-luna",
                prompts=[("appraisal_fields_grounded", "v2")],
                model="gpt-6-luna",
                harness="evals.appraisal.run",
                started="2026-10-28T01:00:00+00:00",
                release="v0.10.0",
            )
        )
    )
    return tmp_path


def test_scan_reads_every_run_directory_newest_first(root: Path) -> None:
    entries = registry.scan(root)
    assert [e.name for e in entries] == [
        "llm_all@gpt-6-luna",
        "countables_v3_sol",
        "countables_v2_sol",
        "stopped",  # no start time: after the dated ones, by name (reverse order)
        "countables_v3_opus",
    ]
    by_name = {e.name: e for e in entries}
    assert by_name["llm_all@gpt-6-luna"].kind == "appraisal"
    assert by_name["llm_all@gpt-6-luna"].path == "appraisal/runs/sweep/llm_all@gpt-6-luna"
    assert by_name["countables_v3_sol"].kind == "predictions"


def test_a_manifest_run_carries_code_components_labels_and_the_latest_scores(root: Path) -> None:
    entry = {e.name: e for e in registry.scan(root)}["countables_v3_sol"]
    assert entry.harness == "evals.predict_photos"
    assert (entry.git_sha, entry.git_dirty, entry.release) == ("b" * 40, False, "v0.9.0")
    assert entry.prompts == [
        {"name": "photo_countables", "version": "v3", "sha256": SHA, "calls": 1}
    ]
    assert "photo_countables/v3" in entry.components and "photo_countables" in entry.components
    assert "embedder:sentence-transformers/all-MiniLM-L6-v2" in entry.components
    assert entry.models_requested == ["gpt-6.1-sol"] and entry.reasoning_effort == ["low"]
    # The latest scoring wins, and the baseline-scored dataset is not this run's score.
    assert entry.scores == {"photo_components": {"f1": 0.62}}
    assert entry.labels["photo_components"]["file"] == "photo_components_v2.json"
    assert entry.labels["photo_components"]["scored_at"] == "2026-10-21T11:00:00+00:00"
    assert entry.label_stems == {"photo_components_v2"}
    assert (entry.cost_usd, entry.calls) == (1.5, 52)
    assert entry.manifest is True and entry.backfilled is False


def test_a_run_json_only_run_is_read_from_the_summary_and_its_eval_md(root: Path) -> None:
    entry = {e.name: e for e in registry.scan(root)}["countables_v3_opus"]
    assert entry.manifest is False and entry.harness is None and entry.release is None
    assert entry.prompts == [{"name": "photo_countables", "version": "v3", "sha256": None}]
    assert entry.models_requested == ["claude-opus-5-5"]
    assert entry.models_answered == ["claude-opus-5-5"]
    assert entry.reasoning_effort == ["medium"]
    assert entry.cost_usd == 4.2 and entry.calls is None
    # eval.md: only the datasets scored by saved predictions; the baseline is left out.
    assert entry.scores == {
        "photo_components": {"precision": 0.7, "f1": 0.65},
        "photo_counts": {"count_mae": 0.25},
    }
    assert entry.labels["photo_components"] == {
        "file": "photo_components_v1.json",
        "version": "v1",
        "sha256": None,
        "scored_at": None,
    }
    assert entry.labels["photo_counts"]["file"] is None  # a derived dataset has no file


def test_parse_eval_md_can_keep_the_baselines_too() -> None:
    everything = registry.parse_eval_md(EVAL_MD, system=None)
    assert set(everything) == {"appraisal_fields", "photo_components", "photo_counts"}
    assert everything["appraisal_fields"] == {"exact_accuracy": 0.0}


def test_a_stopped_run_and_an_appraisal_run_are_read(root: Path) -> None:
    by_name = {e.name: e for e in registry.scan(root)}
    stopped = by_name["stopped"]
    assert stopped.status == "stopped" and stopped.prompts == [] and stopped.cost_usd == 0.01
    appraisal = by_name["llm_all@gpt-6-luna"]
    assert appraisal.scores == {"appraisal": {"field_accuracy": 0.91, "by_tier.critical": 1.0}}
    assert appraisal.harness == "evals.appraisal.run"


def test_the_question_which_runs_tested_a_prompt_version_on_a_model_against_labels(
    root: Path,
) -> None:
    entries = registry.scan(root)
    hits = registry.filter_runs(
        entries, component="photo_countables/v3", model="gpt-6.1-sol", labels="photo_components_v2"
    )
    assert [e.name for e in hits] == ["countables_v3_sol"]
    # v1 labels: only the backfilled v2-prompt run and the eval.md-scored opus run.
    assert {e.name for e in registry.filter_runs(entries, labels="photo_components_v1")} == {
        "countables_v2_sol",
        "countables_v3_opus",
    }
    assert [
        e.name
        for e in registry.filter_runs(entries, component="photo_countables", model="claude-*")
    ] == ["countables_v3_opus"]
    assert [e.name for e in registry.filter_runs(entries, release="v0.7.0")] == [
        "countables_v2_sol"
    ]
    assert [e.name for e in registry.filter_runs(entries, status="stopped")] == ["stopped"]
    assert [e.name for e in registry.filter_runs(entries, harness="evals.appraisal.run")] == [
        "llm_all@gpt-6-luna"
    ]
    assert {e.name for e in registry.filter_runs(entries, since="2026-10-20")} == {
        "countables_v3_sol",
        "llm_all@gpt-6-luna",
    }
    assert {e.name for e in registry.filter_runs(entries, dataset="photo_counts")} == {
        "countables_v3_opus"
    }
    assert registry.filter_runs(entries, component="photo_countables/v9") == []


def test_markdown_and_json_render_every_run_with_its_record_source(root: Path) -> None:
    entries = registry.scan(root)
    text = registry.render_markdown(entries)
    lines = text.splitlines()
    assert lines[0].startswith("| run | started | release | git |")
    assert len(lines) == 2 + len(entries)
    sol = next(line for line in lines if "`countables_v3_sol`" in line)
    assert "| v0.9.0 | bbbbbbb | predict_photos | photo_countables/v3 | gpt-6.1-sol | low |" in sol
    assert "photo_components_v2 | photo_components f1=0.620 | $1.50 | 52 | manifest |" in sol
    old = next(line for line in lines if "`countables_v2_sol`" in line)
    assert old.endswith("| backfilled; §11,15 |")
    opus = next(line for line in lines if "`countables_v3_opus`" in line)
    assert (
        "| ? | ? | ? | photo_countables/v3 | claude-opus-5-5 | medium | photo_components_v1 |"
        in opus
    )
    assert "run.json |" in opus
    data = json.loads(registry.render_json(entries))
    assert {d["name"] for d in data} == {e.name for e in entries}
    assert data[0]["scores"] == {"appraisal": {"field_accuracy": 0.91, "by_tier.critical": 1.0}}


def test_the_matrix_counts_runs_per_prompt_version_and_model_with_the_labels_seen(
    root: Path,
) -> None:
    cells = registry.matrix(registry.filter_runs(registry.scan(root), component="photo_countables"))
    assert cells == {
        "photo_countables/v3": {
            "gpt-6.1-sol": {"runs": 1, "labels": ["photo_components_v2"], "releases": ["v0.9.0"]},
            "claude-opus-5-5": {"runs": 1, "labels": ["photo_components_v1"], "releases": []},
        },
        "photo_countables/v2": {
            "gpt-6.1-sol": {"runs": 1, "labels": ["photo_components_v1"], "releases": ["v0.7.0"]},
        },
    }
    table = registry.render_matrix(cells)
    assert table.splitlines()[0] == "| component | claude-opus-5-5 | gpt-6.1-sol |"
    assert "| `photo_countables/v2` | - | 1 (photo_components_v1) |" in table
    assert "| `photo_countables/v3` | 1 (photo_components_v1) | 1 (photo_components_v2) |" in table


def test_the_cli_writes_markdown_and_json_and_filters(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_md = tmp_path / "runs.md"
    out_json = tmp_path / "runs.json"
    code = registry.main(
        [
            "--root",
            str(root),
            "--component",
            "photo_countables/v3",
            "--model",
            "gpt-6.1-sol",
            "--labels",
            "photo_components_v2",
            "--markdown",
            str(out_md),
            "--json",
            str(out_json),
        ]
    )
    assert code == 0
    assert "`countables_v3_sol`" in out_md.read_text()
    assert [d["name"] for d in json.loads(out_json.read_text())] == ["countables_v3_sol"]
    assert "1 runs" in capsys.readouterr().err
    assert registry.main(["--root", str(root), "--matrix"]) == 0
    assert "| component |" in capsys.readouterr().out


def test_the_cli_refuses_without_a_data_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    assert registry.main([]) == 2
    assert "no data directory" in capsys.readouterr().err
