"""The workflow's wire contract (S2_SPEC section 2).

Field names are shared with the API and the outbox relay (S2-B) and are recorded in every
workflow history, so renaming one is a breaking change: this test pins them. Additive,
defaulted fields are allowed (old payloads still validate); the pins say which exist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError
from temporalio.contrib.pydantic import pydantic_data_converter

from basis.db.enums import REVIEW_STAGES, Stage
from basis.workflow import contracts as c


def fields(model: type[BaseModel]) -> list[str]:
    return list(model.model_fields)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            c.StudyWorkflowInput,
            [
                "study_id",
                "org_id",
                "input_revision",
                "workflow_version",
                "budget_usd",
                "photo_concurrency",
                "localize_concurrency",
                "rooms_gate_waits_for_localization",
                "components_gate",  # v0.12 (patch components-gate-v1): additive, default True
                "review_reminder_days",
                "review_deadline_days",
                "simulate_photos",
            ],
        ),
        (
            c.Decision,
            [
                "review_id",
                "stage",
                "revision",
                "decision",
                "reviewer_id",
                "corrections",
                "submitted_at",
            ],
        ),
        (c.DecisionAck, ["accepted", "applied_revision", "reason"]),
        (c.CorrectionInput, ["entity_type", "entity_id", "after", "reason"]),
        (
            c.StudyStatusView,
            [
                "stage",
                "revision",
                "waiting_since",
                "pending_photos",
                "completed_photos",
                "decisions_applied",
                "stalled",
                "budget_remaining_usd",
                "awaiting",
                "flagged_photos",
                "needs_review_stages",
                "failed_stage",  # v0.9 (EVAL_REPORT 27): additive, default None
                "failure",
                "failure_code",  # error codes: additive, default None
            ],
        ),
        (
            c.TransitionInput,
            ["study_id", "org_id", "from_stage", "to_stage", "revision", "from_revision"],
        ),
    ],
)
def test_field_names_are_pinned(model: type[BaseModel], expected: list[str]) -> None:
    assert fields(model) == expected


def test_stage_vocabulary_matches_the_database_enum() -> None:
    assert set(get_args(c.StageName)) == {stage.value for stage in Stage}
    assert set(get_args(c.ReviewStage)) == {stage.value for stage in REVIEW_STAGES}
    assert set(c.CORRECTION_TYPES) == set(get_args(c.ReviewStage))


def test_correction_types_per_gate() -> None:
    """v0.12 (patch components-gate-v1): the rooms review takes the grouping and the
    components review the components; without the gate the single rooms review takes both."""
    gate = {s: c.correction_types(s, components_gate=True) for s in c.CORRECTION_TYPES}
    assert gate == {
        "appraisal_review": {"extraction_field"},
        "rooms_review": {"room", "photo_room"},
        "components_review": {"component", "room_component", "photo"},
        "assets_review": {"asset"},
    }
    single = {s: c.correction_types(s, components_gate=False) for s in c.CORRECTION_TYPES}
    assert single == {**gate, "rooms_review": {"room", "component", "room_component", "photo"}}
    assert c.SINGLE_ROOMS_GATE_TYPES == single["rooms_review"]
    with pytest.raises(KeyError):
        c.correction_types("extracting", components_gate=True)


def test_old_payloads_take_the_flow_defaults() -> None:
    """Payloads recorded before the patch lack the new fields: a workflow input defaults to
    the components gate (the replayed patch marker decides), every activity input to the
    flow before it."""
    workflow_input = c.StudyWorkflowInput(
        study_id="s", org_id="o", input_revision=1, workflow_version="v", budget_usd=1.0
    )
    assert workflow_input.components_gate is True
    run_start = c.RunStartInput(
        study_id="s", workflow_id="w", run_id="r", workflow_version="v", input_revision=1
    )
    assert run_start.components_gate is False
    apply = c.ApplyCorrectionsInput(
        study_id="s", stage="rooms_review", from_revision=1, to_revision=2, corrections=()
    )
    assert apply.components_gate is False
    analyze = c.AnalyzePhotoInput(
        study_id="s", document_id="d", revision=1, idempotency_key="k", budget_remaining_usd=1
    )
    assert analyze.keep_room is False
    rooms = c.ReconcileRoomsInput(
        study_id="s", revision=1, idempotency_key="k", budget_remaining_usd=1
    )
    assert rooms.vote_only is False


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    ack = c.DecisionAck(accepted=True, applied_revision=1)
    with pytest.raises(ValidationError):
        ack.accepted = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        c.DecisionAck(accepted=True, applied_revision=1, extra="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_revision": 0},
        {"budget_usd": 0},
        {"photo_concurrency": 0},
        {"photo_concurrency": 33},
        {"review_reminder_days": 0},
        {"review_deadline_days": 0},
    ],
)
def test_workflow_input_bounds(overrides: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "study_id": "s",
        "org_id": "o",
        "input_revision": 1,
        "workflow_version": "v",
        "budget_usd": 1.0,
    }
    with pytest.raises(ValidationError):
        c.StudyWorkflowInput(**{**values, **overrides})


def test_a_payload_without_the_additive_fields_still_validates() -> None:
    old = {"study_id": "s", "org_id": "o", "input_revision": 1, "workflow_version": "v"}
    parsed = c.StudyWorkflowInput.model_validate({**old, "budget_usd": 5})
    assert parsed.simulate_photos == {} and parsed.photo_concurrency == 5
    # EVAL_REPORT 30: an input recorded before the SAM 3 fields gets their defaults (the
    # workflow uses them only under patch localize-background-v1).
    assert (parsed.localize_concurrency, parsed.rooms_gate_waits_for_localization) == (3, False)
    transition = c.TransitionInput(
        study_id="s", org_id="o", from_stage="extracting", to_stage="appraisal_review", revision=1
    )
    assert transition.from_revision is None


def test_decision_round_trips_through_the_pydantic_data_converter() -> None:
    decision = c.Decision(
        review_id=str(uuid.uuid4()),
        stage="rooms_review",
        revision=2,
        decision="reject",
        reviewer_id=str(uuid.uuid4()),
        corrections=(
            c.CorrectionInput(
                entity_type="room", entity_id=str(uuid.uuid4()), after={"name": "Lobby"}
            ),
        ),
        submitted_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    )
    converter = pydantic_data_converter.payload_converter
    [payload] = converter.to_payloads([decision])
    [back] = converter.from_payloads([payload], [c.Decision])
    assert back == decision


def test_decision_rejects_unknown_stage_and_kind() -> None:
    base: dict[str, Any] = {
        "review_id": "r",
        "stage": "appraisal_review",
        "revision": 1,
        "decision": "approve",
        "reviewer_id": "u",
        "submitted_at": datetime.now(UTC),
    }
    c.Decision(**base)
    with pytest.raises(ValidationError):
        c.Decision(**{**base, "stage": "exporting"})
    with pytest.raises(ValidationError):
        c.Decision(**{**base, "decision": "maybe"})
    with pytest.raises(ValidationError):
        c.Decision(**{**base, "revision": 0})


def test_simulate_fault_defaults_do_nothing() -> None:
    fault = c.SimulateFault()
    assert (fault.fail_attempts, fault.sleep_seconds, fault.fail_permanently) == (0, 0.0, False)
