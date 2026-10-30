"""``evals.backfill_manifests``: runs made before manifests get one from their files, each
field with its source and ``unknown`` where the files do not say. Synthetic directories
whose ``run.json`` key sets mirror the real runners' (no client data)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basis.llm import prompts
from evals import backfill_manifests as bf
from evals import record, registry

REPORT = """# EVAL_REPORT

## 15. Per-photo accuracy campaign (2026-10-16)

The run `predictions/vision_countables_v2_sol` and `counts_sc3_median_sol` (table 3).

## 26. Vision architecture (2026-10-16)

`arch26_house_v2_sol` is cited here; so is predictions/vision_countables_v2_sol again.
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a ``main`` branch, two tags a day apart and a later commit."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _git(cwd, "init", "-q", "-b", "main")
    _git(cwd, "config", "user.email", "t@example.com")
    _git(cwd, "config", "user.name", "t")
    env_one = {"GIT_COMMITTER_DATE": "2026-10-15T10:00:00-07:00"}
    for n, (date, tag) in enumerate(
        [
            ("2026-10-15T10:00:00-07:00", "v0.1.0"),
            ("2026-10-16T10:00:00-07:00", "v0.2.0"),
            ("2026-10-17T10:00:00-07:00", None),
        ]
    ):
        (cwd / "a.txt").write_text(f"{n}\n")
        _git(cwd, "add", "a.txt")
        subprocess.run(
            ["git", "commit", "-q", "-m", f"c{n}", f"--date={date}"],
            cwd=cwd,
            check=True,
            env={
                **env_one,
                "GIT_COMMITTER_DATE": date,
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            },
        )
        if tag:
            subprocess.run(
                ["git", "tag", "-a", tag, "-m", tag],
                cwd=cwd,
                check=True,
                env={
                    "GIT_COMMITTER_DATE": date,
                    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                },
            )
    return cwd


def _write(directory: Path, name: str, data: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(data) if not isinstance(data, str) else data)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    predictions = data / "predictions"
    # A predict_photos run with a sibling log and an eval.md.
    photos = predictions / "vision_countables_v2_sol"
    _write(
        photos,
        "run.json",
        {
            "photos": 52,
            "repeats": 3,
            "vision_model_setting": "gpt-4o-mini",
            "model_requested": "gpt-6.1-sol",
            "reasoning_effort": "low",
            "max_tokens": 4000,
            "models_reported": ["gpt-6.1-sol"],
            "spent_usd": 1.9,
            "errors": [],
            "prompt": "photo_countables/v2",
        },
    )
    _write(
        photos,
        "eval.md",
        "## photo_components (system: `predictions`)\n\n"
        "| metric | value | n | i | SE |\n|---|---|---|---|---|\n| f1 | 0.5000 | 100 | | |\n\n"
        "## assets (system: `rules_table`)\n\n"
        "| metric | value | n | i | SE |\n|---|---|---|---|---|\n"
        "| bucket_accuracy | 0.1000 | 46 | | |\n",
    )
    (predictions / "vision_countables_v2_sol.log").write_text("repeat 1/3: spent $0.6\n")
    # A predict_rooms run (prompts dict, room_model).
    _write(
        predictions / "rooms16_final_sonnet",
        "run.json",
        {
            "photos": 52,
            "rooms": 33,
            "repeats": 3,
            "room_model": "claude-sonnet-5-5",
            "overlap_model": None,
            "materials_model": "claude-sonnet-5-5",
            "reasoning_effort": "low",
            "models_reported": ["claude-sonnet-5-5"],
            "spent_usd": 0.94,
            "prompts": {
                "photo_analysis": None,
                "room_inventory": "room_inventory/v3",
                "surface_material": "surface_material/v3",
            },
        },
    )
    # A merge run, a detector run, a SAM 3 run, a stopped run, a files-only run, an empty one.
    _write(
        predictions / "vision_decomposed_v2_sol",
        "run.json",
        {
            "merged_from": [
                {"run": "x", "prompt": "photo_countables/v2"},
                {"run": "y", "prompt": "photo_surfaces/v1"},
            ],
            "room_from": "x",
            "photos": 52,
            "repeats": 3,
            "spent_usd": 3.3,
        },
    )
    _write(
        predictions / "arch26_det_gdino",
        "run.json",
        {
            "detector": "gdino",
            "checkpoint": "IDEA-Research/grounding-dino-base",
            "device": "mps",
            "cost_usd": 0.0,
        },
    )
    _write(
        predictions / "sam3_photos_v3",
        "run.json",
        {
            "model": "vufinder/sam3",
            "version": "1bf9" + "0" * 60,
            "phrases_from": "all",
            "tiling": "off",
            "calls": 52,
            "spent_usd": 0.4,
        },
    )
    _write(
        predictions / "stopped_run",
        "run.json",
        {
            "complete": False,
            "stopped": {"reason": "provider_account_error"},
            "spent_usd": 0.02,
            "files_written": [],
        },
    )
    _write(predictions / "sam3_select_v1", "selection.json", {"classes": {}})
    (predictions / "empty_killed").mkdir()
    # An appraisal run with a recorded git sha (filled in by the test from the repo).
    _write(
        data / "appraisal" / "runs" / "sweep1" / "llm_all@gpt-6-luna",
        "run.json",
        {
            "run": "x",
            "mode": "replay",
            "system": {"name": "llm_all", "model": {"model": "gpt-6-luna", "effort": "low"}},
            "sets": ["gse_d1"],
            "repeats": 1,
            "git_sha": "PLACEHOLDER",
            "settings": {"text_model": "gpt-6-luna", "reasoning_effort": "low"},
            "prompt_version": "appraisal_field_fallback/v1",
            "prompt_versions": ["appraisal_fields_grounded/v2"],
            "models": ["gpt-6-luna"],
            "llm_calls": 12,
            "cost_usd": 0.0,
            "replayed_cost_usd": 0.04,
        },
    )
    _write(
        data / "appraisal" / "runs" / "sweep1" / "mismo",
        "run.json",
        {
            "run": "m",
            "mode": "replay",
            "system": {"name": "mismo_xml", "model": {"model": None}},
            "sets": ["gse_d1"],
            "repeats": 1,
            "prompt_version": "appraisal_field_fallback/v1",
            "prompt_versions": [],
            "models": [],
            "llm_calls": 0,
            "cost_usd": 0.0,
        },
    )
    _write(data / "appraisal" / "truth", "gse_d1.json", {"documents": []})
    return data


