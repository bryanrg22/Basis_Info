"""``POST /v1/studies/{id}/decisions`` (S1_SPEC section 5, BUILD_PLAN 6.4).

One transaction records the review, corrections, the stage transition and a
``workflow_signal`` outbox row; the answer is 202. Wrong stage or stale revision is 409.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import pytest
from apitest_support import Api, Seed
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import IDEMPOTENCY_HEADER, REPLAYED_HEADER
from basis.api.schemas.common import MAX_REVISION
from basis.db.enums import (
    REVIEW_STAGES,
    Decision,
    DocumentKind,
    OrgRole,
    Stage,
    StudyStatus,
    status_for_stage,
)
from basis.db.models import Correction, Document, OutboxMessage, Review, Study
from basis.db.repositories import DECISION_TRANSITIONS, WORKFLOW_SIGNAL, scoped_key
from basis.db.repositories.review import CORRECTION_TARGET_TYPES
from basis.db.session import session_scope
from basis.workflow.contracts import CORRECTION_TYPES

pytestmark = pytest.mark.integration


async def counts(sm: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    async with sm() as session:
        reviews = await session.scalar(select(func.count()).select_from(Review))
        corrections = await session.scalar(select(func.count()).select_from(Correction))
        outbox = await session.scalar(select(func.count()).select_from(OutboxMessage))
    return int(reviews or 0), int(corrections or 0), int(outbox or 0)


async def study_at(
    api: Api, seed: Seed, stage: Stage, revision: int = 1, uid: str = "alice"
) -> str:
    study = await api.create_study(uid)
    await seed.stage(study["id"], stage, revision)
    return str(study["id"])


def decision(
    stage: Stage | str, revision: int, verdict: Decision | str, **extra: Any
) -> dict[str, Any]:
    return {"stage": str(stage), "revision": revision, "decision": str(verdict), **extra}


@pytest.mark.parametrize(
    ("stage", "verdict", "to_stage"),
    [(stage, verdict, to) for (stage, verdict), to in DECISION_TRANSITIONS.items()],
    ids=[f"{s.value}-{d.value}" for (s, d) in DECISION_TRANSITIONS],
)
async def test_each_transition(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: Stage,
    verdict: Decision,
    to_stage: Stage,
) -> None:
    sid = await study_at(api, seed, stage, revision=3)
    key = str(uuid.uuid4())

    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(stage, 3, verdict), key=key
    )

    assert response.status_code == 202, response.text
    body = response.json()
    expected_revision = 4 if verdict is Decision.REJECT else 3
    assert body["current_stage"] == to_stage.value
    assert body["revision"] == expected_revision
    assert body["corrections_recorded"] == 0
    review = body["review"]
    assert (review["stage"], review["revision"], review["decision"]) == (
        stage.value,
        3,
        verdict.value,
    )
    alice = await seed.user("alice")
    assert review["reviewer_id"] == str(alice.id)
    assert review["study_id"] == sid

    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(sid))
        row = await session.get(Review, uuid.UUID(review["id"]))
        (signal,) = (await session.scalars(select(OutboxMessage))).all()
    assert study is not None and (study.current_stage, study.revision) == (
        to_stage,
        expected_revision,
    )
    # v0.9: landing on a review stage, the study is processing until the workflow opens it.
    assert study.status is status_for_stage(to_stage) or (
        to_stage in REVIEW_STAGES and study.status is StudyStatus.PROCESSING
    )
    assert row is not None and row.idempotency_key == scoped_key(alice.id, key)
    assert signal.kind == WORKFLOW_SIGNAL
    assert signal.delivered_at is None
    payload = dict(signal.payload)
    assert datetime.fromisoformat(payload.pop("submitted_at")) == row.created_at
    assert payload == {
        "study_id": sid,
        "workflow_id": None,
        "review_id": review["id"],
        "stage": stage.value,
        "revision": 3,
        "decision": verdict.value,
        "to_stage": to_stage.value,
        "new_revision": expected_revision,
        "reviewer_id": str(alice.id),
        "corrections": [],
    }


async def test_decision_for_a_stage_the_study_is_not_at_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)

    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision("rooms_review", 1, "approve")
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["type"].endswith("/stale-state")
    assert await counts(sessionmaker) == (0, 0, 0)
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(sid))
    assert study is not None and study.current_stage is Stage.APPRAISAL_REVIEW


async def test_stale_revision_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW, revision=2)

    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision("appraisal_review", 1, "approve")
    )

    assert response.status_code == 409
    assert await counts(sessionmaker) == (0, 0, 0)


async def test_a_second_decision_on_the_same_revision_is_409(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    path = f"/v1/studies/{sid}/decisions"
    first = await api.post("alice", path, decision("appraisal_review", 1, "approve"))
    second = await api.post("alice", path, decision("appraisal_review", 1, "reject"))
    assert (first.status_code, second.status_code) == (202, 409)


@pytest.mark.parametrize(
    "stage", [Stage.UPLOADING, Stage.EXTRACTING, Stage.ASSETS_PROCESSING, Stage.EXPORTING]
)
async def test_study_not_waiting_for_review_is_409(api: Api, seed: Seed, stage: Stage) -> None:
    sid = await study_at(api, seed, stage)
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision("appraisal_review", 1, "approve")
    )
    assert response.status_code == 409


@pytest.mark.parametrize("status", [StudyStatus.PROCESSING, StudyStatus.FAILED])
async def test_a_gate_the_workflow_has_not_opened_is_409(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    status: StudyStatus,
) -> None:
    """EVAL_REPORT 26: the study is at rooms_review (the appraisal was approved) but the
    photo stage still runs, so the workflow has not opened that gate: no decision."""
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW)
    async with session_scope(sessionmaker) as session:
        await session.execute(update(Study).where(Study.id == uuid.UUID(sid)).values(status=status))
    before = await counts(sessionmaker)
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(Stage.ROOMS_REVIEW, 1, "approve")
    )
    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("/gate-not-open")
    assert f"status {status.value}" in body["detail"]
    assert await counts(sessionmaker) == before  # nothing recorded


async def test_a_stalled_gate_still_takes_a_decision(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == uuid.UUID(sid)).values(status=StudyStatus.STALLED)
        )
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(Stage.ASSETS_REVIEW, 1, "approve")
    )
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("stage", ["extracting", "uploading", "completed", "nope"])
async def test_non_review_stage_in_the_body_is_422(api: Api, seed: Seed, stage: str) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(stage, 1, "approve")
    )
    assert response.status_code == 422


async def test_archived_study_cannot_be_decided(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision("appraisal_review", 1, "approve")
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("role", "expected"), [(OrgRole.VIEWER, 403), (OrgRole.ENGINEER, 202), (OrgRole.OWNER, 202)]
)
async def test_decisions_need_the_engineer_role(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    role: OrgRole,
    expected: int,
) -> None:
    org = await seed.org()
    await seed.member(org, "carol", OrgRole.OWNER)
    if role is not OrgRole.OWNER:
        await seed.member(org, "alice", role)
    uid = "carol" if role is OrgRole.OWNER else "alice"
    study = await api.create_study("carol", org_id=str(org))
    await seed.stage(study["id"], Stage.APPRAISAL_REVIEW)

    response = await api.post(
        uid, f"/v1/studies/{study['id']}/decisions", decision("appraisal_review", 1, "approve")
    )

    assert response.status_code == expected
    if expected == 403:
        assert await counts(sessionmaker) == (0, 0, 0)


async def test_corrections_are_recorded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)  # corrections must name real rows (S2-C)
    field_id, room_id = uuid.UUID(targets["component"]), uuid.UUID(targets["room"])
    corrections = [
        {
            "entity_type": "component",
            "entity_id": str(field_id),
            "before": {"year_built": 1989},
            "after": {"year_built": 1998},
            "reason": "Deed shows 1998.",
        },
        {
            "entity_type": "room",
            "entity_id": str(room_id),
            "after": {"room_type": "kitchen", "tags": ["wet", "commercial"], "area": 12.5},
        },
    ]

    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 2, "reject", comment="Rooms mislabeled", corrections=corrections),
    )

    assert response.status_code == 202, response.text
    assert response.json()["corrections_recorded"] == 2
    assert response.json()["review"]["comment"] == "Rooms mislabeled"
    alice = await seed.user("alice")
    async with sessionmaker() as session:
        rows = (await session.scalars(select(Correction).order_by(Correction.entity_type))).all()
    by_type = {row.entity_type: row for row in rows}
    field, room = by_type["component"], by_type["room"]
    assert (field.entity_id, field.before, field.after, field.reason) == (
        field_id,
        {"year_built": 1989},
        {"year_built": 1998},
        "Deed shows 1998.",
    )
    assert (room.entity_id, room.before, room.reason) == (room_id, None, None)
    assert room.after == {"room_type": "kitchen", "tags": ["wet", "commercial"], "area": 12.5}
    assert {row.revision for row in rows} == {2}
    assert {row.reviewer_id for row in rows} == {alice.id}
    assert {str(row.study_id) for row in rows} == {sid}


@pytest.mark.parametrize(
    "correction",
    [
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1))},  # after missing
        {"entity_type": "room", "entity_id": "x", "after": {"a": 1}},
        {"entity_type": "Room Type", "entity_id": str(uuid.UUID(int=1)), "after": {"a": 1}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {"a": {"b": 1}}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {"a": "x\x00"}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": None},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {"bad key": 1}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {"name\n": 1}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {"x" * 65: 1}},
        {"entity_type": "room", "entity_id": str(uuid.UUID(int=1)), "after": {}, "x": 1},
    ],
)
async def test_invalid_corrections_are_422_and_record_nothing(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    correction: dict[str, Any],
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("appraisal_review", 1, "approve", corrections=[correction]),
    )
    assert response.status_code == 422
    assert await counts(sessionmaker) == (0, 0, 0)


async def test_non_finite_numbers_in_corrections_are_422(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    body = decision(
        "appraisal_review",
        1,
        "approve",
        corrections=[{"entity_type": "room", "entity_id": str(uuid.uuid4()), "after": {"a": 1}}],
    )
    raw = json.dumps(body).replace('"a": 1', '"a": NaN')
    response = await api.client.post(
        f"/v1/studies/{sid}/decisions",
        content=raw,
        headers={
            "Authorization": "Bearer uid:alice",
            "Content-Type": "application/json",
            IDEMPOTENCY_HEADER: str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("revision", [0, -1, 2**31, 2**31 - 1, "1", True, 1.5])
@pytest.mark.parametrize("verdict", ["approve", "reject"])
async def test_revision_must_be_a_positive_int32_below_the_maximum(
    api: Api, seed: Seed, revision: Any, verdict: str
) -> None:
    """2**31 - 1 is refused too: a rejection stores revision + 1 in an INT column.

    Regression (found by the Schemathesis contract test): a rejection at 2**31 - 1
    overflowed the transition's bind parameter and answered 500.
    """
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    body = {"stage": "appraisal_review", "revision": revision, "decision": verdict}
    assert (await api.post("alice", f"/v1/studies/{sid}/decisions", body)).status_code == 422


async def test_rejection_at_the_largest_revision_reaches_the_int32_maximum(
    api: Api, seed: Seed
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW, revision=MAX_REVISION)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("appraisal_review", MAX_REVISION, "reject"),
    )
    assert response.status_code == 202
    assert response.json()["revision"] == 2**31 - 1


async def test_integral_float_revision_is_accepted(api: Api, seed: Seed) -> None:
    """JSON Schema counts 1.0 as an integer, so the server does too."""
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    body = {"stage": "appraisal_review", "revision": 1.0, "decision": "approve"}
    assert (await api.post("alice", f"/v1/studies/{sid}/decisions", body)).status_code == 202


async def test_retry_with_the_same_key_replays_the_202(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    path, key = f"/v1/studies/{sid}/decisions", str(uuid.uuid4())
    body = decision("appraisal_review", 1, "approve")

    first = await api.post("alice", path, body, key=key)
    second = await api.post("alice", path, body, key=key)

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.content == first.content
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert await counts(sessionmaker) == (1, 0, 1)


async def test_same_key_with_a_different_body_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    path, key = f"/v1/studies/{sid}/decisions", str(uuid.uuid4())
    await api.post("alice", path, decision("appraisal_review", 1, "approve"), key=key)

    response = await api.post("alice", path, decision("appraisal_review", 1, "reject"), key=key)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/idempotency-key-reused")
    assert await counts(sessionmaker) == (1, 0, 1)


async def test_missing_idempotency_key_is_400(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    response = await api.client.post(
        f"/v1/studies/{sid}/decisions",
        json=decision("appraisal_review", 1, "approve"),
        headers={"Authorization": "Bearer uid:alice"},
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("/idempotency-key-missing")


# --------------------------------------------------------------------------- S2-C
# Correction targets: the API refuses what the workflow's validator or apply_corrections
# would refuse, naming the offending index (``basis.api.routes.decisions``).

STAGE_TYPES = sorted(
    (stage, entity_type) for stage, types in CORRECTION_TYPES.items() for entity_type in types
)


def correction(entity_type: str, entity_id: str) -> dict[str, Any]:
    return {"entity_type": entity_type, "entity_id": entity_id, "after": {"name": "Kitchen"}}


async def assert_nothing_written(
    sessionmaker: async_sessionmaker[AsyncSession], sid: str, stage: Stage, revision: int
) -> None:
    assert await counts(sessionmaker) == (0, 0, 0)
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(sid))
    assert study is not None and (study.current_stage, study.revision) == (stage, revision)


def test_target_types_cover_the_contract() -> None:
    every_type: set[str] = set().union(*CORRECTION_TYPES.values())
    assert every_type == CORRECTION_TARGET_TYPES


@pytest.mark.parametrize(("stage", "entity_type"), STAGE_TYPES)
async def test_a_real_target_of_an_accepted_type_is_202(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage), revision=2)
    targets = await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 2, "reject", corrections=[correction(entity_type, targets[entity_type])]),
    )
    assert response.status_code == 202, response.text
    assert response.json()["corrections_recorded"] == 1
    async with sessionmaker() as session:
        (row,) = (await session.scalars(select(Correction))).all()
    assert (row.entity_type, str(row.entity_id), row.revision) == (
        entity_type,
        targets[entity_type],
        2,
    )


@pytest.mark.parametrize(
    ("stage", "entity_type"),
    [
        (stage, entity_type)
        for stage, allowed in sorted(CORRECTION_TYPES.items())
        for entity_type in [*sorted(CORRECTION_TARGET_TYPES - allowed), "widget"]
    ],
)
async def test_a_type_the_stage_does_not_accept_is_422_with_its_index(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage))
    targets = await seed.outputs(sid)
    good_type = sorted(CORRECTION_TYPES[stage])[0]
    good = correction(good_type, targets[good_type])
    bad = correction(entity_type, targets.get(entity_type, str(uuid.uuid4())))

    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 1, "reject", corrections=[good, bad, good]),
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["type"].endswith("/validation")
    assert [e["loc"] for e in body["errors"]] == [["body", "corrections", 1, "entity_type"]]
    assert body["errors"][0]["type"] == "correction_type"
    assert stage in body["errors"][0]["detail"] and entity_type in body["errors"][0]["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 1)


async def test_unknown_targets_are_422_naming_every_offending_index(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW)
    targets = await seed.outputs(sid)
    unknown = str(uuid.uuid4())
    corrections = [
        correction("room", targets["room"]),
        correction("component", unknown),
        correction("photo", targets["photo"]),
        correction("room", unknown),
    ]

    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=corrections),
    )

    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert [e["loc"] for e in errors] == [
        ["body", "corrections", 1, "entity_id"],
        ["body", "corrections", 3, "entity_id"],
    ]
    assert {e["type"] for e in errors} == {"correction_target"}
    assert unknown in errors[0]["detail"] and "revision 1" in errors[0]["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 1)


@pytest.mark.parametrize(("stage", "entity_type"), STAGE_TYPES)
async def test_a_target_of_another_study_is_422(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage))
    other = await study_at(api, seed, Stage(stage))
    await seed.outputs(sid)
    theirs = await seed.outputs(other)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 1, "reject", corrections=[correction(entity_type, theirs[entity_type])]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == ["body", "corrections", 0, "entity_id"]
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 1)


@pytest.mark.parametrize("entity_type", ["extraction_field", "room", "component", "asset"])
async def test_a_target_of_an_earlier_revision_is_422(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    entity_type: str,
) -> None:
    """Revisioned outputs must belong to the decided revision, as apply_corrections checks."""
    stage = next(s for s, types in CORRECTION_TYPES.items() if entity_type in types)
    sid = await study_at(api, seed, Stage(stage), revision=2)
    old = await seed.outputs(sid, revision=1)
    await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 2, "reject", corrections=[correction(entity_type, old[entity_type])]),
    )
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 2)


async def test_a_photo_correction_must_name_a_photo(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW)
    await seed.outputs(sid)
    async with sessionmaker() as session:
        (appraisal,) = (
            await session.scalars(
                select(Document.id).where(
                    Document.study_id == uuid.UUID(sid), Document.kind == DocumentKind.APPRAISAL
                )
            )
        ).all()
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[correction("photo", str(appraisal))]),
    )
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 1)


async def test_a_stale_decision_with_unknown_targets_is_still_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The compare-and-set runs before the existence check: stale wins over invalid."""
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[correction("room", str(uuid.uuid4()))]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("/stale-state")
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 2)


async def test_a_refused_correction_does_not_consume_the_idempotency_key(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Validation failures are not stored: the same key with a fixed body is accepted."""
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    path, key = f"/v1/studies/{sid}/decisions", str(uuid.uuid4())
    wrong_type = decision(
        "appraisal_review", 1, "reject", corrections=[correction("room", targets["room"])]
    )
    missing = decision(
        "appraisal_review",
        1,
        "reject",
        corrections=[correction("extraction_field", str(uuid.uuid4()))],
    )
    fixed = decision(
        "appraisal_review",
        1,
        "reject",
        corrections=[correction("extraction_field", targets["extraction_field"])],
    )

    assert (await api.post("alice", path, wrong_type, key=key)).status_code == 422
    assert (await api.post("alice", path, missing, key=key)).status_code == 422
    accepted = await api.post("alice", path, fixed, key=key)

    assert accepted.status_code == 202, accepted.text
    assert REPLAYED_HEADER not in accepted.headers
    assert await counts(sessionmaker) == (1, 1, 1)
    replay = await api.post("alice", path, fixed, key=key)
    assert replay.status_code == 202 and replay.headers[REPLAYED_HEADER] == "true"
