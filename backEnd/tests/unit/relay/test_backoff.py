"""Relay retry policy and pure helpers (S2_SPEC section 6), no database or server."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from basis.db.enums import Stage
from basis.relay.delivery import (
    BATCH_SIZE,
    DELIVERY_TIMEOUT_S,
    MAX_ATTEMPTS,
    MAX_BACKOFF_S,
    backoff,
    decision_from_payload,
    workflow_is_behind,
)
from basis.workflow.contracts import Decision, StageName, StudyStatusView

STUDY = "5f0c6a52-2f53-4c39-9a3e-2b0a8f1f7d10"


def test_spec_constants() -> None:
    assert (BATCH_SIZE, MAX_ATTEMPTS, DELIVERY_TIMEOUT_S, MAX_BACKOFF_S) == (20, 50, 30.0, 300)


@pytest.mark.parametrize(
    ("attempts", "seconds"),
    [(1, 2), (2, 4), (3, 8), (5, 32), (8, 256), (9, 300), (10, 300), (49, 300), (10**9, 300)],
)
def test_backoff_is_two_to_the_attempts_capped_at_300_s(attempts: int, seconds: int) -> None:
    assert backoff(attempts) == timedelta(seconds=seconds)


@pytest.mark.parametrize("attempts", [0, -1])
def test_backoff_needs_a_failed_attempt(attempts: int) -> None:
    with pytest.raises(ValueError):
        backoff(attempts)


def test_backoff_is_monotonic_and_total_wait_before_dead_is_bounded() -> None:
    delays = [backoff(n).total_seconds() for n in range(1, MAX_ATTEMPTS)]
    assert delays == sorted(delays)
    # 49 waits between the 50 attempts: 2+4+...+256 then 41 x 300 s, about 3.6 hours.
    assert sum(delays) == sum(2**n for n in range(1, 9)) + 41 * 300


def decision(stage: str = "rooms_review", revision: int = 2) -> Decision:
    return Decision.model_validate(
        {
            "review_id": "r1",
            "stage": stage,
            "revision": revision,
            "decision": "approve",
            "reviewer_id": "u1",
            "submitted_at": datetime(2026, 10, 15, tzinfo=UTC),
        }
    )


@pytest.mark.parametrize(
    ("stage", "revision", "behind"),
    [
        ("extracting", 2, True),  # same revision, earlier stage
        ("appraisal_review", 2, True),
        ("rooms_review", 2, False),  # caught up: deliver
        ("assets_processing", 2, False),  # ahead: deliver, the validator decides
        ("completed", 2, False),
        ("assets_review", 1, True),  # an older revision is behind whatever its stage
        ("uploading", 3, False),  # a newer revision is ahead whatever its stage
    ],
)
def test_workflow_is_behind(stage: str, revision: int, behind: bool) -> None:
    view = StudyStatusView.model_validate({"stage": stage, "revision": revision})
    assert workflow_is_behind(view, decision()) is behind


def test_contract_stage_names_are_the_database_stages_in_order() -> None:
    # workflow_is_behind orders contract stage names by the database enum's order.
    assert list(get_args(StageName)) == [stage.value for stage in Stage]


PAYLOAD = {
    "study_id": STUDY,
    "workflow_id": f"study-{STUDY}",
    "review_id": "11111111-1111-1111-1111-111111111111",
    "stage": "appraisal_review",
    "revision": 1,
    "decision": "reject",
    "to_stage": "extracting",
    "new_revision": 2,
    "reviewer_id": "22222222-2222-2222-2222-222222222222",
    "submitted_at": "2026-10-15T17:02:11.204000+00:00",
    "corrections": [
        {
            "entity_type": "extraction_field",
            "entity_id": "33333333-3333-3333-3333-333333333333",
            "after": {"year_built": 1998},
            "reason": None,
        }
    ],
}


def test_decision_from_payload() -> None:
    workflow_id, built = decision_from_payload(PAYLOAD)
    assert workflow_id == f"study-{STUDY}"
    assert built.review_id == PAYLOAD["review_id"]
    assert (built.stage, built.revision, built.decision) == ("appraisal_review", 1, "reject")
    assert built.submitted_at == datetime(2026, 10, 15, 17, 2, 11, 204000, tzinfo=UTC)
    assert built.corrections[0].after == {"year_built": 1998}


def test_decision_from_payload_without_workflow_id_uses_the_study_id() -> None:
    workflow_id, _ = decision_from_payload({**PAYLOAD, "workflow_id": None})
    assert workflow_id == f"study-{STUDY}"


@pytest.mark.parametrize("missing", ["review_id", "stage", "reviewer_id", "submitted_at"])
def test_decision_from_payload_rejects_a_missing_field(missing: str) -> None:
    payload = {key: value for key, value in PAYLOAD.items() if key != missing}
    with pytest.raises(KeyError):
        decision_from_payload(payload)


@pytest.mark.parametrize(
    "override",
    [{"stage": "extracting"}, {"decision": "maybe"}, {"revision": 0}, {"corrections": [{}]}],
)
def test_decision_from_payload_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # pydantic.ValidationError is a ValueError
        decision_from_payload({**PAYLOAD, **override})
