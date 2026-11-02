"""``GET /v1/ops/eval-runs``, ``.../{run_id}`` and ``.../{run_id}/files/{name}``
(docs/api/EVAL_RUNS.md) on the real app, with no database: operators only (a firm owner is
403 ``not-operator``), an explicit 404 ``eval-data-unavailable`` on a host without data, the
list newest first with every record field and every unknown null, filters and keyset paging
with their bounds, a malformed manifest listed with its error, the four record files served
redacted and bounded, no local path, host name or key anywhere, the scan cached, the
per-operator rate limit. Synthetic run directories only: one recorded by the real
:class:`evals.record.EvalRun` with a scripted model, the others written by hand (a
backfilled manifest, an appraisal run, a run inside the verification tree, two broken ones).
"""

from __future__ import annotations

import json
import socket
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, FakeVerifier, bearer
from fastapi import FastAPI

from basis.api.app import create_app
from basis.api.auth import get_token_verifier
from basis.api.errors import PROBLEM_JSON
from basis.api.operators import DETAIL_NOT_OPERATOR
from basis.api.routes import eval_runs as route
from basis.config import Settings
from basis.errors.codes import E
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage, prompts
from basis.llm.call import CallContext, call_model
from basis.llm.provider import ChatRequest, ImagePart, Message
from basis.ops import eval_runs
from evals import record

OPERATOR = "ops-admin"
OWNER = "firm-owner"
LIST = "/v1/ops/eval-runs"
KEY = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # pragma: allowlist secret (a placeholder)
SHA = "a" * 64


def _manifest(name: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": 1,
        "kind": "eval_run",
        "id": f"id-{name}",
        "name": name,
        "backfilled": False,
        "harness": {"module": "evals.predict_photos", "sha256": SHA, "command": [], "options": {}},
        "started_at": "2026-10-20T10:00:00+00:00",
        "finished_at": "2026-10-20T10:12:00+00:00",
        "wall_seconds": 720.0,
        "status": "ok",
        "exit_code": 0,
        "git": {"sha": "b" * 40, "short": "bbbbbbb", "dirty": False, "release": "v0.9.0"},
        "components": {
            "prompts": [{"name": "photo_countables", "version": "v3", "sha256": SHA, "calls": 4}],
            "models": {"requested": ["gpt-6.1-sol"], "answered": ["gpt-6.1-sol"]},
            "request_params": {"reasoning_effort": ["low"]},
        },
        "data": {"inputs": [], "datasets": [], "labels": {}, "split": "dev", "repeats": 3},
        "calls": {"count": 4, "cost_usd": 0.5},
        "files": {"log": None, "summary": "run.json", "ledger": None, "report": "report.md"},
        "tree": {"component": "photo_countables", "version": "v3", "link": None},
        "results": {"passed": 3, "failed": 1, "test_error": 0},
    }
    base.update(extra)
    return base


async def _recorded_run(root: Path, settings: Settings) -> Path:
    """A run the real helper recorded: ledger, payloads, console log, results, manifest, the
    link in the verification tree, then a scoring."""
    out = root / "predictions" / "countables_v3_mini"
    labels = root / "photo_components_v1.json"
    labels.write_text("[]")
    run = record.EvalRun.begin(
        out,
        harness="evals.predict_photos",
        settings=settings,
        tree=root / record.TREE_DIR,
        args={"split": "dev", "repeats": 2, "photos": str(root / "inputs" / "photos")},
    )
    try:
        print(f"photos from {root}/inputs/photos with key {KEY}")
        run.dataset(labels)
        prompt = prompts.latest("photo_countables")
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
        request = ChatRequest(
            model="gpt-4o-mini",
            system=prompt.text,
            messages=(
                Message(
                    role="user", content="count", images=(ImagePart(data_base64="QUJD", ref="p1"),)
                ),
            ),
            prompt_version=prompt.version_id,
            reasoning_effort="low",
            study_id=str(ctx.study_id),
            activity="evals.test",
        )
        await call_model(provider, request, ctx)
        run.case("p1#1", "passed", kind="photo")
        run.case("p2#1", "failed", kind="photo")
    finally:
        run.finish({"photos": 2, "spent_usd": float(run.recorder.spent())})
    # A hand-appended ledger row with a credential in it: the download must mask it.
    with (out / record.LEDGER_FILE).open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "id": "manual",
                    "kind": "chat",
                    "model": "gpt-4o-mini",
                    "error": f"HTTP 401 from {root}/x Bearer {KEY}",
                    "request_params": {"api_key": KEY},
                }
            )
            + "\n"
        )
    record.append_scores(
        out,
        {
            "format": 1,
            "kind": "scores",
            "scored_at": "2026-10-21T11:00:00+00:00",
            "data": {
                "labels": {
                    "photo_components": {
                        "file": "photo_components_v1.json",
                        "version": "v1",
                        "sha256": SHA,
                        "path": str(labels),
                    }
                },
                "split": "dev",
                "repeats": 2,
            },
            "results": {
                "photo_components": {
                    "system": "predictions",
                    "skipped": False,
                    "metrics": {"f1": {"value": 0.71, "n": 2}, "precision": {"value": 0.8, "n": 2}},
                }
            },
        },
    )
    return out


