"""``evals.record``: an evaluation run leaves the study's ledger (one ``llm_calls`` row per
call as a JSONL line, the two payload files), its terminal output and a manifest naming the
code, the components, the data and the cost; the account-stop decorator finishes it on
any exit."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from basis.db.models import LlmCall
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage, prompts
from basis.llm.call import CallContext, call_model, step_entry
from basis.llm.cost import PRICING_VERSION
from basis.llm.errors import ProviderAccountError
from basis.llm.provider import ChatRequest, ImagePart, Message
from basis.llm.recorder import KIND_LOCALIZATION
from evals import account_stop, record

KEY = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # pragma: allowlist secret (a placeholder)


def _request(prompt: prompts.Prompt, study_id: uuid.UUID, text: str = "count") -> ChatRequest:
    return ChatRequest(
        model="gpt-4o-mini",
        system=prompt.text,
        messages=(
            Message(
                role="user",
                content=text,
                images=(ImagePart(data_base64="QUJD", ref="photo-1"),),
            ),
        ),
        prompt_version=prompt.version_id,
        reasoning_effort="low",
        study_id=str(study_id),
        activity="evals.test",
    )


async def _one_call(run: record.EvalRun, prompt: prompts.Prompt, text: str = "count") -> None:
    provider = ScriptedProvider(
        [ScriptedTurn(text='{"ok": true}', usage=Usage(input_tokens=1000, output_tokens=20))]
    )
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.test",
        budget=Budget(remaining_usd=5.0),
        recorder=run.recorder,
    )
    await call_model(provider, _request(prompt, ctx.study_id, text), ctx)


# --------------------------------------------------------------------------- the ledger


async def test_the_ledger_line_has_every_llm_calls_column_and_the_payloads_exist(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    run = record.EvalRun.begin(out, harness="evals.record", log=False)
    prompt = prompts.latest("photo_countables")
    await _one_call(run, prompt)
    run.finish({"photos": 1})

    rows = record.read_ledger(out)
    assert len(rows) == 1
    row = rows[0]
    columns = {c.name for c in LlmCall.__table__.columns}
    assert set(row) == columns, set(row) ^ columns
    assert row["prompt_version"] == prompt.version_id
    assert row["prompt_name"] == prompt.name
    assert row["prompt_sha256"] == prompt.sha256
    assert row["requested_model"] == "gpt-4o-mini"
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 20)
    assert row["reasoning_effort"] == "low"
    assert row["pricing_version"] == PRICING_VERSION
    assert row["build_sha"] == run.git["sha"]
    assert row["created_at"].startswith("20")
    # The payloads sit beside the ledger under the paths the row references.
    request_file = out / row["request_uri"]
    response_file = out / row["response_uri"]
    assert request_file.is_file() and response_file.is_file()
    assert request_file.name == f"{row['id']}.request.json"
    request = json.loads(request_file.read_text())
    assert request["call_id"] == row["id"]
    assert request["prompt"] == {
        "name": prompt.name,
        "version": prompt.version,
        "sha256": prompt.sha256,
    }
    assert request["messages"][0]["images"][0]["ref"] == "photo-1"
    assert "data_base64" not in json.dumps(request)
    response = json.loads(response_file.read_text())
    assert response["cost_usd"] == row["cost_usd"]
    assert response["usage"]["input_tokens"] == 1000
    # The row's sha256 is the sha256 of the stored bytes.
    assert record.sha256_file(request_file) == row["request_sha256"]


async def test_api_keys_are_redacted_in_the_ledger_the_payloads_and_the_log(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    run = record.EvalRun.begin(out, harness="evals.record")
    try:
        print(f"the key is {KEY}")
        logging.getLogger("evals.test").warning("token r8_ABCDEFGHIJKLMNOPQRST")
        await _one_call(run, prompts.latest("photo_countables"), text=f"use {KEY}")
    finally:
        run.finish({})
    everything = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert KEY not in everything
    assert "r8_ABCDEFGHIJKLMNOPQRST" not in everything  # pragma: allowlist secret
    assert "[redacted]" in (out / record.LOG_FILE).read_text()
    assert "[redacted]" in (out / record.LEDGER_FILE).read_text() or "[redacted]" in "".join(
        p.read_text() for p in (out / record.PAYLOAD_DIR).iterdir()
    )


async def test_a_metered_step_is_a_ledger_row_too(tmp_path: Path) -> None:
    out = tmp_path / "run"
    run = record.EvalRun.begin(out, harness="evals.record", log=False)
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.test",
        budget=Budget(remaining_usd=1.0),
        recorder=run.recorder,
    )
    await run.recorder.record(
        step_entry(
            ctx,
            kind=KIND_LOCALIZATION,
            provider="replicate",
            model="sam3:abc",
            quantity=2.5,
            unit="second",
            unit_price_usd=0.001,
            cost_usd=0.0025,
            params={"phrases": ["window"]},
        )
    )
    manifest = run.finish({})
    [row] = record.read_ledger(out)
    assert (row["kind"], row["quantity"], row["unit"], row["cost_usd"]) == (
        KIND_LOCALIZATION,
        "2.5",
        "second",
        "0.002500",
    )
    assert manifest["calls"]["by_kind"] == {KIND_LOCALIZATION: 1}
    assert manifest["calls"]["cost_usd"] == 0.0025


# --------------------------------------------------------------------------- the log


async def test_stdout_stderr_and_logging_go_to_the_log_and_the_streams_are_restored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run"
    before = (sys.stdout, sys.stderr)
    run = record.EvalRun.begin(out, harness="evals.record")
    print("to stdout")
    print("to stderr", file=sys.stderr)
    logging.getLogger("evals.test").warning("logged line")
    run.finish({})
    assert (sys.stdout, sys.stderr) == before
    log = (out / record.LOG_FILE).read_text()
    assert "to stdout" in log and "to stderr" in log and "logged line" in log
    assert log.startswith("# evals.record started ")
    assert "status=ok" in log.splitlines()[-1]
    captured = capsys.readouterr()  # the terminal still saw everything
    assert "to stdout" in captured.out and "to stderr" in captured.err
    print("after")  # not in the log any more
    assert "after" not in (out / record.LOG_FILE).read_text()


# --------------------------------------------------------------------------- the manifest


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny repository with one tagged release and one commit after it."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _git(cwd, "init", "-q", "-b", "main")
    _git(cwd, "config", "user.email", "t@example.com")
    _git(cwd, "config", "user.name", "t")
    (cwd / "a.txt").write_text("a\n")
    _git(cwd, "add", "a.txt")
    _git(cwd, "commit", "-q", "-m", "one")
    _git(cwd, "tag", "v0.1.0")
    (cwd / "a.txt").write_text("b\n")
    _git(cwd, "commit", "-q", "-am", "two")
    return cwd


def test_git_state_names_the_release_the_sha_and_the_dirty_flag(repo: Path) -> None:
    clean = record.git_state(repo)
    assert clean["sha"] == _git(repo, "rev-parse", "HEAD")
    assert clean["short"] == clean["sha"][:7]
    assert clean["branch"] == "main"
    assert clean["release"] == "v0.1.0"
    assert clean["commits_since_release"] == 1
    assert clean["dirty"] is False and clean["untracked"] == 0
    assert clean["describe"].startswith("v0.1.0-1-g")
    (repo / "a.txt").write_text("c\n")
    (repo / "new.txt").write_text("n\n")
    dirty = record.git_state(repo)
    assert dirty["dirty"] is True and dirty["untracked"] == 1
    assert dirty["describe"].endswith("-dirty")


def test_git_state_outside_a_repository_falls_back_to_GIT_SHA(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "plain").mkdir()
    monkeypatch.setenv("GIT_SHA", "f" * 40)
    state = record.git_state(tmp_path / "plain")
    assert state["sha"] == "f" * 40 and state["short"] == "fffffff"
    assert state["dirty"] is None and state["release"] is None
    monkeypatch.delenv("GIT_SHA")
    assert record.git_state(tmp_path / "plain")["sha"] is None


def test_git_state_defaults_to_the_harness_checkout_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = record.git_state()
    # This test runs inside the Basis checkout (or a CI checkout with GIT_SHA set).
    assert state["sha"] is not None and len(state["sha"]) == 40


async def test_the_manifest_names_code_components_data_and_cost(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path / "data"))
    labels = tmp_path / "data" / "photo_components_v1.json"
    labels.parent.mkdir()
    labels.write_text('{"schema_version": 1, "photos": []}')
    photos = tmp_path / "data" / "photos"
    photos.mkdir()
    (photos / "01_kitchen.jpg").write_bytes(b"jpg")
    (photos / "02_bath.jpg").write_bytes(b"jpg2")

    class Settings:
        embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        cost_table_path = None
        vision_model = "gpt-6.1-sol"
        anthropic_effort = "low"
        openai_api_key = "sk-secret-not-recorded-XXXXXXXX"  # pragma: allowlist secret
        database_url = "postgresql://x"

        def model_dump(self) -> dict[str, Any]:
            return {
                k: v
                for k, v in vars(type(self)).items()
                if not k.startswith("_") and k != "model_dump"
            }

    args = argparse.Namespace(
        out=tmp_path / "run", photos=photos, repeats=3, split="dev", model=None
    )
    run = record.EvalRun.begin(
        args.out, harness="evals.record", args=args, settings=Settings(), log=False, cwd=repo
    )
    assert args._eval_record is run
    prompt = prompts.load("photo_countables", "v2")
    declared = prompts.load("photo_surfaces", "v1")
    run.prompt(declared)
    run.input("photos", photos)
    run.dataset(labels)
    run.note(scope="house")
    await _one_call(run, prompt)
    await _one_call(run, prompt)
    manifest = run.finish({"photos": 2, "spent_usd": 0.1}, exit_code=0)

    on_disk = json.loads((args.out / record.MANIFEST_FILE).read_text())
    assert on_disk == manifest
    assert manifest["format"] == record.FORMAT and manifest["kind"] == "eval_run"
    assert manifest["name"] == "run" and manifest["status"] == "ok"
    assert manifest["harness"]["module"] == "evals.record"
    assert manifest["harness"]["sha256"] == record.sha256_file(Path(record.__file__))
    assert manifest["harness"]["options"] == {
        "model": None,
        "out": str(args.out),
        "photos": str(photos),
        "repeats": 3,
        "split": "dev",
    }
    assert manifest["git"]["release"] == "v0.1.0" and manifest["git"]["dirty"] is False
    assert manifest["environment"]["pricing_version"] == PRICING_VERSION
    assert manifest["environment"]["rules_version"]
    assert manifest["components"]["embedder"]["model"].endswith("MiniLM-L6-v2")
    assert manifest["components"]["embedder"]["revision"]
    assert manifest["components"]["cost_table"] is None
    assert manifest["components"]["prompts"] == [
        {"name": "photo_countables", "version": "v2", "sha256": prompt.sha256, "calls": 2},
        {"name": "photo_surfaces", "version": "v1", "sha256": declared.sha256, "calls": 0},
    ]
    assert manifest["components"]["models"]["requested"] == ["gpt-4o-mini"]
    assert manifest["components"]["models"]["answered"] == ["scripted-model"]
    assert manifest["components"]["request_params"]["reasoning_effort"] == ["low"]
    assert manifest["settings"] == {
        "anthropic_effort": "low",
        "cost_table_path": None,
        "embedding_model": Settings.embedding_model,
        "vision_model": "gpt-6.1-sol",
    }
    assert "sk-secret" not in json.dumps(manifest)
    assert manifest["data"]["split"] == "dev" and manifest["data"]["repeats"] == 3
    assert manifest["data"]["data_dir"] == str(tmp_path / "data")
    [photos_input] = manifest["data"]["inputs"]
    assert photos_input["name"] == "photos" and photos_input["files"] == 2
    assert photos_input["sha256"] == record.sha256_tree(photos)["sha256"]
    [dataset] = manifest["data"]["datasets"]
    assert dataset == {
        "name": "photo_components",
        "file": "photo_components_v1.json",
        "version": "v1",
        "path": str(labels),
        "sha256": record.sha256_file(labels),
    }
    assert manifest["calls"]["count"] == 2 and manifest["calls"]["input_tokens"] == 2000
    assert manifest["calls"]["cost_usd"] == round(float(run.recorder.spent()), 6)
    assert manifest["files"] == {"log": None, "summary": "run.json", "ledger": "llm_calls.jsonl"}
    assert manifest["notes"] == {"scope": "house"}
    assert manifest["summary"] == {"photos": 2, "spent_usd": 0.1}
    assert json.loads((args.out / "run.json").read_text()) == {"photos": 2, "spent_usd": 0.1}


def test_sha256_tree_is_order_independent_and_skips_dotfiles(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    (a / "x.txt").write_text("x")
    (a / "y.txt").write_text("y")
    (b / "y.txt").write_text("y")
    (b / "x.txt").write_text("x")
    (b / ".DS_Store").write_text("junk")
    assert record.sha256_tree(a) == record.sha256_tree(b)
    assert record.sha256_tree(a)["files"] == 2
    (b / "x.txt").write_text("changed")
    assert record.sha256_tree(a)["sha256"] != record.sha256_tree(b)["sha256"]


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("photo_components_v1.json", "v1"),
        ("room_inventory_v12.json", "v12"),
        ("room_inventory_v1.json.bak-2026-10-16", None),
        ("appraisal.pdf", None),
        ("countable_boxes_v1_part1.json", None),
    ],
)
def test_label_version_reads_the_suffix_only(name: str, version: str | None) -> None:
    assert record.label_version(name) == version


# --------------------------------------------------------------------------- finishing


async def test_the_account_stop_decorator_finishes_a_pending_run_as_stopped(tmp_path: Path) -> None:
    error = ProviderAccountError(
        "insufficient_quota", provider="openai", status=429, code="insufficient_quota"
    )

    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        rec = record.EvalRun.begin(args.out, harness="evals.record", args=args, log=False)
        budget = Budget(remaining_usd=1.0)
        account_stop.track(args, budget)
        await _one_call(rec, prompts.latest("photo_countables"))
        raise error

    args = argparse.Namespace(out=tmp_path / "out")
    assert await run(args) == account_stop.ACCOUNT_STOPPED
    manifest = json.loads((args.out / record.MANIFEST_FILE).read_text())
    assert manifest["status"] == "stopped"
    assert manifest["exit_code"] == account_stop.ACCOUNT_STOPPED
    assert "insufficient_quota" in manifest["error"]
    assert manifest["calls"]["count"] == 1  # the call that was made is in the ledger
    assert manifest["summary"]["complete"] is False  # the decorator's run.json is kept
    assert "stopped" in manifest["summary"]


async def test_the_account_stop_decorator_finishes_a_pending_run_as_error_and_reraises(
    tmp_path: Path,
) -> None:
    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        record.EvalRun.begin(args.out, harness="evals.record", args=args, log=False)
        raise RuntimeError("boom")

    args = argparse.Namespace(out=tmp_path / "out")
    with pytest.raises(RuntimeError):
        await run(args)
    manifest = json.loads((args.out / record.MANIFEST_FILE).read_text())
    assert manifest["status"] == "error" and manifest["exit_code"] == 1
    assert "RuntimeError: boom" in manifest["error"]


async def test_a_normal_exit_without_finish_still_leaves_a_manifest(tmp_path: Path) -> None:
    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        record.EvalRun.begin(args.out, harness="evals.record", args=args, log=False)
        (args.out / "run.json").write_text('{"photos": 1}')
        return 2

    args = argparse.Namespace(out=tmp_path / "out")
    assert await run(args) == 2
    manifest = json.loads((args.out / record.MANIFEST_FILE).read_text())
    assert (manifest["status"], manifest["exit_code"]) == ("ok", 2)
    assert manifest["summary"] == {"photos": 1}


async def test_finish_is_idempotent_and_a_second_begin_releases_a_leaked_log(
    tmp_path: Path,
) -> None:
    before = (sys.stdout, sys.stderr)
    leaked = record.EvalRun.begin(tmp_path / "one", harness="evals.record")
    second = record.EvalRun.begin(tmp_path / "two", harness="evals.record")
    print("only in two")
    second.finish({})
    assert (sys.stdout, sys.stderr) == before
    assert "only in two" not in (tmp_path / "one" / record.LOG_FILE).read_text()
    assert "only in two" in (tmp_path / "two" / record.LOG_FILE).read_text()
    assert not (tmp_path / "one" / record.MANIFEST_FILE).exists()
    assert leaked.finished is False
    record.finish_pending(argparse.Namespace(), exit_code=0, error=None)  # nothing attached: no-op


# --------------------------------------------------------------------------- scores


def test_score_entry_names_the_label_files_their_versions_and_every_metric(tmp_path: Path) -> None:
    from evals.run import MetricRow, TaskResult

    data = tmp_path / "data"
    data.mkdir()
    (data / "photo_components_v1.json").write_text("{}")
    results = [
        TaskResult(
            "photo_components",
            "predictions",
            rows=[MetricRow("f1", 0.5, 10, float("nan"), (0.2, 0.8))],
            notes=["a note"],
        ),
        TaskResult("photo_counts", "predictions", skipped=True, notes=["no file"]),
    ]
    entry = record.score_entry(
        results,
        harness="evals.run",
        data_dir=data,
        label_files={"photo_components": "photo_components_v1.json"},
        split="dev",
        repeats=3,
        options={"predictions": tmp_path / "run", "inventory_file": "room_inventory.json"},
    )
    assert entry["kind"] == "scores" and entry["format"] == record.FORMAT
    assert entry["data"]["labels"] == {
        "photo_components": {
            "file": "photo_components_v1.json",
            "version": "v1",
            "sha256": record.sha256_file(data / "photo_components_v1.json"),
        },
        "photo_counts": {"file": None, "version": None, "sha256": None},
    }
    assert entry["results"]["photo_components"]["metrics"]["f1"] == {
        "value": 0.5,
        "n": 10,
        "stderr": None,
        "interval": [0.2, 0.8],
    }
    assert entry["results"]["photo_counts"]["skipped"] is True
    assert entry["options"]["predictions"] == str(tmp_path / "run")
    assert entry["git"]["sha"]
    (tmp_path / "run").mkdir()
    path = record.append_scores(tmp_path / "run", entry)
    record.append_scores(tmp_path / "run", entry)
    assert path.name == record.SCORES_FILE
    assert len(record.read_scores(tmp_path / "run")) == 2


async def test_a_rerun_into_the_same_directory_describes_the_whole_ledger(tmp_path: Path) -> None:
    """``predict_marks --from-raw`` redoes a step over its own output directory: the ledger
    keeps the earlier rows, the manifest counts the file and says what this run added."""
    out = tmp_path / "run"
    first = record.EvalRun.begin(out, harness="evals.record", log=False)
    prompt = prompts.latest("photo_countables")
    await _one_call(first, prompt)
    await _one_call(first, prompt)
    first_manifest = first.finish({"photos": 2})
    second = record.EvalRun.begin(out, harness="evals.record", log=False)
    await _one_call(second, prompts.latest("photo_surfaces"))
    manifest = second.finish({"photos": 2, "from_raw": True})
    assert len(record.read_ledger(out)) == 3
    assert manifest["calls"]["count"] == 3
    assert manifest["calls"]["this_run"] == {
        "count": 1,
        "cost_usd": round(float(second.recorder.spent()), 6),
        "earlier_rows": 2,
    }
    assert [(p["name"], p["calls"]) for p in manifest["components"]["prompts"]] == [
        ("photo_countables", 2),
        ("photo_surfaces", 1),
    ]
    assert manifest["notes"]["previous_manifest"]["id"] == first_manifest["id"]
    assert manifest["notes"]["previous_manifest"]["status"] == "ok"
    assert "this_run" not in first_manifest["calls"]
