"""How the relay classifies a delivery and records it, and its health endpoint (no DB).

``Relay._deliver`` and ``Relay._record`` are exercised with an in-memory gateway and a
recording stand-in for ``OutboxRepository``; the database side is covered in
``tests/integration/relay/test_relay.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from basis.db.models import OutboxMessage
from basis.relay.delivery import (
    MAX_ATTEMPTS,
    Outcome,
    Relay,
    Result,
)
from basis.relay.main import healthy, serve_health
from basis.workflow.contracts import Decision, DecisionAck, StudyStatusView

STUDY = str(uuid.uuid4())


def message(kind: str = "workflow_signal", attempts: int = 0, **payload: Any) -> OutboxMessage:
    body: dict[str, Any] = {
        "study_id": STUDY,
        "workflow_id": f"study-{STUDY}",
        "review_id": str(uuid.uuid4()),
        "stage": "appraisal_review",
        "revision": 1,
        "decision": "approve",
        "reviewer_id": str(uuid.uuid4()),
        "submitted_at": datetime.now(UTC).isoformat(),
        "corrections": [],
        **payload,
    }
    return OutboxMessage(id=uuid.uuid4(), kind=kind, payload=body, attempts=attempts)


class Gateway:
    def __init__(
        self,
        *,
        view: StudyStatusView | None = None,
        error: BaseException | None = None,
        ack: DecisionAck | None = None,
        delay: float = 0.0,
    ) -> None:
        self.view = view or StudyStatusView(stage="appraisal_review", revision=1)
        self.error = error
        self.ack = ack or DecisionAck(accepted=True, applied_revision=1)
        self.delay = delay
        self.calls: list[str] = []

    async def status(self, workflow_id: str) -> StudyStatusView:
        self.calls.append(f"status {workflow_id}")
        return self.view

    async def decide(self, workflow_id: str, decision: Decision) -> DecisionAck:
        self.calls.append(f"decide {workflow_id} {decision.review_id}")
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.ack


def relay(gateway: Gateway, **kwargs: Any) -> Relay:
    return Relay(None, gateway, **kwargs)  # type: ignore[arg-type]


async def outcome_of(gateway: Gateway, msg: OutboxMessage | None = None, **kwargs: Any) -> Result:
    return await relay(gateway, **kwargs)._deliver(msg or message())


async def test_delivered() -> None:
    gateway = Gateway()
    msg = message()
    result = await outcome_of(gateway, msg)
    assert (result.outcome, result.error) == (Outcome.DELIVERED, None)
    assert gateway.calls == [
        f"status study-{STUDY}",
        f"decide study-{STUDY} {msg.payload['review_id']}",
    ]


async def test_validator_rejection_is_refused() -> None:
    error = WorkflowUpdateFailedError(ApplicationError("wrong stage", type="ValueError"))
    result = await outcome_of(Gateway(error=error))
    assert result.outcome is Outcome.REFUSED
    assert result.error == "rejected by the workflow: ValueError: wrong stage"


async def test_an_ack_that_does_not_accept_is_refused() -> None:
    ack = DecisionAck(accepted=False, applied_revision=1, reason="study cancelled")
    result = await outcome_of(Gateway(ack=ack))
    assert (result.outcome, result.error) == (Outcome.REFUSED, "not accepted: study cancelled")


@pytest.mark.parametrize(
    "error",
    [
        RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""),
        RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b""),
        RuntimeError("Failed client connect: connection refused"),
        ConnectionRefusedError(61, "Connection refused"),
        KeyError("a bug"),
    ],
    ids=["unavailable", "not-found", "connect", "refused", "bug"],
)
async def test_transient_and_unexpected_errors_are_retried(error: BaseException) -> None:
    result = await outcome_of(Gateway(error=error))
    assert result.outcome is Outcome.RETRY
    assert result.error is not None and type(error).__name__ in result.error


async def test_a_slow_delivery_times_out_and_is_retried() -> None:
    result = await outcome_of(Gateway(delay=1.0), delivery_timeout=0.05)
    assert (result.outcome, result.error) == (Outcome.RETRY, "timed out after 0.05 s")


async def test_a_workflow_behind_is_retried_without_sending() -> None:
    gateway = Gateway(view=StudyStatusView(stage="extracting", revision=1))
    result = await outcome_of(gateway)
    assert result.outcome is Outcome.RETRY
    assert result.error == (
        "workflow at extracting revision 1 has not reached appraisal_review revision 1 yet"
    )
    assert len(gateway.calls) == 1  # status only; the update was not sent


async def test_notification_is_delivered_to_the_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="basis.relay.delivery")
    gateway = Gateway()
    result = await outcome_of(gateway, message("notification"))
    assert result.outcome is Outcome.DELIVERED and gateway.calls == []
    assert any(record.message == "notification" for record in caplog.records)


async def test_unknown_kind_and_malformed_payload_are_dead() -> None:
    assert (await outcome_of(Gateway(), message("fax"))).outcome is Outcome.DEAD
    malformed = message(stage="nowhere")
    result = await outcome_of(Gateway(), malformed)
    assert result.outcome is Outcome.DEAD
    assert result.error is not None and result.error.startswith("malformed payload")


async def test_one_studys_messages_are_delivered_in_order_others_concurrently() -> None:
    order: list[str] = []

    class Slow(Gateway):
        async def decide(self, workflow_id: str, decision: Decision) -> DecisionAck:
            order.append(f"start {decision.review_id}")
            await asyncio.sleep(0.05)
            order.append(f"end {decision.review_id}")
            return self.ack

    a1, a2 = message(review_id="a1"), message(review_id="a2")
    b1 = message(review_id="b1", study_id="other", workflow_id="study-other")
    results = await relay(Slow())._deliver_all([a1, b1, a2])

    assert [r.message_id for r in results] == [a1.id, b1.id, a2.id]
    assert order.index("end a1") < order.index("start a2")  # same study: sequential
    assert order.index("start b1") < order.index("end a1")  # other study: concurrent


# --------------------------------------------------------------------------- record


class Outbox:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def mark_delivered(self, message_id: uuid.UUID, *, last_error: str | None = None) -> None:
        self.calls.append(("delivered", last_error))

    async def mark_failed(self, message_id: uuid.UUID, error: str, retry_in: timedelta) -> None:
        self.calls.append(("failed", error, retry_in))

    async def mark_dead(self, message_id: uuid.UUID, error: str) -> None:
        self.calls.append(("dead", error))


@pytest.mark.parametrize(
    ("attempts", "outcome", "expected"),
    [
        (0, Outcome.DELIVERED, ("delivered", None)),
        (7, Outcome.REFUSED, ("delivered", "e")),  # final, whatever the attempts
        (0, Outcome.RETRY, ("failed", "e", timedelta(seconds=2))),
        (3, Outcome.RETRY, ("failed", "e", timedelta(seconds=16))),
        (MAX_ATTEMPTS - 2, Outcome.RETRY, ("failed", "e", timedelta(seconds=300))),
        (MAX_ATTEMPTS - 1, Outcome.RETRY, ("dead", "e")),  # the 50th failure
        (0, Outcome.DEAD, ("dead", "e")),
    ],
)
async def test_record(attempts: int, outcome: Outcome, expected: tuple[Any, ...]) -> None:
    outbox = Outbox()
    msg = message(attempts=attempts)
    error = None if outcome is Outcome.DELIVERED else "e"
    await relay(Gateway())._record(outbox, msg, Result(msg.id, outcome, error))  # type: ignore[arg-type]
    assert outbox.calls == [expected]


def test_limits_must_be_positive() -> None:
    for kwargs in ({"batch_size": 0}, {"max_attempts": 0}, {"poll_seconds": 0}):
        with pytest.raises(ValueError):
            relay(Gateway(), **kwargs)


# --------------------------------------------------------------------------- health


def test_healthy_window() -> None:
    r = relay(Gateway(), poll_seconds=2.0)
    assert healthy(r, started=100.0, now=150.0)  # starting: measured from start
    assert not healthy(r, started=100.0, now=161.0)
    r.last_poll = 200.0
    assert healthy(r, started=100.0, now=259.0)
    assert not healthy(r, started=100.0, now=261.0)
    slow = relay(Gateway(), poll_seconds=30.0)  # the window grows with the poll interval
    slow.last_poll = 0.0
    assert healthy(slow, started=0.0, now=299.0)


async def test_health_endpoint() -> None:
    r = relay(Gateway())
    server = await serve_health(r, port=0, host="127.0.0.1")
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            ok = await client.get("/health")
            assert (ok.status_code, ok.json()) == (200, {"status": "ok"})
            assert (await client.get("/metrics")).status_code == 404
            r.last_poll = -1e9  # long ago
            stale = await client.get("/health")
            assert (stale.status_code, stale.json()) == (503, {"status": "fail"})
    finally:
        server.close()
        await server.wait_closed()
