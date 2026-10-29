"""Outbox delivery: claim due messages, deliver each by kind, record the outcome (S2_SPEC 6).

One :meth:`Relay.run_once` is one transaction:

1. ``claim_batch(batch_size)``: up to 20 ``pending`` rows that are due, locked with
   ``FOR UPDATE SKIP LOCKED`` so concurrent relays never hold the same row.
2. Deliver. Messages of different studies are delivered concurrently; messages of one
   study are delivered one after another in claim order, so a study's decisions are
   never reordered within a batch. Each delivery is bounded by ``delivery_timeout``.
3. Mark every message in the same transaction, then commit (releasing the locks):

   ===========================================  =======================================
   outcome                                      row
   ===========================================  =======================================
   delivered                                    ``delivered``, ``delivered_at = now()``
   refused by the workflow (validator rejected  ``delivered`` with ``last_error``, logged
   the update, ``WorkflowUpdateFailedError``)   at WARNING: final, the API already
                                                answered the client
   transient (Temporal unreachable, RPC error,  ``attempts + 1``, ``last_error``,
   timeout, workflow not caught up yet)         due again in ``min(2**attempts, 300)`` s
   ... for the ``max_attempts``-th time         ``dead``
   malformed payload, unknown kind              ``dead`` at once: retrying cannot help
   ===========================================  =======================================

If the process dies mid-batch the transaction rolls back: nothing is marked and every
claimed row is due again for any relay. Delivery is therefore at least once, and the
consumer is idempotent: the update id is ``decision-{review_id}`` (Temporal deduplicates
updates by id within a run) and the workflow ignores a review id it has applied.

**Catching up before deciding.** The API moves the study in the database the moment a
decision is recorded, but the workflow reaches the next review stage only after it has
processed the previous decision (and possibly run activities). A second decision
delivered in between would be refused by the validator as "wrong stage" and, being
final, lost. So before sending a decision the relay queries the workflow's ``status``:
if the workflow is still *behind* the decision (its ``(revision, stage)`` orders before
the decision's), the message is retried later instead of sent. Stages are totally
ordered within a revision and revisions only grow, so "behind" is well defined.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client, WorkflowUpdateFailedError

from basis.db.enums import Stage
from basis.db.models import OutboxMessage
from basis.db.repositories.outbox import NOTIFICATION, WORKFLOW_SIGNAL, OutboxRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.obs.logging import bound
from basis.temporal.names import (
    DECIDE_UPDATE,
    STATUS_QUERY,
    decision_update_id,
    study_workflow_id,
)
from basis.workflow.contracts import Decision, DecisionAck, StudyStatusView

BATCH_SIZE: Final = 20
MAX_ATTEMPTS: Final = 50
DELIVERY_TIMEOUT_S: Final = 30.0
MAX_BACKOFF_S: Final = 300
_STAGE_ORDER: Final = {stage.value: index for index, stage in enumerate(Stage)}

logger = logging.getLogger(__name__)


def backoff(attempts: int) -> timedelta:
    """Delay before the next try after ``attempts`` failed ones: ``min(2**attempts, 300)`` s."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    # 2**9 = 512 > 300: cap the exponent so huge attempt counts do not build huge ints.
    return timedelta(seconds=min(2 ** min(attempts, 9), MAX_BACKOFF_S))


def workflow_is_behind(view: StudyStatusView, decision: Decision) -> bool:
    """True when the workflow has not reached the decision's ``(revision, stage)`` yet."""
    current = (view.revision, _STAGE_ORDER[view.stage])
    target = (decision.revision, _STAGE_ORDER[decision.stage])
    return current < target


class TransientDeliveryError(Exception):
    """Delivery failed in a way that may succeed later; the message is retried."""


class WorkflowGateway(Protocol):
    """The two Temporal calls the relay makes, so tests can substitute them."""

    async def status(self, workflow_id: str) -> StudyStatusView: ...

    async def decide(self, workflow_id: str, decision: Decision) -> DecisionAck:
        """Execute the ``decide`` update. ``WorkflowUpdateFailedError`` means refused."""
        ...