def _set_sha(root: Path, sha: str) -> None:
    path = root / "appraisal" / "runs" / "sweep1" / "llm_all@gpt-6-luna" / "run.json"
    data = json.loads(path.read_text())
    data["git_sha"] = sha
    path.write_text(json.dumps(data))


def test_guess_harness_from_run_json_keys_then_files() -> None:
    assert (
        bf.guess_harness({"detector": 1, "checkpoint": 2, "photos": 3}, set())[0]
        == "evals.predict_detector"
    )
    assert (
        bf.guess_harness({"prompts": {}, "rooms": 1, "photos": 2}, set())[0]
        == "evals.predict_rooms"
    )
    assert (
        bf.guess_harness({"prompt": "x", "photos": 2, "repeats": 1}, set())[0]
        == "evals.predict_photos"
    )
    assert (
        bf.guess_harness({"scope": "house", "template_rows": None}, set())[0]
        == "evals.predict_multiview"
    )
    assert bf.guess_harness(None, {"selection.json", "photo_counts.json"})[0] == "evals.sam3_select"
    assert bf.guess_harness(None, {"photo_raw.jsonl"})[0] == "evals.predict_photos"
    harness, how = bf.guess_harness({"complete": False, "stopped": {}}, {"photo_raw.jsonl"})
    assert harness == "evals.predict_photos" and how.startswith("files")
    assert bf.guess_harness(None, set()) == (
        bf.UNKNOWN,
        "no run.json key set or file set matches a runner",
    )


def test_report_mentions_finds_whole_tokens_and_paths_by_section() -> None:
    names = [
        "vision_countables_v2_sol",
        "counts_sc3_median_sol",
        "arch26_house_v2_sol",
        "vision_countables_v2",
    ]
    assert bf.report_mentions(REPORT, names) == {
        "vision_countables_v2_sol": [15, 26],
        "counts_sc3_median_sol": [15],
        "arch26_house_v2_sol": [26],
    }
    assert bf.prefix_section("arch26_house_v2_sol") == 26
    assert bf.prefix_section("cost28_rooms") == 28
    assert bf.prefix_section("vision_v1") is None