def _hand_written_runs(root: Path) -> None:
    predictions = root / "predictions"
    # A backfilled manifest: unknowns, an absolute label path, an estimated duration, eval.md.
    b = predictions / "countables_v2_sol"
    b.mkdir(parents=True)
    (b / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "countables_v2_sol",
                backfilled=True,
                started_at="2026-10-16T03:00:00-07:00",
                finished_at="2026-10-29T03:00:00-07:00",
                wall_seconds=1123200.0,
                git={
                    "sha": "unknown",
                    "short": "unknown",
                    "dirty": "unknown",
                    "release": "unknown",
                },
                components={
                    "prompts": [
                        {
                            "name": "photo_countables",
                            "version": "v2",
                            "sha256": "unknown",
                            "calls": "unknown",
                        }
                    ],
                    "models": {"requested": ["gpt-6.1-sol"], "answered": ["gpt-6.1-sol"]},
                },
                data={
                    "data_dir": str(root),
                    "inputs": [],
                    "datasets": [],
                    "labels": {
                        "photo_components": {
                            "file": "photo_components_v1.json",
                            "version": "v1",
                            "sha256": "unknown",
                            "path": str(root / "photo_components_v1.json"),
                        }
                    },
                    "split": None,
                    "repeats": None,
                },
                calls={"count": "unknown", "cost_usd": 1.1},
                files={"log": None, "summary": "run.json", "ledger": None, "report": "eval.md"},
                tree={
                    "component": "photo_countables",
                    "version": "v2",
                    "link": str(root / "verification" / "x"),
                },
                sources={"git": "not recorded", "started_at": f"file birth time under {root}"},
                report_sections=[11, 15],
                environment={"hostname": socket.gethostname(), "basis_version": "unknown"},
                settings={},
            )
        )
    )
    (b / "run.json").write_text(
        json.dumps({"photos": 52, "spent_usd": 1.1, "prompt": "photo_countables/v2"})
    )
    (b / "eval.md").write_text(
        "# Basis evaluation report\n\n## photo_components (system: `predictions`)\n\n"
        "| metric | value | n | 95% interval | SE |\n|---|---|---|---|---|\n"
        "| f1 | 0.6500 | 120 | [0.6, 0.7] | 0.0200 |\n"
    )
    # A stopped run: run.json only, no prompt, no model.
    s = predictions / "stopped"
    s.mkdir()
    (s / "run.json").write_text(json.dumps({"complete": False, "spent_usd": 0.01}))
    # A manifest that is valid JSON but not a run manifest (components is a string).
    t = predictions / "broken_types"
    t.mkdir()
    (t / "manifest.json").write_text(json.dumps(_manifest("broken_types", components="nope")))
    (t / "console.log").write_text(f"ran in {root}\n")
    # A manifest that is not JSON.
    j = predictions / "broken_json"
    j.mkdir()
    (j / "manifest.json").write_text("{not json")
    (j / "run.json").write_text(json.dumps({"spent_usd": 0.2, "model": "gpt-6-luna"}))
    # An appraisal run with non-finite scores.
    a = root / "appraisal" / "runs" / "sweep" / "llm_all@gpt-6-luna"
    a.mkdir(parents=True)
    (a / "run.json").write_text(
        json.dumps(
            {
                "models": ["gpt-6-luna"],
                "prompt_versions": ["appraisal_fields_grounded/v2"],
                "cost_usd": 0.0,
            }
        )
    )
    (a / "score.json").write_text(
        '{"field_accuracy": 0.91, "by_tier": {"critical": NaN, "other": 1.0}}'
    )
    (a / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "llm_all@gpt-6-luna",
                harness={
                    "module": "evals.appraisal.run",
                    "sha256": SHA,
                    "command": [],
                    "options": {},
                },
                started_at="2026-10-28T01:00:00+00:00",
                components={
                    "prompts": [
                        {
                            "name": "appraisal_fields_grounded",
                            "version": "v2",
                            "sha256": SHA,
                            "calls": 10,
                        }
                    ],
                    "models": {"requested": ["gpt-6-luna"], "answered": ["gpt-6-luna"]},
                },
                git={"sha": "c" * 40, "short": "ccccccc", "dirty": True, "release": "v0.8.1"},
                tree={"component": "appraisal_fields_grounded", "version": "v2", "link": None},
                results={},
            )
        )
    )
    # A run written inside the verification tree: the pipeline's room step, current.
    r = root / record.TREE_DIR / "room_inventory" / "v3" / "rooms_v3_luna"
    r.mkdir(parents=True)
    (r / "manifest.json").write_text(
        json.dumps(
            _manifest(
                "rooms_v3_luna",
                harness={
                    "module": "evals.predict_rooms",
                    "sha256": SHA,
                    "command": [],
                    "options": {},
                },
                started_at="2026-10-30T09:00:00+00:00",
                components={
                    "prompts": [
                        {"name": "room_inventory", "version": "v3", "sha256": SHA, "calls": 6}
                    ],
                    "models": {"requested": ["gpt-6-luna"], "answered": ["gpt-6-luna"]},
                    "request_params": {"reasoning_effort": ["low"]},
                },
                calls={
                    "count": 6,
                    "cost_usd": 0.3,
                    "input_tokens": 6000,
                    "output_tokens": 600,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "reasoning_output_tokens": 120,
                },
                tree={"component": "room_inventory", "version": "v3", "link": None},
                results={"passed": 6, "failed": 0, "test_error": 0},
            )
        )
    )