class TemporalWorkflowGateway:
    def __init__(self, client: Client, *, rpc_timeout: float = DELIVERY_TIMEOUT_S) -> None:
        self.client = client
        self.rpc_timeout = timedelta(seconds=rpc_timeout)

    async def status(self, workflow_id: str) -> StudyStatusView:
        view: StudyStatusView = await self.client.get_workflow_handle(workflow_id).query(
            STATUS_QUERY, result_type=StudyStatusView, rpc_timeout=self.rpc_timeout
        )
        return view

    async def decide(self, workflow_id: str, decision: Decision) -> DecisionAck:
        ack: DecisionAck = await self.client.get_workflow_handle(workflow_id).execute_update(
            DECIDE_UPDATE,
            decision,
            id=decision_update_id(decision.review_id),
            result_type=DecisionAck,
            rpc_timeout=self.rpc_timeout,
        )
        return ack


class Outcome(StrEnum):
    DELIVERED = "delivered"
    REFUSED = "refused"
    RETRY = "retry"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class Result:
    message_id: uuid.UUID
    outcome: Outcome
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BatchReport:
    claimed: int
    delivered: int = 0
    refused: int = 0
    retried: int = 0
    dead: int = 0


def decision_from_payload(payload: Mapping[str, Any]) -> tuple[str, Decision]:
    """``(workflow_id, Decision)`` from a ``workflow_signal`` payload; raises if malformed."""
    decision = Decision.model_validate(
        {
            "review_id": payload["review_id"],
            "stage": payload["stage"],
            "revision": payload["revision"],
            "decision": payload["decision"],
            "reviewer_id": payload["reviewer_id"],
            "corrections": payload.get("corrections", ()),
            "submitted_at": payload["submitted_at"],
        }
    )
    workflow_id = payload.get("workflow_id") or study_workflow_id(payload["study_id"])
    return str(workflow_id), decision


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _refusal(exc: WorkflowUpdateFailedError) -> str:
    cause = exc.cause
    return f"rejected by the workflow: {cause}" if cause is not None else str(exc)