def test_backfill_recovers_what_the_files_say_and_marks_the_rest_unknown(
    root: Path, repo: Path, tmp_path: Path
) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _set_sha(root, head)
    report = tmp_path / "EVAL_REPORT.md"
    report.write_text(REPORT)
    lines = bf.backfill(root, repo=repo, report=report, write=False)
    assert {line["action"] for line in lines} == {"would_write"}
    assert not list(root.rglob(record.MANIFEST_FILE))  # a dry run writes nothing
    by_name = {line["manifest"]["name"]: line["manifest"] for line in lines}
    assert set(by_name) == {
        "vision_countables_v2_sol",
        "rooms16_final_sonnet",
        "vision_decomposed_v2_sol",
        "arch26_det_gdino",
        "sam3_photos_v3",
        "stopped_run",
        "sam3_select_v1",
        "empty_killed",
        "llm_all@gpt-6-luna",
        "mismo",
    }

    photos = by_name["vision_countables_v2_sol"]
    assert photos["backfilled"] is True and photos["format"] == record.FORMAT
    assert photos["harness"]["module"] == "evals.predict_photos"
    assert photos["harness"]["sha256"] == bf.UNKNOWN
    released = prompts.manifest()
    assert photos["components"]["prompts"] == [
        {
            "name": "photo_countables",
            "version": "v2",
            "sha256": released["photo_countables"]["v2"],
            "calls": bf.UNKNOWN,
        }
    ]
    assert photos["components"]["models"] == {
        "requested": ["gpt-6.1-sol"],
        "answered": ["gpt-6.1-sol"],
        "providers": [],
    }
    assert photos["components"]["request_params"] == {
        "reasoning_effort": ["low"],
        "max_output_tokens": ["4000"],
    }
    assert photos["components"]["pricing_version"] == bf.UNKNOWN
    # The code: not recorded, so unknown; the main commit and the tags around the time.
    git = photos["git"]
    assert git["sha"] == bf.UNKNOWN and git["dirty"] == bf.UNKNOWN and git["release"] == bf.UNKNOWN
    assert git["main_at_finish"] == head  # the directory was written today, after every commit
    assert (git["release_before"], git["release_after"]) == ("v0.2.0", None)
    assert "feature branches" in photos["sources"]["git"]
    # Labels from eval.md: the saved-predictions dataset only, by name; the sha unknown.
    assert photos["data"]["labels"] == {
        "photo_components": {
            "file": "photo_components_v1.json",
            "version": "v1",
            "sha256": bf.UNKNOWN,
        }
    }
    assert photos["calls"] == {"count": bf.UNKNOWN, "cost_usd": 1.9}
    assert photos["files"] == {
        "log": "../vision_countables_v2_sol.log",
        "summary": "run.json",
        "ledger": None,
        "report": "eval.md",
    }
    assert photos["report_sections"] == [15, 26]
    assert photos["tree"] == {"component": "photo_countables", "version": "v2", "link": None}
    assert photos["status"] == "ok"
    assert photos["started_at"] and photos["finished_at"] and photos["wall_seconds"] >= 0
    assert photos["summary"]["spent_usd"] == 1.9

    rooms = by_name["rooms16_final_sonnet"]
    assert rooms["harness"]["module"] == "evals.predict_rooms"
    assert [(p["name"], p["version"]) for p in rooms["components"]["prompts"]] == [
        ("room_inventory", "v3"),
        ("surface_material", "v3"),
    ]
    assert rooms["components"]["models"]["requested"] == ["claude-sonnet-5-5"]
    assert rooms["report_sections"] == [16]  # the prefix encodes the section
    assert rooms["tree"]["component"] == "room_inventory"  # the first prompt named
    assert rooms["data"]["labels"] == {} and "no eval.md" in rooms["sources"]["labels"]
    assert rooms["files"]["log"] is None

    assert by_name["vision_decomposed_v2_sol"]["harness"]["module"] == "evals.merge_photo_runs"
    assert by_name["vision_decomposed_v2_sol"]["components"]["prompts"] == []
    detector = by_name["arch26_det_gdino"]
    assert detector["harness"]["module"] == "evals.predict_detector"
    assert (detector["tree"]["component"], detector["tree"]["version"]) == (
        "detection",
        "gdino:IDEA-Research_grounding-dino-base",
    )
    assert detector["components"]["detector"] == "gdino:IDEA-Research/grounding-dino-base"
    assert detector["report_sections"] == [26]
    sam3 = by_name["sam3_photos_v3"]
    assert sam3["harness"]["module"] == "evals.predict_boxes"
    assert sam3["components"]["localizer"].startswith("sam3:1bf9")
    assert sam3["calls"] == {"count": 52, "cost_usd": 0.4}
    assert by_name["stopped_run"]["status"] == "stopped"
    assert by_name["sam3_select_v1"]["harness"]["module"] == "evals.sam3_select"
    assert by_name["sam3_select_v1"]["files"]["summary"] is None
    empty = by_name["empty_killed"]
    assert empty["status"] == "empty" and empty["harness"]["module"] == bf.UNKNOWN
    assert empty["started_at"] is None

    appraisal = by_name["llm_all@gpt-6-luna"]
    assert appraisal["harness"]["module"] == "evals.appraisal.run"
    assert appraisal["git"]["sha"] == head and appraisal["git"]["release"] == "v0.2.0"
    assert appraisal["git"]["dirty"] == bf.UNKNOWN
    assert "recorded by the appraisal runner" in appraisal["sources"]["git"]
    # The prompt the calls used comes before the fallback the runner merely declared, so
    # the run files under what ran.
    assert [p["name"] for p in appraisal["components"]["prompts"]] == [
        "appraisal_fields_grounded",
        "appraisal_field_fallback",
    ]
    assert (appraisal["tree"]["component"], appraisal["tree"]["version"]) == (
        "appraisal_fields_grounded",
        "v2",
    )
    mismo = by_name["mismo"]
    assert (mismo["tree"]["component"], mismo["tree"]["version"]) == (
        "appraisal_fields",
        "mismo_xml",
    )
    assert "no model call" in mismo["sources"]["tree"]
    merged = by_name["vision_decomposed_v2_sol"]
    assert (merged["tree"]["component"], merged["tree"]["version"]) == (
        "photo_analysis",
        "photo_countables-v2+photo_surfaces-v1",
    )
    assert appraisal["components"]["models"]["requested"] == ["gpt-6-luna"]
    assert appraisal["components"]["request_params"]["reasoning_effort"] == ["low"]
    [dataset] = appraisal["data"]["datasets"]
    assert dataset["name"] == "gse_d1" and dataset["sha256"] == bf.UNKNOWN
    assert dataset["sha256_now"] == record.sha256_file(root / "appraisal" / "truth" / "gse_d1.json")
    assert appraisal["calls"] == {"count": 12, "cost_usd": 0.0}  # a replay pays nothing
    assert appraisal["settings"] == {"text_model": "gpt-6-luna", "reasoning_effort": "low"}


