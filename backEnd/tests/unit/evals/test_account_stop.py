"""``evals.account_stop``: how a runner stops at the first provider account error.

The runners' own tests (``test_predict_photos``, ``test_predict_rooms``,
``test_predict_text_steps``) drive it end to end; these cover the pieces.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from basis.llm import ScriptedProvider, ScriptedTurn
from basis.llm.errors import ProviderAccountError, ProviderConfigError, ProviderUnavailable
from basis.llm.provider import Budget, ChatRequest, Message
from evals import account_stop

pytestmark = pytest.mark.asyncio

ERROR = ProviderAccountError("quota", provider="openai", status=429, code="insufficient_quota")


def request() -> ChatRequest:
    return ChatRequest(
        model="gpt-4o-mini",
        system="s",
        messages=(Message(role="user", content="hi"),),
        prompt_version="p/v1",
        study_id="s",
        activity="evals",
    )


def test_find_account_error_looks_through_causes_and_groups() -> None:
    assert account_stop.find_account_error(ERROR) is ERROR
    try:
        try:
            raise ERROR
        except ProviderAccountError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as wrapped:
        assert account_stop.find_account_error(wrapped) is ERROR
    group = ExceptionGroup("tasks", [ValueError("x"), ExceptionGroup("inner", [ERROR])])
    assert account_stop.find_account_error(group) is ERROR
    assert account_stop.find_account_error(ProviderUnavailable("503")) is None
    assert account_stop.find_account_error(None) is None


async def test_the_guard_refuses_later_calls_without_making_them() -> None:
    inner = ScriptedProvider([ScriptedTurn(raise_error=ERROR), ScriptedTurn(text="never")])
    guarded = account_stop.guard(inner)
    assert guarded is not None and guarded.name == inner.name
    for _ in range(3):
        with pytest.raises(ProviderAccountError):
            await guarded.complete(request())
    assert len(inner.requests) == 1
    assert account_stop.guard(None) is None


async def test_the_guard_passes_other_errors_and_answers_through() -> None:
    inner = ScriptedProvider(
        [ScriptedTurn(raise_error=ProviderUnavailable("503", status=503)), ScriptedTurn(text="a")]
    )
    guarded = account_stop.guard(inner)
    assert guarded is not None
    with pytest.raises(ProviderUnavailable):
        await guarded.complete(request())
    assert (await guarded.complete(request())).text == "a"  # a transient error is not sticky


async def test_the_decorator_writes_the_stop_record_and_returns_the_exit_code(
    tmp_path: Path,
) -> None:
    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        budget = Budget(remaining_usd=1.0)
        account_stop.track(args, budget)
        budget.reserve(0.1)
        budget.charge(0.05)
        (args.out / "raw.jsonl").write_text("{}\n")
        raise ERROR

    out = tmp_path / "out"
    out.mkdir()
    code = await run(argparse.Namespace(out=out))
    assert code == account_stop.ACCOUNT_STOPPED == 3
    record = json.loads((out / "run.json").read_text())
    assert record == {
        "complete": False,
        "stopped": {
            "reason": "provider_account_error",
            "provider": "openai",
            "status": 429,
            "code": "insufficient_quota",
            "message": "openai account error (HTTP 429, insufficient_quota): quota",
        },
        "spent_usd": 0.05,
        "files_written": ["raw.jsonl"],
    }


async def test_a_configuration_error_stops_the_run_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model or deployment the provider refuses (an account error by type) stops the run
    the same way, exit 3, with a reason and advice about the settings, not the account."""
    config = ProviderConfigError(
        "The API deployment for this resource does not exist.",
        provider="azure",
        status=404,
        code="DeploymentNotFound",
    )

    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        raise RuntimeError("wrapped") from config

    out = tmp_path / "out"
    assert await run(argparse.Namespace(out=out)) == account_stop.ACCOUNT_STOPPED
    stopped = json.loads((out / "run.json").read_text())["stopped"]
    assert stopped["reason"] == "provider_config_error"
    assert stopped["message"].startswith("azure rejected the request (HTTP 404, Deployment")
    err = capsys.readouterr().err
    assert "Fix the model or deployment settings and run again." in err
    assert "account" not in err.split("\n", 1)[0]


async def test_the_decorator_lets_every_other_error_through(tmp_path: Path) -> None:
    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        raise ProviderUnavailable("503")

    with pytest.raises(ProviderUnavailable):
        await run(argparse.Namespace(out=tmp_path / "out"))
    assert not (tmp_path / "out").exists()


async def test_a_successful_run_is_untouched(tmp_path: Path) -> None:
    @account_stop.stops_on_account_error
    async def run(args: argparse.Namespace) -> int:
        return 0

    assert await run(argparse.Namespace(out=tmp_path)) == 0
    assert not (tmp_path / "run.json").exists()
