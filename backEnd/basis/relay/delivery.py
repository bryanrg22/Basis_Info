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
   invitation email handed to the sender        ``delivered``; ``invitations.emailed_at``
   sender failed (SMTP down, 4xx)               retried as transient; ``email_error`` set
   recipient refused for good (5xx)             ``dead`` at once; ``email_error`` set
   invitation revoked/answered before the send  ``delivered`` with ``last_error``, not sent
   alert handed to the email sender             ``delivered``
   alert, sender failed (SMTP down, 4xx)        retried as transient (``E.ALERT_EMAIL_FAILED``)
   alert, recipient refused for good (5xx)      ``dead`` at once (``E.ALERT_EMAIL_FAILED``)
   alert, no ``ALERT_EMAIL_TO``                 logged at WARNING, ``delivered``
                                                (``E.ALERT_NO_RECIPIENT``)
   ===========================================  =======================================

If the process dies mid-batch the transaction rolls back: nothing is marked and every
claimed row is due again for any relay. Delivery is therefore at least once, and the
consumer is idempotent: the update id is ``decision-{review_id}`` (Temporal deduplicates
updates by id within a run) and the workflow ignores a review id it has applied.

**Notifications.** A ``notification`` row carries a ``type``. ``invitation`` is the
invitation email (``basis.email.invitation``): the relay loads the invitation, skips it
when it was already emailed (a redelivered row) or is no longer pending, else renders it
and calls the configured :class:`~basis.email.EmailSender`; the outcome is recorded on the
invitation in the same transaction that marks the outbox row.
``alert`` is an operator alert (``basis.db.repositories.alert``: a failed study, a stale
heartbeat): the relay renders it (``basis.notify.render``) and calls the same sender with
``ALERT_EMAIL_TO`` as the recipient and the alert's key as the idempotency key, so a
redelivered row cannot become a second email at a provider that honours the key. Any
other type is delivered to the log, as before.

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
from basis.db.errors import NotVisibleError
from basis.db.models import OutboxMessage
from basis.db.repositories.alert import ALERT_NOTIFICATION
from basis.db.repositories.invitation import InvitationRepository, InvitationStatus
from basis.db.repositories.outbox import (
    INVITATION_NOTIFICATION,
    NOTIFICATION,
    WORKFLOW_SIGNAL,
    OutboxRepository,
)
from basis.db.session import session_scope
from basis.email.invitation import render_invitation
from basis.email.sender import (
    EmailRejectedError,
    EmailSender,
    EmailSendError,
    LoggingEmailSender,
)
from basis.errors.codes import E
from basis.notify.render import MalformedAlert, render_alert
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
    # An invitation email was handed to the sender in this delivery (``emailed_at`` is
    # recorded); False for a redelivered row whose email had already gone.
    emailed: bool = False


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
        email_sender: EmailSender | None = None,
        app_url: str | None = None,
        alert_to: str | None = None,
        env: str | None = None,
    ) -> None:
        if batch_size < 1 or max_attempts < 1 or poll_seconds <= 0 or delivery_timeout <= 0:
            raise ValueError("relay limits must be positive")
        self.sessionmaker = sessionmaker
        self.gateway = gateway
        # The log sender unless one is given: a relay never sends mail by accident.
        self.email_sender: EmailSender = email_sender or LoggingEmailSender()
        self.app_url = app_url
        self.alert_to = alert_to
        self.env = env
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
            invitations = InvitationRepository(session)
            for message, result in zip(messages, results, strict=True):
                await self._record(outbox, message, result, invitations=invitations)
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
            if message.payload.get("type") == INVITATION_NOTIFICATION:
                return await self._deliver_invitation(message)
            if message.payload.get("type") == ALERT_NOTIFICATION:
                return await self._deliver_alert(message)
            # Other notifications (review reminders) are delivered to the log until a channel
            # for them exists.
            logger.info("notification", extra={"payload": dict(message.payload)})
            return Result(message.id, Outcome.DELIVERED)
        return Result(message.id, Outcome.DEAD, f"unknown outbox kind {message.kind!r}")

    async def _deliver_invitation(self, message: OutboxMessage) -> Result:
        try:
            invitation_id = uuid.UUID(str(message.payload["invitation_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            return Result(message.id, Outcome.DEAD, f"malformed payload: {_describe(exc)}")
        # Read outside the claim transaction (nothing to lock: the row is written only by
        # ``_record``, in the claim transaction, after this returns).
        async with self.sessionmaker() as session:
            try:
                view = await InvitationRepository(session).view(invitation_id)
            except NotVisibleError:
                return Result(message.id, Outcome.DEAD, f"invitation {invitation_id} not found")
        if view.invitation.emailed_at is not None:
            return Result(message.id, Outcome.DELIVERED)  # already sent: never twice
        if view.status is not InvitationStatus.PENDING:
            return Result(
                message.id,
                Outcome.DELIVERED,
                f"not sent: the invitation was {view.status.value} before it was emailed",
            )
        try:
            email = render_invitation(view, self.app_url)
        except ValueError as exc:  # an address the message rules refuse: retrying cannot help
            return Result(message.id, Outcome.DEAD, f"cannot render: {exc}")
        try:
            sent = await self.email_sender.send(email)
        except EmailRejectedError as exc:
            return Result(message.id, Outcome.DEAD, f"email rejected: {exc}")
        logger.info(
            "invitation email sent",
            extra={
                "invitation_id": str(invitation_id),
                "provider": sent.provider,
                "message_id": sent.message_id,
            },
        )
        return Result(message.id, Outcome.DELIVERED, emailed=True)

    async def _deliver_alert(self, message: OutboxMessage) -> Result:
        """Render the alert and hand it to the sender; without a recipient, log it."""
        recipient = self.alert_to or "nobody@localhost"
        try:
            email = render_alert(message.payload, recipient, env=self.env)
        except MalformedAlert as exc:
            return Result(message.id, Outcome.DEAD, f"malformed alert: {exc}")
        if self.alert_to is None:
            logger.warning(
                "alert (no ALERT_EMAIL_TO)",
                extra={
                    "error_code": E.ALERT_NO_RECIPIENT,
                    "alert_key": email.idempotency_key,
                    "alert_subject": email.subject,
                    "alert_text": email.text,
                },
            )
            return Result(message.id, Outcome.DELIVERED)
        try:
            sent = await self.email_sender.send(email)
        except EmailRejectedError as exc:
            logger.error(
                "alert email refused",
                extra={"error_code": E.ALERT_EMAIL_FAILED, "alert_key": email.idempotency_key},
            )
            return Result(message.id, Outcome.DEAD, f"alert email refused: {exc}")
        except EmailSendError as exc:
            logger.warning(
                "alert email failed; will retry",
                extra={"error_code": E.ALERT_EMAIL_FAILED, "alert_key": email.idempotency_key},
            )
            raise TransientDeliveryError(f"alert email failed: {exc}") from exc
        logger.info(
            "alert emailed",
            extra={
                "alert_key": email.idempotency_key,
                "provider": sent.provider,
                "message_id": sent.message_id,
            },
        )
        return Result(message.id, Outcome.DELIVERED)

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
        self,
        outbox: OutboxRepository,
        message: OutboxMessage,
        result: Result,
        *,
        invitations: InvitationRepository | None = None,
    ) -> None:
        fields = {
            "outbox_id": str(message.id),
            "study_id": message.payload.get("study_id"),
            "kind": message.kind,
        }
        attempts = message.attempts + 1
        if invitations is not None:
            await self._record_email(invitations, message, result, attempts)
        if result.outcome is Outcome.DELIVERED:
            await outbox.mark_delivered(message.id, last_error=result.error)
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

    async def _record_email(
        self,
        invitations: InvitationRepository,
        message: OutboxMessage,
        result: Result,
        attempts: int,
    ) -> None:
        """The invitation's own record of its email, next to the outbox row's."""
        if message.kind != NOTIFICATION or message.payload.get("type") != INVITATION_NOTIFICATION:
            return
        try:
            invitation_id = uuid.UUID(str(message.payload.get("invitation_id")))
        except (ValueError, TypeError):
            return  # dead as malformed; there is no row to annotate
        if result.emailed:
            await invitations.record_emailed(invitation_id)
            return
        if result.error is None:
            return  # already emailed earlier: nothing new to record
        await invitations.record_email_error(invitation_id, result.error)
        if result.outcome is not Outcome.DELIVERED:
            logger.warning(
                "invitation email failed",
                extra={
                    "invitation_id": str(invitation_id),
                    "attempts": attempts,
                    "error": result.error,
                    "error_code": E.INF_EMAIL_FAILED,
                },
            )

    # ------------------------------------------------------------------ the loop

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set. A failing poll (database down) is logged and retried."""
        logger.info(
            "outbox relay started",
            extra={
                "poll_seconds": self.poll_seconds,
                "email_provider": self.email_sender.provider,
                "app_url": self.app_url,
                "alert_recipient_set": self.alert_to is not None,
            },
        )
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
