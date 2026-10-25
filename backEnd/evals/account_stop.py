"""Stop an evaluation run at the first provider account error (EVAL_REPORT section 26.12).

A provider that refuses the account (no credit or quota, a bad or revoked key:
:class:`~basis.llm.errors.ProviderAccountError`) refuses every later call too. Before this
the runners went on: the vision step recorded each refused photo as ``invalid_model_output``
(156 of them in one run), the pool sent ``gpt-*`` requests to the other provider, and the
run ended with ``exit 0`` and partial predictions that looked complete.

:func:`stops_on_account_error` wraps a runner's ``run`` coroutine. At the first account
error it cancels the run's outstanding model calls, writes ``<out>/run.json`` as::

    {"complete": false,
     "stopped": {"reason": "provider_account_error", "provider": ..., "status": ...,
                 "code": ..., "message": ...},
     "spent_usd": ..., "files_written": [...]}

(the provider's message, never a key), prints what happened and how to resume, and returns
:data:`ACCOUNT_STOPPED`. A configuration error (:class:`~basis.llm.errors.ProviderConfigError`,
an account error by type: an unknown model or deployment, a parameter the model refuses, no
provider serving the model) stops the run the same way, with ``"reason":
"provider_config_error"`` and a message that says to fix the model or deployment settings.
Whatever the run had already written to ``<out>`` stays (completed repeats, raw records);
``run.json`` is the one place that says the run is partial.
:func:`track` lets a runner name the budget whose spend the stop record reports, and
:func:`guard` wraps its provider so that, once one call has been refused, the calls already
queued behind the concurrency limit fail at once without reaching the provider (with
``asyncio.gather`` a queued call can start before the run sees the first error).
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Final, cast

from basis.llm.errors import ProviderAccountError, ProviderConfigError
from basis.llm.provider import Budget, ChatProvider, ChatRequest, ChatResponse

ACCOUNT_STOPPED: Final = 3
REASON: Final = "provider_account_error"
CONFIG_REASON: Final = "provider_config_error"
_BUDGET_ATTR: Final = "_account_stop_budget"


def track(args: Any, budget: Budget) -> None:
    """Report ``budget``'s spend in the stop record of the run ``args`` configures."""
    setattr(args, _BUDGET_ATTR, budget)


class AccountGuard:
    """``provider``, refusing every call after the first account error without making it."""

    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider
        self.name = provider.name
        self.error: ProviderAccountError | None = None
        self.calls = 0

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._provider, attribute)  # the pool's candidates, providers, ...

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if self.error is not None:
            raise self.error
        self.calls += 1
        try:
            return await self._provider.complete(request)
        except ProviderAccountError as error:
            if self.error is None:
                self.error = error
            raise


def guard[T: ChatProvider](provider: T | None) -> T | None:
    """``provider`` behind an :class:`AccountGuard` (``None`` stays ``None``)."""
    return None if provider is None else cast(T, AccountGuard(provider))


def find_account_error(exc: BaseException | None) -> ProviderAccountError | None:
    """The :class:`ProviderAccountError` ``exc`` is, groups, or was raised from."""
    seen: set[int] = set()
    pending: list[BaseException] = [] if exc is None else [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ProviderAccountError):
            return current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return None


def stop_record(error: ProviderAccountError) -> dict[str, Any]:
    return {
        "reason": CONFIG_REASON if isinstance(error, ProviderConfigError) else REASON,
        "provider": error.provider,
        "status": error.status,
        "code": error.code,
        "message": str(error),
    }


def write_stopped(out: Path, error: ProviderAccountError, *, spent_usd: float | None) -> Path:
    """``<out>/run.json`` for a run stopped by ``error`` (replacing any earlier one: a
    ``run.json`` left in ``out`` by a previous run does not describe this one)."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / "run.json"
    written = sorted(p.name for p in out.iterdir() if p.is_file() and p.name != "run.json")
    summary: dict[str, Any] = {
        "complete": False,
        "stopped": stop_record(error),
        "spent_usd": None if spent_usd is None else round(spent_usd, 6),
        "files_written": written,
    }
    path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return path


def stops_on_account_error[**P](
    run: Callable[P, Coroutine[Any, Any, int]],
) -> Callable[P, Coroutine[Any, Any, int]]:
    """``run`` (``async def run(args, ...) -> int`` with ``args.out``), stopped at the first
    account error with a partial ``run.json`` and exit code :data:`ACCOUNT_STOPPED`."""

    @functools.wraps(run)
    async def wrapped(*a: P.args, **kw: P.kwargs) -> int:
        args: Any = a[0] if a else kw["args"]
        before = asyncio.all_tasks()
        try:
            return await run(*a, **kw)
        except Exception as exc:
            error = find_account_error(exc)
            if error is None:
                raise
            await _cancel_started_since(before)
            budget = getattr(args, _BUDGET_ATTR, None)
            out = Path(args.out)
            path = write_stopped(
                out, error, spent_usd=budget.spent_usd if isinstance(budget, Budget) else None
            )
            fix = (
                "the model or deployment settings"
                if isinstance(error, ProviderConfigError)
                else "the account (credit, quota or key)"
            )
            print(
                f"STOPPED: {error}\n"
                f"Every later call would fail the same way, so the run stopped at the first "
                f"one. Partial results are in {out}; {path.name} has complete=false. Fix "
                f"{fix} and run again.",
                file=sys.stderr,
            )
            return ACCOUNT_STOPPED

    return wrapped


async def _cancel_started_since(before: set[asyncio.Task[Any]]) -> None:
    """Cancel the tasks the run started that are still going (``asyncio.gather`` leaves
    its other children running when one fails), so no further model call is made."""
    current = asyncio.current_task()
    leftover = [t for t in asyncio.all_tasks() - before if t is not current and not t.done()]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)