class Relay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        gateway: WorkflowGateway,
        *,
        poll_seconds: float = 2.0,
        batch_size: int = BATCH_SIZE,
        max_attempts: int = MAX_ATTEMPTS,
        delivery_timeout: float = DELIVERY_TIMEOUT_S,
    ) -> None:
        if batch_size < 1 or max_attempts < 1 or poll_seconds <= 0 or delivery_timeout <= 0:
            raise ValueError("relay limits must be positive")
        self.sessionmaker = sessionmaker
        self.gateway = gateway
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.delivery_timeout = delivery_timeout
        # monotonic time of the last poll that reached the database (health check)
        self.last_poll: float | None = None

    # ------------------------------------------------------------------ one batch

    async def run_once(self) -> BatchReport:
        async with session_scope(self.sessionmaker) as session:
            outbox = OutboxRepository(session)
            messages = await outbox.claim_batch(self.batch_size)
            self.last_poll = time.monotonic()
            if not messages:
                return BatchReport(claimed=0)
            results = await self._deliver_all(messages)
            for message, result in zip(messages, results, strict=True):
                await self._record(outbox, message, result)
        return BatchReport(
            claimed=len(messages),
            delivered=sum(r.outcome is Outcome.DELIVERED for r in results),
            refused=sum(r.outcome is Outcome.REFUSED for r in results),
            retried=sum(r.outcome is Outcome.RETRY for r in results),
            dead=sum(r.outcome is Outcome.DEAD for r in results),
        )

    async def _deliver_all(self, messages: list[OutboxMessage]) -> list[Result]:
        """Studies in parallel, each study's messages in order. Results keep claim order."""
        groups: dict[str, list[int]] = {}
        for index, message in enumerate(messages):
            key = str(message.payload.get("study_id", message.id))
            groups.setdefault(key, []).append(index)
        results: list[Result | None] = [None] * len(messages)

        async def deliver_group(indexes: list[int]) -> None:
            for index in indexes:
                results[index] = await self._deliver(messages[index])

        async with asyncio.TaskGroup() as group:
            for indexes in groups.values():
                group.create_task(deliver_group(indexes))
        return [result for result in results if result is not None]

    async def _deliver(self, message: OutboxMessage) -> Result:
        study_id = message.payload.get("study_id")
        with bound(outbox_id=str(message.id), study_id=study_id, kind=message.kind):
            try:
                async with asyncio.timeout(self.delivery_timeout):
                    return await self._deliver_by_kind(message)
            except TransientDeliveryError as exc:
                return Result(message.id, Outcome.RETRY, str(exc))
            except WorkflowUpdateFailedError as exc:
                return Result(message.id, Outcome.REFUSED, _refusal(exc))
            except TimeoutError:
                error = f"timed out after {self.delivery_timeout:g} s"
                return Result(message.id, Outcome.RETRY, error)
            except Exception as exc:
                # RPCError, "Failed client connect" (RuntimeError), or anything else
                # unexpected: retried with backoff and dead-lettered after max_attempts,
                # so a bug cannot make the relay spin or lose the message.
                return Result(message.id, Outcome.RETRY, _describe(exc))

    async def _deliver_by_kind(self, message: OutboxMessage) -> Result:
        if message.kind == WORKFLOW_SIGNAL:
            return await self._deliver_decision(message)
        if message.kind == NOTIFICATION:
            # S5 wires email/Slack; until then a notification is delivered to the log.
            logger.info("notification", extra={"payload": dict(message.payload)})
            return Result(message.id, Outcome.DELIVERED)
        return Result(message.id, Outcome.DEAD, f"unknown outbox kind {message.kind!r}")

    async def _deliver_decision(self, message: OutboxMessage) -> Result:
        try:
            workflow_id, decision = decision_from_payload(message.payload)
        except (KeyError, TypeError, ValidationError) as exc:
            return Result(message.id, Outcome.DEAD, f"malformed payload: {_describe(exc)}")
        view = await self.gateway.status(workflow_id)
        if workflow_is_behind(view, decision):
            raise TransientDeliveryError(
                f"workflow at {view.stage} revision {view.revision} has not reached "
                f"{decision.stage} revision {decision.revision} yet"
            )
        ack = await self.gateway.decide(workflow_id, decision)
        if not ack.accepted:
            return Result(message.id, Outcome.REFUSED, f"not accepted: {ack.reason}")
        return Result(message.id, Outcome.DELIVERED)

    async def _record(
        self, outbox: OutboxRepository, message: OutboxMessage, result: Result
    ) -> None:
        fields = {
            "outbox_id": str(message.id),
            "study_id": message.payload.get("study_id"),
            "kind": message.kind,
        }
        attempts = message.attempts + 1
        if result.outcome is Outcome.DELIVERED:
            await outbox.mark_delivered(message.id)
            logger.info("outbox message delivered", extra={**fields, "attempts": attempts})
        elif result.outcome is Outcome.REFUSED:
            await outbox.mark_delivered(message.id, last_error=result.error)
            logger.warning(
                "outbox message refused by its consumer; not retried",
                extra={**fields, "error": result.error, "error_code": E.INF_RELAY_REFUSED},
            )
        elif result.outcome is Outcome.DEAD or attempts >= self.max_attempts:
            await outbox.mark_dead(message.id, result.error or "dead")
            logger.error(
                "outbox message is dead",
                extra={
                    **fields,
                    "attempts": attempts,
                    "error": result.error,
                    "error_code": E.INF_RELAY_DEAD,
                },
            )
        else:
            retry_in = backoff(attempts)
            await outbox.mark_failed(message.id, result.error or "failed", retry_in)
            logger.warning(
                "outbox delivery failed; will retry",
                extra={
                    **fields,
                    "attempts": attempts,
                    "retry_in_s": retry_in.total_seconds(),
                    "error": result.error,
                },
            )

    # ------------------------------------------------------------------ the loop

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set. A failing poll (database down) is logged and retried."""
        logger.info("outbox relay started", extra={"poll_seconds": self.poll_seconds})
        while not stop.is_set():
            try:
                report = await self.run_once()
            except Exception:
                logger.exception("outbox poll failed")
                report = BatchReport(claimed=0)
            if report.claimed == self.batch_size:
                continue  # a full batch: more may be due, poll again at once
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
        logger.info("outbox relay stopped")