def test_an_unknown_recorded_sha_falls_back_to_the_timeline(root: Path, repo: Path) -> None:
    _set_sha(root, "f" * 40)
    [appraisal] = [
        line["manifest"]
        for line in bf.backfill(root, repo=repo, report=None, write=False)
        if line["manifest"]["name"] == "llm_all@gpt-6-luna"
    ]
    assert appraisal["git"]["sha"] == "f" * 40 and appraisal["git"]["release"] == bf.UNKNOWN
    assert "not in this clone" in appraisal["sources"]["git"]


def test_write_is_idempotent_keeps_live_manifests_and_force_rewrites_backfills(
    root: Path, repo: Path
) -> None:
    _set_sha(root, _git(repo, "rev-parse", "HEAD"))
    live = root / "predictions" / "rooms16_final_sonnet" / record.MANIFEST_FILE
    live.write_text(json.dumps({"format": 1, "kind": "eval_run", "name": "live"}))
    first = bf.backfill(root, repo=repo, report=None, write=True)
    actions = {Path(line["path"]).name: line["action"] for line in first}
    assert (
        actions["rooms16_final_sonnet"] == "kept"
        and actions["vision_countables_v2_sol"] == "written"
    )
    written = json.loads(
        (root / "predictions" / "vision_countables_v2_sol" / record.MANIFEST_FILE).read_text()
    )
    assert written["backfilled"] is True
    link = root / "verification" / "photo_countables" / "v2" / "vision_countables_v2_sol"
    assert link.is_symlink()
    assert link.resolve() == (root / "predictions" / "vision_countables_v2_sol").resolve()
    assert written["tree"]["path"] == "photo_countables/v2/vision_countables_v2_sol"
    assert (root / "verification" / "README.md").is_file()
    tree = root / "verification"
    grounded = tree / "appraisal_fields_grounded" / "v2"
    assert (grounded / "sweep1__llm_all@gpt-6-luna").is_symlink()  # nested: sweep__cell
    assert (tree / "appraisal_fields" / "mismo_xml" / "sweep1__mismo").is_symlink()
    assert json.loads(live.read_text()) == {"format": 1, "kind": "eval_run", "name": "live"}
    second = bf.backfill(root, repo=repo, report=None, write=True)
    assert {line["action"] for line in second} == {"kept"}
    third = bf.backfill(root, repo=repo, report=None, write=True, force=True)
    actions = {Path(line["path"]).name: line["action"] for line in third}
    assert (
        actions["vision_countables_v2_sol"] == "written"
        and actions["rooms16_final_sonnet"] == "kept"
    )
    # The registry reads the backfilled manifests like any other.
    entries = {e.name: e for e in registry.scan(root)}
    photos = entries["vision_countables_v2_sol"]
    assert photos.backfilled is True and photos.harness == "evals.predict_photos"
    assert photos.prompts[0]["sha256"] == prompts.manifest()["photo_countables"]["v2"]
    assert photos.scores == {"photo_components": {"f1": 0.5}}
    assert photos.label_stems == {"photo_components_v1"}
    assert photos.release is None  # "unknown" is not a release
    hits = registry.filter_runs(
        entries.values(), component="photo_countables/v2", model="gpt-6.1-sol"
    )
    assert [e.name for e in hits] == ["vision_countables_v2_sol"]