@pytest.fixture
async def root(tmp_path: Path, settings: Settings) -> Path:
    data = tmp_path / "eval-data"
    data.mkdir()
    await _recorded_run(data, settings)
    _hand_written_runs(data)
    return data


def app_settings(settings: Settings, root: Path | None, **overrides: Any) -> Settings:
    return settings.model_copy(
        update={
            "ops_operator_uids": (OPERATOR,),
            "eval_data_dir": root,
            # A decomposed pipeline with a room step, so the synthetic runs have a standing.
            "vision_photo_mode": "decomposed",
            "vision_model": "gpt-6.1-sol",
            "vision_surfaces_model": "gpt-6-luna",
            "room_reconcile": True,
            "room_model": "gpt-6-luna",
            **overrides,
        }
    )


def make_app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    verifier = FakeVerifier()
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    return app


@pytest.fixture
def app(settings: Settings, root: Path) -> FastAPI:
    return make_app(app_settings(settings, root))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        yield c


async def get(
    client: httpx.AsyncClient, path: str, uid: str = OPERATOR, **params: Any
) -> httpx.Response:
    return await client.get(path, headers=bearer(uid), params=params or None)


async def listed(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await get(client, LIST, **params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- access


async def test_no_token_401_and_a_firm_owner_403_without_diagnostics(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get(LIST)).status_code == 401
    for path in (
        LIST,
        f"{LIST}/{eval_runs.run_id('predictions/stopped')}",
        f"{LIST}/x/files/manifest",
    ):
        response = await get(client, path, uid=OWNER)
        assert response.status_code == 403, path
        assert response.headers["content-type"] == PROBLEM_JSON
        body = response.json()
        assert body["detail"] == DETAIL_NOT_OPERATOR and body["code"] == E.API_NOT_OPERATOR
        assert "items" not in body and "predictions" not in response.text


async def test_an_empty_operator_list_refuses_everyone(settings: Settings, root: Path) -> None:
    app = make_app(app_settings(settings, root, ops_operator_uids=()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        assert (await get(c, LIST)).status_code == 403


# --------------------------------------------------------------------------- no data


@pytest.mark.parametrize("where", ["unset", "missing"])
async def test_a_host_without_evaluation_data_answers_an_explicit_404(
    settings: Settings, tmp_path: Path, where: str
) -> None:
    root = None if where == "unset" else tmp_path / "nowhere"
    app = make_app(app_settings(settings, root))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        for path in (
            LIST,
            f"{LIST}/{eval_runs.run_id('predictions/x')}",
            f"{LIST}/{eval_runs.run_id('predictions/x')}/files/manifest",
        ):
            response = await get(c, path)
            assert response.status_code == 404, path
            body = response.json()
            assert body["code"] == E.API_EVAL_DATA_UNAVAILABLE
            assert body["type"].endswith("/eval-data-unavailable")
            assert "items" not in body
            assert ("is not set" if where == "unset" else "does not exist") in body["detail"]


async def test_without_the_evaluation_tooling_the_answer_says_so(
    settings: Settings, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eval_runs, "TOOLING_INSTALLED", False)
    app = make_app(app_settings(settings, root))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        response = await get(c, LIST)
    assert response.status_code == 404
    assert response.json()["code"] == E.API_EVAL_DATA_UNAVAILABLE
    assert "not installed" in response.json()["detail"]


# --------------------------------------------------------------------------- the list


async def test_the_list_is_newest_first_with_every_record_field(
    client: httpx.AsyncClient, root: Path
) -> None:
    page = await listed(client)
    assert page["next_cursor"] is None and page["total"] == 7
    names = [item["name"] for item in page["items"]]
    # The recorded run started now; the hand-written ones carry their dates; no date last.
    assert names[0] == "countables_v3_mini"
    assert (
        names[1:]
        == [
            "rooms_v3_luna",
            "llm_all@gpt-6-luna",
            "countables_v2_sol",
            "broken_types",  # the written date of its (unreadable) manifest is not read: no date
            "stopped",
            "broken_json",
        ]
        or names[1:4] == ["rooms_v3_luna", "llm_all@gpt-6-luna", "countables_v2_sol"]
    )
    by_name = {item["name"]: item for item in page["items"]}

    recorded = by_name["countables_v3_mini"]
    assert recorded["run_id"] == eval_runs.run_id("predictions/countables_v3_mini")
    assert (
        recorded["path"] == "predictions/countables_v3_mini" and recorded["kind"] == "predictions"
    )
    assert (recorded["component"], recorded["version"]) == ("photo_countables", "v3")
    assert recorded["harness"] == "evals.predict_photos"
    assert recorded["status"] == "ok" and recorded["results"] == {
        "passed": 1,
        "failed": 1,
        "test_error": 0,
    }
    assert recorded["git"]["sha"] and len(recorded["git"]["sha"]) == 40
    assert isinstance(recorded["git"]["dirty"], bool)
    prompt = prompts.latest("photo_countables")
    assert recorded["prompts"] == [
        {"name": "photo_countables", "version": prompt.version, "sha256": prompt.sha256, "calls": 1}
    ]
    assert "photo_countables/v3" in recorded["components"]
    assert recorded["models_requested"] == ["gpt-4o-mini"] and recorded["reasoning_effort"] == [
        "low"
    ]
    assert recorded["labels"] == {
        "photo_components": {
            "file": "photo_components_v1.json",
            "version": "v1",
            "sha256": SHA,
            "scored_at": "2026-10-21T11:00:00+00:00",
        }
    }
    assert (recorded["split"], recorded["repeats"]) == ("dev", 2)
    assert recorded["scores"] == {"photo_components": {"f1": 0.71, "precision": 0.8}}
    assert recorded["headline"] == [{"dataset": "photo_components", "metric": "f1", "value": 0.71}]
    spend = recorded["spend"]
    assert (
        spend["cost_source"] == "ledger" and spend["cost_usd"] is not None and spend["calls"] == 1
    )
    assert spend["tokens"] == {
        "input": 1000,
        "output": 20,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
    }
    assert spend["duration_source"] == "recorded" and 0 <= spend["duration_seconds"] < 60
    assert recorded["record"] == {
        "manifest": True,
        "backfilled": False,
        "format": record.FORMAT,
        "error": None,
    }
    # v3 is the pipeline's version; gpt-4o-mini is not its VISION_MODEL or fusion model.
    assert recorded["selection"]["status"] == "alternative"
    assert any("gpt-6.1-sol (VISION_MODEL)" in r for r in recorded["selection"]["reasons"])
    assert recorded["report_sections"] == []
    assert recorded["files"] == {
        name: f"{LIST}/{recorded['run_id']}/files/{name}"
        for name in ("manifest", "report", "ledger", "console")
    }

    backfilled = by_name["countables_v2_sol"]
    assert backfilled["record"]["backfilled"] is True
    assert backfilled["git"] == {"sha": None, "dirty": None, "release": None}
    assert backfilled["prompts"] == [
        {"name": "photo_countables", "version": "v2", "sha256": None, "calls": None}
    ]
    assert backfilled["labels"]["photo_components"] == {
        "file": "photo_components_v1.json",
        "version": "v1",
        "sha256": None,
        "scored_at": None,
    }
    assert (
        backfilled["spend"]["cost_source"] == "summary" and backfilled["spend"]["cost_usd"] == 1.1
    )
    assert backfilled["spend"]["calls"] is None and backfilled["spend"]["tokens"] is None
    assert (
        backfilled["spend"]["duration_source"] == "file_times"
        and backfilled["spend"]["duration_seconds"] == 1123200.0
    )
    assert (backfilled["split"], backfilled["repeats"]) == (None, None)
    assert backfilled["scores"] == {"photo_components": {"f1": 0.65}}
    assert backfilled["selection"]["status"] == "alternative"
    assert any("the pipeline runs v3" in r for r in backfilled["selection"]["reasons"])
    assert backfilled["report_sections"] == [11, 15]
    assert backfilled["files"]["report"] is not None and backfilled["files"]["ledger"] is None

    stopped = by_name["stopped"]
    assert stopped["status"] == "stopped" and stopped["record"]["manifest"] is False
    assert (
        stopped["component"] is None
        and stopped["prompts"] == []
        and stopped["selection"]["status"] == "unknown"
    )
    assert stopped["spend"] == {
        "cost_usd": 0.01,
        "cost_source": "summary",
        "calls": None,
        "tokens": None,
        "duration_seconds": None,
        "duration_source": None,
    }
    assert stopped["files"] == {"manifest": None, "report": None, "ledger": None, "console": None}

    broken = by_name["broken_types"]
    assert broken["record"]["manifest"] is True and "AttributeError" in broken["record"]["error"]
    assert (
        broken["component"] is None
        and broken["status"] is None
        and broken["spend"]["cost_usd"] is None
    )
    assert broken["files"]["console"] is not None and broken["files"]["manifest"] is not None
    syntax = by_name["broken_json"]
    assert syntax["record"]["manifest"] is False and "not valid JSON" in syntax["record"]["error"]
    assert syntax["models_requested"] == ["gpt-6-luna"]

    appraisal = by_name["llm_all@gpt-6-luna"]
    assert (
        appraisal["kind"] == "appraisal"
        and appraisal["path"] == "appraisal/runs/sweep/llm_all@gpt-6-luna"
    )
    assert appraisal["scores"] == {
        "appraisal": {"field_accuracy": 0.91, "by_tier.critical": None, "by_tier.other": 1.0}
    }
    assert appraisal["git"]["dirty"] is True and appraisal["results"] is None
    assert (
        appraisal["selection"]["status"] == "not_in_pipeline"
    )  # the test pipeline reads with the Azure DI pipeline

    rooms = by_name["rooms_v3_luna"]
    assert rooms["path"] == "verification/room_inventory/v3/rooms_v3_luna"
    assert rooms["selection"]["status"] == "current"
    assert rooms["spend"]["tokens"] == {
        "input": 6000,
        "output": 600,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 120,
    }
    # The tree link of the recorded run is not a second run.
    assert sum(1 for item in page["items"] if item["name"] == "countables_v3_mini") == 1


async def test_nothing_private_leaks_from_the_list_the_details_or_the_files(
    client: httpx.AsyncClient, root: Path
) -> None:
    page = await listed(client)
    texts = [json.dumps(page)]
    for item in page["items"]:
        detail = await get(client, f"{LIST}/{item['run_id']}")
        assert detail.status_code == 200, item["name"]
        texts.append(detail.text)
        for name, url in item["files"].items():
            if url is None:
                continue
            response = await get(client, url)
            assert response.status_code == 200, (item["name"], name)
            texts.append(response.text)
    everything = "\n".join(texts)
    for forbidden in (
        str(root),
        str(root.resolve()),
        str(Path.home()),
        "/Users/",
        "/home/",
        KEY,
        socket.gethostname(),
    ):
        assert forbidden not in everything, forbidden


# --------------------------------------------------------------------------- filters


async def test_filters_match_as_the_registry_does(client: httpx.AsyncClient) -> None:
    names = lambda page: sorted(i["name"] for i in page["items"])  # noqa: E731
    assert names(await listed(client, component="photo_countables")) == [
        "countables_v2_sol",
        "countables_v3_mini",
    ]
    assert names(await listed(client, component="photo_countables/v2")) == ["countables_v2_sol"]
    assert names(await listed(client, component="room_inventory/v3")) == ["rooms_v3_luna"]
    assert names(
        await listed(client, component=["room_inventory", "appraisal_fields_grounded"])
    ) == ["llm_all@gpt-6-luna", "rooms_v3_luna"]
    assert names(await listed(client, model="gpt-6*")) == [
        "broken_json",
        "countables_v2_sol",
        "llm_all@gpt-6-luna",
        "rooms_v3_luna",
    ]
    assert names(await listed(client, model="gpt-4o-mini")) == ["countables_v3_mini"]
    assert names(await listed(client, labels="photo_components_v1")) == [
        "countables_v2_sol",
        "countables_v3_mini",
    ]
    assert names(await listed(client, dataset="appraisal")) == ["llm_all@gpt-6-luna"]
    assert names(await listed(client, split="dev")) == [
        "countables_v3_mini",
        "llm_all@gpt-6-luna",
        "rooms_v3_luna",
    ]
    assert names(await listed(client, status="stopped")) == ["stopped"]
    assert names(await listed(client, status="unknown")) == ["broken_json"]
    assert names(await listed(client, release="v0.8.1")) == ["llm_all@gpt-6-luna"]
    assert names(
        await listed(
            client, component="photo_countables", model="gpt-6.1-sol", labels="photo_components_v1"
        )
    ) == ["countables_v2_sol"]
    empty = await listed(client, component="nothing")
    assert empty == {"items": [], "next_cursor": None, "total": 0}
    assert (await get(client, LIST, status="weird")).status_code == 422
    assert (await get(client, LIST, model="")).status_code == 422
    assert (await get(client, LIST, component="x" * 201)).status_code == 422


# --------------------------------------------------------------------------- paging


async def test_pages_walk_every_run_once_with_bounded_limits(client: httpx.AsyncClient) -> None:
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = await listed(client, limit=3, **({"cursor": cursor} if cursor else {}))
        pages += 1
        assert page["total"] == 7 and len(page["items"]) <= 3
        seen += [i["name"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 7 and len(set(seen)) == 7
    assert seen == [i["name"] for i in (await listed(client))["items"]]
    last = (await listed(client, limit=7))["items"][-1]
    assert await listed(client, cursor=eval_runs.encode_cursor(_key(last))) == {
        "items": [],
        "next_cursor": None,
        "total": 7,
    }
    for params in (
        {"limit": 0},
        {"limit": 201},
        {"limit": "x"},
        {"cursor": "not-a-cursor!"},
        {"cursor": "eyJhIjogMX0"},
    ):
        response = await get(client, LIST, **params)
        assert response.status_code == 422, params
    assert (await get(client, LIST, cursor="eyJhIjogMX0")).json()["code"] == E.API_INVALID_CURSOR


def _key(item: dict[str, Any]) -> eval_runs.RunRecord:
    return eval_runs.RunRecord(
        run_id=item["run_id"],
        name=item["name"],
        path=item["path"],
        kind=item["kind"],
        directory=Path("."),
        entry=None,
        started_at=item["started_at"],
    )


# --------------------------------------------------------------------------- detail


async def test_the_detail_adds_settings_environment_options_and_sources(
    client: httpx.AsyncClient, root: Path
) -> None:
    page = await listed(client)
    by_name = {item["name"]: item for item in page["items"]}
    recorded = (await get(client, f"{LIST}/{by_name['countables_v3_mini']['run_id']}")).json()
    assert (
        recorded["settings"]["vision_model"] == "gpt-4o-mini"
    )  # the test settings the run recorded
    assert "hostname" not in recorded["environment"] and recorded["environment"]["basis_version"]
    assert recorded["options"] == {
        "split": "dev",
        "repeats": 2,
        "photos": "<data dir>/inputs/photos",
    }
    assert recorded["sources"] is None and recorded["error"] is None
    assert recorded["notes"] is None or isinstance(recorded["notes"], dict)
    assert {k: v for k, v in recorded.items() if k in by_name["countables_v3_mini"]} == by_name[
        "countables_v3_mini"
    ]
    backfilled = (await get(client, f"{LIST}/{by_name['countables_v2_sol']['run_id']}")).json()
    assert backfilled["settings"] is None
    assert backfilled["sources"] == {
        "git": "not recorded",
        "started_at": "file birth time under <data dir>",
    }
    assert backfilled["environment"] == {
        "basis_version": None
    }  # unknown -> null; the host name withheld
    broken = (await get(client, f"{LIST}/{by_name['broken_types']['run_id']}")).json()
    assert broken["settings"] is None and "AttributeError" in broken["record"]["error"]


@pytest.mark.parametrize(
    "run_id",
    [
        eval_runs.run_id("predictions/nothing-here"),
        eval_runs.run_id("../etc/passwd"),
        eval_runs.run_id("/etc/passwd"),
        eval_runs.run_id("predictions/../../x"),
        "AA",  # decodes to an empty path
        "not*base64",
        eval_runs.run_id("predictions"),  # a directory, not a run
        eval_runs.run_id(
            "verification/room_inventory/v3/countables_v3_mini"
        ),  # the tree link, scanned as predictions/
    ],
)
async def test_an_id_this_host_did_not_issue_is_404(client: httpx.AsyncClient, run_id: str) -> None:
    for path in (f"{LIST}/{run_id}", f"{LIST}/{run_id}/files/manifest"):
        response = await get(client, path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == route.DETAIL_NOT_FOUND


# --------------------------------------------------------------------------- files


async def test_the_record_files_are_served_redacted_as_attachments(
    client: httpx.AsyncClient, root: Path
) -> None:
    by_name = {item["name"]: item for item in (await listed(client))["items"]}
    files = by_name["countables_v3_mini"]["files"]
    manifest = await get(client, files["manifest"])
    assert manifest.status_code == 200
    assert manifest.headers["content-type"] == "application/json"
    assert (
        manifest.headers["content-disposition"]
        == 'attachment; filename="countables_v3_mini.manifest.json"'
    )
    assert manifest.headers["cache-control"] == "no-store"
    document = manifest.json()
    assert document["name"] == "countables_v3_mini" and document["environment"]["hostname"] == "***"
    assert document["harness"]["options"]["photos"] == "<data dir>/inputs/photos"
    assert all(
        "/Users/" not in part and "/home/" not in part for part in document["harness"]["command"]
    )
    ledger = await get(client, files["ledger"])
    assert ledger.headers["content-type"] == "application/x-ndjson"
    rows = [json.loads(line) for line in ledger.text.splitlines()]
    assert rows[0]["prompt_version"] == prompts.latest("photo_countables").version_id
    assert rows[0]["request_uri"].startswith("llm/") and rows[0]["cost_usd"] is not None
    manual = rows[-1]
    assert manual["request_params"]["api_key"] == "***"
    assert manual["error"] == "HTTP 401 from <data dir>/x Bearer [redacted]"
    console = await get(client, files["console"])
    assert console.headers["content-type"].startswith("text/plain")
    assert "photos from <data dir>/inputs/photos with key [redacted]" in console.text
    assert "finished" in console.text
    report = await get(client, files["report"])
    assert report.headers["content-type"].startswith("text/markdown")
    assert report.text.startswith("# countables_v3_mini") and "passed" in report.text
    # The payload files are never served, whatever the name.
    for name in ("llm", "payloads", "manifest.json", "console.log", "..%2Fmanifest"):
        response = await get(
            client, f"{LIST}/{by_name['countables_v3_mini']['run_id']}/files/{name}"
        )
        assert response.status_code in (404, 422), name
    # A backfilled run's report is its eval.md; its missing files are 404.
    eval_md = await get(client, by_name["countables_v2_sol"]["files"]["report"])
    assert eval_md.status_code == 200 and eval_md.text.startswith("# Basis evaluation report")
    assert (
        eval_md.headers["content-disposition"]
        == 'attachment; filename="countables_v2_sol.report.md"'
    )
    for name in ("ledger", "console"):
        response = await get(
            client, f"{LIST}/{by_name['countables_v2_sol']['run_id']}/files/{name}"
        )
        assert (
            response.status_code == 404 and response.json()["detail"] == route.DETAIL_FILE_NOT_FOUND
        )
    # The stopped run has no manifest at all.
    assert (
        await get(client, f"{LIST}/{by_name['stopped']['run_id']}/files/manifest")
    ).status_code == 404
    # The broken manifest is served as it is (it is the record of the problem), redacted.
    broken = await get(client, f"{LIST}/{by_name['broken_json']['run_id']}/files/manifest")
    assert broken.status_code == 200 and broken.text == "{not json"
    console_broken = await get(client, f"{LIST}/{by_name['broken_types']['run_id']}/files/console")
    assert console_broken.text == "ran in <data dir>\n"


async def test_a_file_over_the_bound_is_413(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_name = {item["name"]: item for item in (await listed(client))["items"]}
    monkeypatch.setattr(eval_runs, "MAX_FILE_BYTES", 100)
    response = await get(client, by_name["countables_v3_mini"]["files"]["console"])
    assert response.status_code == 413
    assert response.json()["code"] == E.API_EVAL_RUN_FILE_TOO_LARGE
    assert (await get(client, by_name["countables_v2_sol"]["files"]["report"])).status_code == 413


# --------------------------------------------------------------------------- cache, limits


async def test_the_scan_is_cached_per_process_and_refreshed_after_the_ttl(
    settings: Settings, root: Path
) -> None:
    now = [1000.0]
    app = make_app(app_settings(settings, root))
    setattr(
        app.state,
        route.CATALOGUE_STATE_ATTR,
        eval_runs.RunCatalogue(ttl_s=30.0, clock=lambda: now[0]),
    )
    catalogue: eval_runs.RunCatalogue = getattr(app.state, route.CATALOGUE_STATE_ATTR)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        first = await listed(c)
        second = await listed(c, component="photo_countables")
        assert catalogue.scans == 1 and first["total"] == 7 and second["total"] == 2
        new = root / "predictions" / "later"
        new.mkdir()
        (new / "manifest.json").write_text(
            json.dumps(_manifest("later", started_at="2026-11-01T00:00:00+00:00"))
        )
        assert (await listed(c))["total"] == 7  # within the TTL: the cached scan
        now[0] += 31
        page = await listed(c)
        assert page["total"] == 8 and page["items"][1]["name"] == "later" and catalogue.scans == 2


async def test_sixty_reads_a_minute_per_operator_then_429(
    settings: Settings, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route, "EVAL_RUNS_RATE_LIMIT_PER_MIN", 3)
    app = make_app(app_settings(settings, root))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        for _ in range(3):
            assert (await get(c, LIST)).status_code == 200
        refused = await get(c, LIST)
        assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1
        # Another operator has a budget of their own; a non-operator is refused before counting.
        assert (await get(c, LIST, uid=OWNER)).status_code == 403
    other = make_app(app_settings(settings, root, ops_operator_uids=(OPERATOR, "second")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url=BASE_URL) as c:
        for _ in range(3):
            assert (await get(c, LIST)).status_code == 200
        assert (await get(c, LIST)).status_code == 429
        assert (await get(c, LIST, uid="second")).status_code == 200


async def test_the_routes_are_in_the_openapi_document_with_their_problems(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    for path, statuses in {
        "/v1/ops/eval-runs": {"401", "403", "404", "422", "429", "503"},
        "/v1/ops/eval-runs/{run_id}": {"401", "403", "404", "422", "429", "503"},
        "/v1/ops/eval-runs/{run_id}/files/{name}": {
            "401",
            "403",
            "404",
            "413",
            "422",
            "429",
            "503",
        },
    }.items():
        responses = paths[path]["get"]["responses"]
        assert statuses <= set(responses), path
        for status in statuses:
            assert PROBLEM_JSON in responses[status]["content"], (path, status)
    assert paths["/v1/ops/eval-runs/{run_id}/files/{name}"]["get"]["parameters"][1]["schema"][
        "enum"
    ] == [
        "manifest",
        "report",
        "ledger",
        "console",
    ]
