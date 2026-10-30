"""``python -m evals.provoke_errors``: provoke the provider failures on purpose and record,
per case, which error was expected, which occurred, and whether they matched.

The product maps every provider failure to an error code (``docs/ERROR_CODES.md``,
``BASIS-PROVIDER-nnn`` / ``BASIS-BUDGET-nnn``) and decides from it whether a study stops,
retries or flags an item. This runner checks that mapping against the live providers with
failures that cost nothing: a key the provider refuses, a model that does not exist, a
parameter the model rejects, a request that cannot finish in time, a budget that cannot
cover one call. Each scenario is one case of kind ``expected_error`` in ``results.json``:

* ``passed``: the expected code occurred;
* ``failed``: a different code occurred, or no error at all (the system under test is the
  mapping, so an unexpected code is its failure);
* ``test_error``: the scenario could not be run (no provider configured for it, the harness
  itself raised).

A provider that is not configured (no key in the environment) skips its scenarios as
``test_error`` with the reason, never as ``passed``. An account without credit cannot be
provoked on demand: that scenario is listed in the docs as covered by the unit tests only.

``--dry-run`` runs the scenarios against a scripted provider that raises what each expects
(and one that deliberately does not), so the record can be exercised without a network;
it proves the mechanics, not the providers. Writes the run record
(:mod:`evals.record`) under ``--out`` and files it in the verification tree as component
``provider_errors``, version = the Basis release (``git describe --tags --abbrev=0``), the
component under test being the error mapping of that release.

Usage (from ``backEnd/``, the provider keys in the environment)::

    BASIS_ENV=dev python -m evals.provoke_errors --out ~/basis-eval-data/predictions/errors_v0_11
    python -m evals.provoke_errors --out /tmp/errors --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import openai

from basis.config import Settings, load_settings
from basis.errors.codes import E
from basis.llm.anthropic_provider import AnthropicProvider
from basis.llm.call import CallContext, call_model
from basis.llm.errors import (
    ProviderAccountError,
    ProviderConfigError,
    ProviderError,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.provider import Budget, BudgetExceededError, ChatProvider, ChatRequest, Message
from basis.llm.providers import OpenAIProvider
from evals import record

COMPONENT = "provider_errors"
# A key no provider issued: the request is refused before any token is billed.
BAD_KEY_OPENAI = "sk-basis-provoke-errors-not-a-real-key-000000000000"  # pragma: allowlist secret
BAD_KEY_ANTHROPIC = "sk-ant-basis-provoke-errors-not-a-real-key-0000000"  # pragma: allowlist secret
TINY_TIMEOUT_S = 0.001

Build = Callable[[Settings], ChatProvider | None]


@dataclass(frozen=True)
class Scenario:
    """One provoked failure: how to build the provider and the request, what must occur."""

    id: str
    expected: str
    description: str
    provider: str  # openai | anthropic
    build: Build  # the provider to call (None: that provider is not configured)
    request: Callable[[Settings], ChatRequest]
    budget_usd: float = 1.0


def _request(model: str, **changes: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "model": model,
        "system": "You answer with one word.",
        "messages": (Message(role="user", content="Say ok."),),
        "max_tokens": 16,
        "prompt_version": "provoke_errors",
        "study_id": str(uuid.UUID(int=0)),
        "activity": "evals.provoke_errors",
    }
    values.update(changes)
    return ChatRequest(**values)


def _openai(
    settings: Settings, *, api_key: str | None = None, timeout: float | None = None
) -> ChatProvider | None:
    key = api_key or (settings.openai_api_key.get_secret_value() if settings.openai_api_key else "")
    if not key:
        return None
    seconds = timeout if timeout is not None else settings.llm_request_timeout_seconds
    client = openai.AsyncOpenAI(api_key=key, max_retries=0, timeout=seconds)
    return OpenAIProvider(client, timeout_s=seconds)


def _anthropic(
    settings: Settings, *, api_key: str | None = None, timeout: float | None = None
) -> ChatProvider | None:
    secret = getattr(settings, "anthropic_api_key", None)
    key = api_key or (secret.get_secret_value() if secret else "")
    if not key:
        return None
    seconds = timeout if timeout is not None else settings.llm_request_timeout_seconds
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=0, timeout=seconds)
    return AnthropicProvider(client, timeout_s=seconds, effort=settings.anthropic_effort)


def _text_model(settings: Settings) -> str:
    return str(settings.text_model)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "openai_bad_key",
        E.PROVIDER_ACCOUNT,
        "an API key OpenAI did not issue (401 invalid_api_key)",
        "openai",
        lambda s: _openai(s, api_key=BAD_KEY_OPENAI),
        lambda s: _request(_text_model(s)),
    ),
    Scenario(
        "openai_unknown_model",
        E.PROVIDER_CONFIG,
        "a model name that does not exist (404 model_not_found)",
        "openai",
        lambda s: _openai(s),
        lambda s: _request("gpt-does-not-exist-basis"),
    ),
    Scenario(
        "openai_rejected_parameter",
        E.PROVIDER_CONFIG,
        "a reasoning effort the model does not take (reasoning_effort 'ultra', 400)",
        "openai",
        lambda s: _openai(s),
        lambda s: _request(_text_model(s), reasoning_effort="ultra"),
    ),
    Scenario(
        "openai_timeout",
        E.PROVIDER_UNAVAILABLE,
        f"a request that cannot finish in {TINY_TIMEOUT_S} s (timeout after the provider's retry)",
        "openai",
        lambda s: _openai(s, timeout=TINY_TIMEOUT_S),
        lambda s: _request(_text_model(s)),
    ),
    Scenario(
        "openai_budget_exceeded",
        E.BUDGET_PHOTO,
        "a budget that cannot cover one call (nothing is sent)",
        "openai",
        lambda s: _openai(s),
        lambda s: _request(_text_model(s)),
        budget_usd=0.0,
    ),
    Scenario(
        "anthropic_bad_key",
        E.PROVIDER_ACCOUNT,
        "an API key Anthropic did not issue (401 authentication_error)",
        "anthropic",
        lambda s: _anthropic(s, api_key=BAD_KEY_ANTHROPIC),
        lambda s: _request("claude-haiku-5-5"),
    ),
    Scenario(
        "anthropic_unknown_model",
        E.PROVIDER_CONFIG,
        "a Claude model name that does not exist (404 not_found_error)",
        "anthropic",
        lambda s: _anthropic(s),
        lambda s: _request("claude-does-not-exist-basis"),
    ),
    Scenario(
        "anthropic_timeout",
        E.PROVIDER_UNAVAILABLE,
        f"a request that cannot finish in {TINY_TIMEOUT_S} s",
        "anthropic",
        lambda s: _anthropic(s, timeout=TINY_TIMEOUT_S),
        lambda s: _request("claude-haiku-5-5"),
    ),
)


# --------------------------------------------------------------------------- dry run


class _Raising:
    """A provider that raises what a scenario expects; ``mismatch`` raises something else."""

    name = "scripted"

    def __init__(self, error: BaseException | None) -> None:
        self._error = error

    async def complete(self, request: ChatRequest) -> Any:
        if self._error is None:
            raise AssertionError("the dry run only raises")
        raise self._error


def _dry_error(scenario: Scenario) -> BaseException:
    if scenario.expected == E.PROVIDER_ACCOUNT:
        return ProviderAccountError("invalid api key", provider=scenario.provider, status=401)
    if scenario.expected == E.PROVIDER_CONFIG:
        return ProviderConfigError("model not found", provider=scenario.provider, status=404)
    if scenario.expected == E.PROVIDER_UNAVAILABLE:
        return ProviderUnavailable("timed out", provider=scenario.provider, status=None)
    return BudgetExceededError("call would cost about $0.01 but $0.00 remains")


def dry_scenarios() -> tuple[Scenario, ...]:
    """Every scenario against a scripted provider raising its expected error, plus one that
    raises a different error on purpose (recorded ``failed``, matched ``no``) and one whose
    provider is not configured (recorded ``test_error``)."""
    scripted = tuple(
        Scenario(
            s.id,
            s.expected,
            s.description + " [dry run]",
            s.provider,
            (lambda err: lambda settings: _Raising(err))(_dry_error(s)),
            s.request,
            s.budget_usd,
        )
        for s in SCENARIOS
    )
    mismatch = Scenario(
        "dry_mismatch",
        E.PROVIDER_CONFIG,
        "expects a configuration error but the provider refuses the input [dry run]",
        "openai",
        lambda settings: _Raising(
            ProviderRequestRejected("too large", provider="openai", status=413)
        ),
        lambda s: _request("gpt-x"),
    )
    unconfigured = Scenario(
        "dry_unconfigured",
        E.PROVIDER_ACCOUNT,
        "a provider that is not configured [dry run]",
        "nowhere",
        lambda settings: None,
        lambda s: _request("gpt-x"),
    )
    return (*scripted, mismatch, unconfigured)


# --------------------------------------------------------------------------- running


async def run_scenario(
    scenario: Scenario, settings: Settings, run: record.EvalRun
) -> dict[str, Any]:
    """Run one scenario and record its case."""
    started = time.monotonic()
    provider = scenario.build(settings)
    if provider is None:
        return run.case(
            scenario.id,
            record.TEST_ERROR,
            kind="expected_error",
            expected_error=scenario.expected,
            detail=f"{scenario.provider} is not configured (no key in the environment)",
            seconds=time.monotonic() - started,
        )
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.provoke_errors",
        budget=Budget(remaining_usd=scenario.budget_usd),
        recorder=run.recorder,
    )
    try:
        response = await call_model(provider, scenario.request(settings), ctx)
    except (ProviderError, BudgetExceededError) as error:
        observed = record.error_code(error)
        return run.case(
            scenario.id,
            record.PASSED if observed == scenario.expected else record.FAILED,
            kind="expected_error",
            expected_error=scenario.expected,
            observed_error=error,
            detail=scenario.description,
            seconds=time.monotonic() - started,
        )
    except Exception as error:  # the harness, not the provider mapping
        return run.case(
            scenario.id,
            record.TEST_ERROR,
            kind="expected_error",
            expected_error=scenario.expected,
            observed_error=error,
            detail=f"unexpected exception type {type(error).__name__}: {scenario.description}",
            seconds=time.monotonic() - started,
        )
    return run.case(
        scenario.id,
        record.FAILED,
        kind="expected_error",
        expected_error=scenario.expected,
        detail=f"no error occurred; the model answered {response.text[:80]!r}",
        seconds=time.monotonic() - started,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.provoke_errors", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--dry-run", action="store_true", help="scripted providers, no network")
    parser.add_argument("--only", nargs="+", default=None, help="these scenario ids only")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace, scenarios: Sequence[Scenario] | None = None) -> int:
    settings = load_settings()
    chosen = list(
        scenarios if scenarios is not None else (dry_scenarios() if args.dry_run else SCENARIOS)
    )
    if args.only:
        chosen = [s for s in chosen if s.id in set(args.only)]
    git = record.git_state()
    eval_run = record.EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        settings=settings,
        component=COMPONENT,
        version=git.get("release") or record.UNVERSIONED,
    )
    try:
        print(f"{len(chosen)} scenarios ({'dry run' if args.dry_run else 'live providers'})")
        for scenario in chosen:
            case = await run_scenario(scenario, settings, eval_run)
            print(
                f"{scenario.id}: {case['outcome']} (expected {scenario.expected}, "
                f"observed {case.get('observed_error') or '-'})"
            )
        results = record.summarize_cases(eval_run.cases)
        summary: dict[str, Any] = {
            "scenarios": [s.id for s in chosen],
            "dry_run": bool(args.dry_run),
            "results": results,
            "spent_usd": round(float(eval_run.recorder.spent()), 6),
        }
        print(f"wrote {args.out}", file=sys.stderr)
        eval_run.finish(summary)
        return 0 if results[record.FAILED] == 0 else 1
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