def test_the_note_counts_what_was_and_was_not_recovered(
    root: Path, repo: Path, tmp_path: Path
) -> None:
    _set_sha(root, _git(repo, "rev-parse", "HEAD"))
    lines = bf.backfill(root, repo=repo, report=None, write=False)
    note = bf.render_note(lines, root=root)
    assert "| run directories found | 10 |" in note
    assert "| backfilled | 10 (8 under `predictions/`, 2 under `appraisal/runs/`) |" in note
    assert "| with a `run.json` summary | 8 |" in note
    assert "| harness identified | 8 |" in note  # the stopped run left no file to read
    assert "| prompts named (sha256 exact from the prompt manifest) | 4 |" in note
    assert "| git sha recorded (appraisal runner only) | 1 |" in note
    assert "| label files known (from `eval.md`; sha256 at the time unknown) | 1 |" in note
    assert "| terminal log kept (sibling `.log`) | 1 |" in note
    assert "| stopped by an account error | 1 |" in note
    assert "| empty directories | 1 |" in note
    assert "| filed in `verification/<component>/<version>/` as a link | 0 |" in note  # dry run
    assert "| `photo_countables` | 1 |" in note.split("## Component per run")[1]
    unidentified = note.split("## Runs whose harness could not be identified")[1]
    assert "`empty_killed`" in unidentified and "`stopped_run`" in unidentified
    assert "`sam3_select_v1`" in note.split("## Runs without a `run.json`")[1]
    assert "## Not recoverable" in note
    assert "$" not in note  # no dollar figures, no scores: counts and names only


def test_the_cli_dry_runs_by_default_and_writes_the_note(
    root: Path, repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_sha(root, _git(repo, "rev-parse", "HEAD"))
    note = tmp_path / "note.md"
    code = bf.main(
        [
            "--root",
            str(root),
            "--repo",
            str(repo),
            "--report",
            str(tmp_path / "none.md"),
            "--note",
            str(note),
        ]
    )
    assert code == 0
    out = capsys.readouterr()
    assert "would_write" in out.out and "would_write: 10" in out.err
    assert note.read_text().startswith("# Backfilled run manifests")
    assert not list(root.rglob(record.MANIFEST_FILE))
    assert bf.main(["--root", str(root), "--repo", str(repo), "--write"]) == 0
    assert len(list(root.rglob(record.MANIFEST_FILE))) == 10


def test_the_cli_refuses_without_a_data_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    assert bf.main([]) == 2
    assert "no data directory" in capsys.readouterr().err
