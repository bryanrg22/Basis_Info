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

from basis.activities.reprice_corrections import STUDY_TARGETED
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
from basis.db.models import (
    Component,
    Correction,
    Document,
    Extraction,
    ExtractionField,
    OutboxMessage,
    PhotoRoom,
    Review,
    Study,
)
from basis.db.repositories import (
    DECISION_TRANSITIONS,
    SINGLE_ROOMS_GATE_TRANSITIONS,
    WORKFLOW_SIGNAL,
    scoped_key,
)
from basis.db.repositories.review import CORRECTION_TARGET_TYPES
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.vision.rooms import ROOM_INVENTORY_SOURCE
from basis.workflow.contracts import CORRECTION_TYPES, SINGLE_ROOMS_GATE_TYPES, correction_types

pytestmark = pytest.mark.integration


async def counts(sm: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    async with sm() as session:
        reviews = await session.scalar(select(func.count()).select_from(Review))
        corrections = await session.scalar(select(func.count()).select_from(Correction))
        outbox = await session.scalar(select(func.count()).select_from(OutboxMessage))
    return int(reviews or 0), int(corrections or 0), int(outbox or 0)


async def study_at(
    api: Api,
    seed: Seed,
    stage: Stage,
    revision: int = 1,
    uid: str = "alice",
    *,
    components_gate: bool | None = None,
) -> str:
    study = await api.create_study(uid)
    await seed.stage(study["id"], stage, revision, components_gate=components_gate)
    return str(study["id"])


def decision(
    stage: Stage | str, revision: int, verdict: Decision | str, **extra: Any
) -> dict[str, Any]:
    return {"stage": str(stage), "revision": revision, "decision": str(verdict), **extra}


@pytest.mark.parametrize(
    ("components_gate", "stage", "verdict", "to_stage"),
    [(True, stage, verdict, to) for (stage, verdict), to in DECISION_TRANSITIONS.items()]
    + [
        (gate, stage, verdict, to)
        for (stage, verdict), to in SINGLE_ROOMS_GATE_TRANSITIONS.items()
        if stage in (Stage.ROOMS_REVIEW, Stage.ASSETS_REVIEW)
        for gate in (False, None)
    ],
    ids=[f"{s.value}-{d.value}" for (s, d) in DECISION_TRANSITIONS]
    + [
        f"single_gate_{'null' if gate is None else 'false'}-{s.value}-{d.value}"
        for (s, d) in SINGLE_ROOMS_GATE_TRANSITIONS
        if s in (Stage.ROOMS_REVIEW, Stage.ASSETS_REVIEW)
        for gate in (False, None)
    ],
)
async def test_each_transition(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    components_gate: bool | None,
    stage: Stage,
    verdict: Decision,
    to_stage: Stage,
) -> None:
    """Every transition of the study's flow: with the components gate (patch
    components-gate-v1), and for a study whose workflow has the single rooms review
    (``components_gate`` false, or null: the API never moves such a study to a components
    stage)."""
    sid = await study_at(api, seed, stage, revision=3, components_gate=components_gate)
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
    """EVAL_REPORT 27: the study is at rooms_review (the appraisal was approved) but the
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

# The assets rebuild's types (``reprice``, ``structure_model``) are accepted by the workflow
# but recorded only by ``POST .../assets/reprice`` (tests/api/test_reprice.py), so the
# decisions route refuses them.
STAGE_TYPES = sorted(
    (stage, entity_type)
    for stage, types in CORRECTION_TYPES.items()
    for entity_type in types
    if entity_type not in STUDY_TARGETED
)
# v0.12: the types above are those of a study with the components gate (patch
# components-gate-v1, ``studies.components_gate``); these tests seed it so.
GATE = True


# An added appraisal field the seeded extraction lacks (it has subject.year_built only).
ADDED_FIELD: dict[str, Any] = {
    "key": "improvements.year_built",
    "value_num": 1987,
    "page": 3,
    "evidence": "Year Built 1987",
}


def correction(entity_type: str, entity_id: str, room_id: str | None = None) -> dict[str, Any]:
    # A room_component's values are checked (basis.domain.counts): it adds a real component;
    # a photo_room's is a room of the revision (``room_id``, else a random one); a removal is
    # ``{}`` with a reason, an added field a catalogue key with its value, page and evidence.
    if entity_type == "room_component":
        after: dict[str, Any] = {"label": "window", "count": 2}
    elif entity_type == "photo_room":
        after = {"room_id": room_id or str(uuid.uuid4())}
    elif entity_type == "component_remove":
        after = {}
    elif entity_type == "extraction_field_add":
        after = dict(ADDED_FIELD)
    else:
        after = {"name": "Kitchen"}
    one: dict[str, Any] = {"entity_type": entity_type, "entity_id": entity_id, "after": after}
    if entity_type in ("component_remove", "extraction_field_add"):
        one["reason"] = "Not in the property."
    return one


async def assert_nothing_written(
    sessionmaker: async_sessionmaker[AsyncSession], sid: str, stage: Stage, revision: int
) -> None:
    assert await counts(sessionmaker) == (0, 0, 0)
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(sid))
    assert study is not None and (study.current_stage, study.revision) == (stage, revision)


def test_target_types_cover_the_contract() -> None:
    every_type: set[str] = set().union(*CORRECTION_TYPES.values(), SINGLE_ROOMS_GATE_TYPES)
    assert every_type - STUDY_TARGETED == CORRECTION_TARGET_TYPES


@pytest.mark.parametrize(
    ("components_gate", "stage", "entity_type"),
    [(GATE, stage, entity_type) for stage, entity_type in STAGE_TYPES]
    + [(False, "rooms_review", entity_type) for entity_type in sorted(SINGLE_ROOMS_GATE_TYPES)],
)
async def test_a_real_target_of_an_accepted_type_is_202(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    components_gate: bool,
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage), revision=2, components_gate=components_gate)
    targets = await seed.outputs(sid, revision=2)
    one = correction(entity_type, targets[entity_type], room_id=targets["room"])
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(stage, 2, "reject", corrections=[one])
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
    ("components_gate", "stage", "entity_type"),
    [
        (GATE, stage, entity_type)
        for stage, allowed in sorted(CORRECTION_TYPES.items())
        for entity_type in [*sorted(CORRECTION_TARGET_TYPES - allowed), "widget"]
    ]
    + [
        (False, "rooms_review", entity_type)
        for entity_type in [*sorted(CORRECTION_TARGET_TYPES - SINGLE_ROOMS_GATE_TYPES), "widget"]
    ],
)
async def test_a_type_the_stage_does_not_accept_is_422_with_its_index(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    components_gate: bool,
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage), components_gate=components_gate)
    targets = await seed.outputs(sid)
    good_type = sorted(correction_types(stage, components_gate=components_gate))[0]
    good = correction(good_type, targets[good_type], room_id=targets["room"])
    bad = correction(
        entity_type, targets.get(entity_type, str(uuid.uuid4())), room_id=targets["room"]
    )

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
    sid = await study_at(api, seed, Stage(stage), components_gate=GATE)
    other = await study_at(api, seed, Stage(stage), components_gate=GATE)
    ours = await seed.outputs(sid)
    theirs = await seed.outputs(other)
    one = correction(entity_type, theirs[entity_type], room_id=ours["room"])
    response = await api.post(
        "alice", f"/v1/studies/{sid}/decisions", decision(stage, 1, "reject", corrections=[one])
    )
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == ["body", "corrections", 0, "entity_id"]
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 1)


@pytest.mark.parametrize(
    "entity_type",
    [
        "extraction_field",
        "extraction_field_add",
        "room",
        "component",
        "component_remove",
        "asset",
    ],
)
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


# --------------------------------------------------------------------------- counts


@pytest.mark.parametrize(
    ("entity_type", "after", "message"),
    [
        ("component", {"count": 0}, "whole number from 1"),
        ("component", {"count": True}, "whole number from 1"),
        ("component", {"count": 2.5}, "whole number from 1"),
        ("room_component", {"label": "skylight", "count": 1}, "unknown component label"),
        ("room_component", {"label": "window"}, "needs count"),
        ("room_component", {"label": "window", "count": 1, "material": "oak"}, "material"),
    ],
)
async def test_a_count_the_rooms_review_would_refuse_is_422_before_anything_is_written(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    entity_type: str,
    after: dict[str, Any],
    message: str,
) -> None:
    """apply_corrections refusing one correction would start the next revision with none
    of them; the API refuses the bad value instead, located at the correction's ``after``."""
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    good = {"entity_type": "room", "entity_id": targets["room"], "after": {"name": "Galley"}}
    bad = {"entity_type": entity_type, "entity_id": targets[entity_type], "after": after}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 2, "reject", corrections=[good, bad]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["loc"] == ["body", "corrections", 1, "after"]
    assert message in error["detail"] and error["type"] == "correction_value"
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 2)


async def test_a_count_and_an_added_component_are_recorded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    corrections = [
        {"entity_type": "component", "entity_id": targets["component"], "after": {"count": 7}},
        {
            "entity_type": "room_component",
            "entity_id": targets["room"],
            "after": {"label": "electrical_outlet", "count": 4, "attachment": "permanent"},
        },
    ]
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 2, "reject", corrections=corrections),
    )
    assert response.status_code == 202, response.text
    assert response.json()["corrections_recorded"] == 2
    # An approval still cannot carry them: reject, then approve the new revision.
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    approve = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "rooms_review",
            2,
            "approve",
            corrections=[
                {
                    "entity_type": "room_component",
                    "entity_id": targets["room"],
                    "after": {"label": "window", "count": 1},
                }
            ],
        ),
    )
    assert approve.status_code == 422


# --------------------------------------------------------------------------- two rooms gates
# v0.12 (patch components-gate-v1): the rooms review takes the grouping (room fields,
# merges, photo moves), the components review the components, counts and re-analysis.


async def test_a_move_and_a_rename_at_the_rooms_review_are_recorded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2, components_gate=True)
    targets = await seed.outputs(sid, revision=2)
    move = {
        "entity_type": "photo_room",
        "entity_id": targets["photo"],
        "before": {"room_id": None},
        "after": {"room_id": targets["room"]},
    }
    rename = {"entity_type": "room", "entity_id": targets["room"], "after": {"name": "Galley"}}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 2, "reject", corrections=[move, rename]),
    )
    assert response.status_code == 202, response.text
    assert (response.json()["current_stage"], response.json()["revision"]) == ("extracting", 3)
    async with sessionmaker() as session:
        (signal,) = (await session.scalars(select(OutboxMessage))).all()
    assert signal.payload["corrections"][0] == {
        "entity_type": "photo_room",
        "entity_id": targets["photo"],
        "after": {"room_id": targets["room"]},
        "reason": None,
    }


@pytest.mark.parametrize(
    "after",
    [
        {"room_id": "not-a-room"},
        {"room_id": 7},
        {"room_id": None},
        {},
        {"room": "x"},
        {"room_id": "00000000-0000-0000-0000-000000000000", "name": "Kitchen"},
    ],
)
async def test_a_move_that_is_not_exactly_a_room_id_is_422_at_after(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], after: dict[str, Any]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    bad = {"entity_type": "photo_room", "entity_id": targets["photo"], "after": after}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[bad]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 0, "after"],
        "correction_value",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 1)


@pytest.mark.parametrize("where", ["unknown", "earlier_revision", "other_study"])
async def test_a_move_to_a_room_not_in_the_revision_is_422_at_after(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], where: str
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, revision=2, components_gate=True)
    old = await seed.outputs(sid, revision=1)
    targets = await seed.outputs(sid, revision=2)
    other = await study_at(api, seed, Stage.ROOMS_REVIEW, components_gate=True)
    rooms = {
        "unknown": str(uuid.uuid4()),
        "earlier_revision": old["room"],
        "other_study": (await seed.outputs(other))["room"],
    }
    move = correction("photo_room", targets["photo"], room_id=rooms[where])
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 2, "reject", corrections=[move]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 0, "after"],
        "correction_target",
    )
    assert rooms[where] in error["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 2)


async def test_the_single_rooms_review_still_takes_components_and_refuses_moves(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A study whose workflow has the single rooms review (started before the patch)."""
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, components_gate=False)
    targets = await seed.outputs(sid)
    count = {"entity_type": "component", "entity_id": targets["component"], "after": {"count": 3}}
    move = correction("photo_room", targets["photo"], room_id=targets["room"])
    refused = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[count, move]),
    )
    assert refused.status_code == 422, refused.text
    assert [e["loc"] for e in refused.json()["errors"]] == [
        ["body", "corrections", 1, "entity_type"]
    ]
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 1)
    accepted = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[count]),
    )
    assert accepted.status_code == 202, accepted.text


async def test_approving_the_rooms_review_leaves_the_components_review_closed(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The row goes to components_processing; a decision for the components review before
    the workflow opens it is 409 (stale: the study is not at it yet)."""
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, components_gate=True)
    path = f"/v1/studies/{sid}/decisions"
    approved = await api.post("alice", path, decision("rooms_review", 1, "approve"))
    assert approved.status_code == 202, approved.text
    assert approved.json()["current_stage"] == "components_processing"
    early = await api.post("alice", path, decision("components_review", 1, "approve"))
    assert early.status_code == 409
    assert early.json()["type"].endswith("/stale-state")
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(sid))
    assert study is not None
    assert (study.current_stage, study.status) == (
        Stage.COMPONENTS_PROCESSING,
        StudyStatus.PROCESSING,
    )


async def test_an_approval_at_the_components_review_cannot_carry_corrections(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    count = {"entity_type": "component", "entity_id": targets["component"], "after": {"count": 3}}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("components_review", 1, "approve", corrections=[count]),
    )
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)


# --------------------------------------------------------------------------- removals
# ``component_remove`` (basis.domain.removals): an erroneous room inventory component row is
# left out of the next revision; ``after`` is ``{}`` and a reason is required.

REASON = "Not in the property."


def removal(component_id: str, reason: str | None = REASON, **after: Any) -> dict[str, Any]:
    one: dict[str, Any] = {
        "entity_type": "component_remove",
        "entity_id": component_id,
        "after": dict(after),
    }
    if reason is not None:
        one["reason"] = reason
    return one


async def inventory_rows(
    sm: async_sessionmaker[AsyncSession],
    sid: str,
    targets: dict[str, str],
    revision: int = 1,
    count: int = 2,
) -> list[str]:
    """``count`` reconciled inventory rows in the seeded room, whose photo is assigned to it."""
    async with session_scope(sm) as session:
        session.add(
            PhotoRoom(
                study_id=uuid.UUID(sid),
                document_id=uuid.UUID(targets["photo"]),
                room_id=uuid.UUID(targets["room"]),
                revision=revision,
            )
        )
        rows = [
            Component(
                study_id=uuid.UUID(sid),
                revision=revision,
                document_id=None,
                room_id=uuid.UUID(targets["room"]),
                label=label,
                raw={"source": ROOM_INVENTORY_SOURCE, "count": 2, "photos": [targets["photo"]]},
            )
            for label in ("cabinet", "window", "door", "sink")[:count]
        ]
        session.add_all(rows)
        await session.flush()
        return [str(row.id) for row in rows]


async def test_a_removal_is_recorded_with_its_reason(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, revision=2, components_gate=True)
    targets = await seed.outputs(sid, revision=2)
    rows = await inventory_rows(sessionmaker, sid, targets, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "components_review",
            2,
            "reject",
            corrections=[removal(rows[0]), removal(targets["component"], reason="Duplicate.")],
        ),
    )
    assert response.status_code == 202, response.text
    assert response.json()["corrections_recorded"] == 2
    async with sessionmaker() as session:
        stored = (await session.scalars(select(Correction).order_by(Correction.reason))).all()
        (signal,) = (await session.scalars(select(OutboxMessage))).all()
    assert [(r.entity_type, r.after, r.reason) for r in stored] == [
        ("component_remove", {}, "Duplicate."),
        ("component_remove", {}, REASON),
    ]
    assert signal.payload["corrections"][0] == {
        "entity_type": "component_remove",
        "entity_id": rows[0],
        "after": {},
        "reason": REASON,
    }


@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_a_removal_without_a_reason_is_422_at_reason(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], reason: str | None
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    good = {
        "entity_type": "room_component",
        "entity_id": targets["room"],
        "after": {"label": "window", "count": 1},
    }
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "components_review",
            1,
            "reject",
            corrections=[good, removal(targets["component"], reason=reason)],
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 1, "reason"],
        "correction_reason",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)


async def test_a_removal_without_a_reason_carries_its_code(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "components_review", 1, "reject", corrections=[removal(targets["component"], None)]
        ),
    )
    assert response.status_code == 422
    assert response.json()["code"] == E.STUDY_CORRECTION_REASON


async def test_a_removal_takes_no_fields(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "components_review", 1, "reject", corrections=[removal(targets["component"], count=0)]
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 0, "after"],
        "correction_value",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)


async def test_a_row_removed_twice_or_removed_and_corrected_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    component = targets["component"]
    count = {"entity_type": "component", "entity_id": component, "after": {"count": 3}}
    for corrections, index in (
        ([removal(component), removal(component)], 1),
        ([count, removal(component)], 1),
    ):
        response = await api.post(
            "alice",
            f"/v1/studies/{sid}/decisions",
            decision("components_review", 1, "reject", corrections=corrections),
        )
        assert response.status_code == 422, response.text
        [error] = response.json()["errors"]
        assert error["loc"] == ["body", "corrections", index, "entity_id"]
        assert error["type"] == "correction_duplicate"
        assert response.json()["code"] == E.STUDY_CORRECTION_DUPLICATE
        await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)


async def test_removing_a_row_the_rejection_rebuilds_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A photo of the room flagged for re-analysis in the same decision: reconcile_rooms
    rebuilds the room's inventory, and the photo's own rows are rebuilt by its analysis;
    corrections of rebuilt rows are not carried, so the removal is refused (at its
    entity_id). Without the re-analysis the same removal is accepted."""
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    rows = await inventory_rows(sessionmaker, sid, targets)
    flag = {"entity_type": "photo", "entity_id": targets["photo"], "after": {}}
    path = f"/v1/studies/{sid}/decisions"
    for removed in (rows[0], targets["component"]):  # an inventory row, the photo's own row
        response = await api.post(
            "alice",
            path,
            decision("components_review", 1, "reject", corrections=[flag, removal(removed)]),
        )
        assert response.status_code == 422, response.text
        [error] = response.json()["errors"]
        assert error["loc"] == ["body", "corrections", 1, "entity_id"]
        assert error["type"] == "correction_rebuilt"
        assert "re-analy" in error["detail"] and "not carried" in error["detail"]
        assert response.json()["code"] == E.STUDY_CORRECTION_REBUILT
        await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)
    accepted = await api.post(
        "alice", path, decision("components_review", 1, "reject", corrections=[removal(rows[0])])
    )
    assert accepted.status_code == 202, accepted.text


async def test_removing_a_merged_rooms_inventory_row_is_422_in_the_single_rooms_review(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ROOMS_REVIEW, components_gate=False)
    targets = await seed.outputs(sid)
    rows = await inventory_rows(sessionmaker, sid, targets)
    other = (await seed.outputs(sid))["room"]  # a second room of the same revision
    merge = {"entity_type": "room", "entity_id": targets["room"], "after": {"merge_into": other}}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("rooms_review", 1, "reject", corrections=[merge, removal(rows[0])]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 1, "entity_id"],
        "correction_rebuilt",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.ROOMS_REVIEW, 1)


async def test_removing_every_inventory_row_of_a_room_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, components_gate=True)
    targets = await seed.outputs(sid)
    rows = await inventory_rows(sessionmaker, sid, targets, count=2)
    path = f"/v1/studies/{sid}/decisions"
    response = await api.post(
        "alice",
        path,
        decision("components_review", 1, "reject", corrections=[removal(r) for r in rows]),
    )
    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert [e["loc"] for e in errors] == [
        ["body", "corrections", 0, "entity_id"],
        ["body", "corrections", 1, "entity_id"],
    ]
    assert {e["type"] for e in errors} == {"correction_last_inventory"}
    assert "last inventory row" in errors[0]["detail"]
    assert response.json()["code"] == E.STUDY_CORRECTION_LAST_INVENTORY
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 1)
    one = await api.post(
        "alice", path, decision("components_review", 1, "reject", corrections=[removal(rows[1])])
    )
    assert one.status_code == 202, one.text


# --------------------------------------------------------------------------- added fields
# ``extraction_field_add`` (basis.extraction.added_fields): an appraisal field the extraction
# missed; ``entity_id`` is the extraction, a reason is required.


def field_add(extraction_id: str, reason: str | None = REASON, **after: Any) -> dict[str, Any]:
    one: dict[str, Any] = {
        "entity_type": "extraction_field_add",
        "entity_id": extraction_id,
        "after": {**ADDED_FIELD, **after},
    }
    if reason is not None:
        one["reason"] = reason
    return one


async def test_an_added_field_is_recorded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "appraisal_review",
            2,
            "reject",
            corrections=[field_add(targets["extraction_field_add"])],
        ),
    )
    assert response.status_code == 202, response.text
    async with sessionmaker() as session:
        (row,) = (await session.scalars(select(Correction))).all()
    assert (row.entity_type, str(row.entity_id), row.after, row.reason) == (
        "extraction_field_add",
        targets["extraction_field_add"],
        ADDED_FIELD,
        REASON,
    )


@pytest.mark.parametrize(
    ("after", "fragment"),
    [
        ({"key": "improvements.year"}, "catalogue"),
        ({"value_num": None, "value_text": "1987"}, "takes value_num"),
        ({"value_text": "1987"}, "exactly one"),
        ({"value_num": 1987.5}, "whole number"),
        ({"page": 0}, "from 1"),
        ({"evidence": " "}, "evidence"),
        ({"evidence": "e" * 1001}, "at most"),
        ({"source_document": "appraisal"}, "not source_document"),
    ],
)
async def test_an_added_field_the_workflow_would_refuse_is_422_at_after(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    after: dict[str, Any],
    fragment: str,
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "appraisal_review",
            1,
            "reject",
            corrections=[field_add(targets["extraction_field_add"], **after)],
        ),
    )
    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert {(tuple(e["loc"]), e["type"]) for e in errors} == {
        (("body", "corrections", 0, "after"), "correction_value")
    }
    assert any(fragment in e["detail"] for e in errors), errors
    await assert_nothing_written(sessionmaker, sid, Stage.APPRAISAL_REVIEW, 1)


@pytest.mark.parametrize("reason", [None, "\t"])
async def test_an_added_field_needs_a_reason(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], reason: str | None
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "appraisal_review",
            1,
            "reject",
            corrections=[field_add(targets["extraction_field_add"], reason=reason)],
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 0, "reason"],
        "correction_reason",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.APPRAISAL_REVIEW, 1)


async def test_adding_a_field_the_extraction_has_is_422_and_says_correct_it(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    extraction_id = uuid.UUID(targets["extraction_field_add"])
    async with session_scope(sessionmaker) as session:
        session.add(
            ExtractionField(
                extraction_id=extraction_id,
                study_id=uuid.UUID(sid),
                section="improvements",
                field="year_built",
                value_num=1990,
                source="azure",
            )
        )
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("appraisal_review", 1, "reject", corrections=[field_add(str(extraction_id))]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 0, "after"],
        "correction_field_exists",
    )
    assert "extraction_field" in error["detail"]
    assert response.json()["code"] == E.STUDY_CORRECTION_FIELD_EXISTS
    await assert_nothing_written(sessionmaker, sid, Stage.APPRAISAL_REVIEW, 1)


async def test_a_page_past_the_appraisal_pdf_is_422_only_when_the_page_count_is_known(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    extraction_id = uuid.UUID(targets["extraction_field_add"])
    path = f"/v1/studies/{sid}/decisions"
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Extraction).where(Extraction.id == extraction_id).values(raw={"page_count": 2})
        )
    response = await api.post(
        "alice",
        path,
        decision(
            "appraisal_review", 1, "reject", corrections=[field_add(str(extraction_id), page=3)]
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["type"] == "correction_value" and "2 pages" in error["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.APPRAISAL_REVIEW, 1)
    accepted = await api.post(
        "alice",
        path,
        decision(
            "appraisal_review", 1, "reject", corrections=[field_add(str(extraction_id), page=2)]
        ),
    )
    assert accepted.status_code == 202, accepted.text


async def test_the_same_field_added_twice_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.APPRAISAL_REVIEW)
    targets = await seed.outputs(sid)
    extraction_id = targets["extraction_field_add"]
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "appraisal_review",
            1,
            "reject",
            corrections=[field_add(extraction_id), field_add(extraction_id, value_num=1988)],
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 1, "after"],
        "correction_duplicate",
    )
    await assert_nothing_written(sessionmaker, sid, Stage.APPRAISAL_REVIEW, 1)


# --------------------------------------------------------------------------- both new types
# The contract every correction type keeps: a stale revision is 409, a viewer 403, a
# non-member 404, and the same decision replayed with its Idempotency-Key is the same 202.

NEW_TYPES = [
    ("components_review", "component_remove"),
    ("appraisal_review", "extraction_field_add"),
]


@pytest.mark.parametrize(("stage", "entity_type"), NEW_TYPES)
async def test_a_stale_decision_with_a_new_type_is_409(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage), revision=2, components_gate=True)
    old = await seed.outputs(sid, revision=1)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 1, "reject", corrections=[correction(entity_type, old[entity_type])]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("/stale-state")
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 2)


@pytest.mark.parametrize(("stage", "entity_type"), NEW_TYPES)
async def test_a_viewer_is_403_and_a_non_member_404_with_a_new_type(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    await seed.stage(sid, Stage(stage), components_gate=True)
    targets = await seed.outputs(sid)
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    body = decision(stage, 1, "reject", corrections=[correction(entity_type, targets[entity_type])])
    assert (await api.post("vera", f"/v1/studies/{sid}/decisions", body)).status_code == 403
    assert (await api.post("mallory", f"/v1/studies/{sid}/decisions", body)).status_code == 404
    await assert_nothing_written(sessionmaker, sid, Stage(stage), 1)


@pytest.mark.parametrize(("stage", "entity_type"), NEW_TYPES)
async def test_a_decision_with_a_new_type_replays_with_its_key(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: str,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, Stage(stage), components_gate=True)
    targets = await seed.outputs(sid)
    path, key = f"/v1/studies/{sid}/decisions", str(uuid.uuid4())
    body = decision(stage, 1, "reject", corrections=[correction(entity_type, targets[entity_type])])
    first = await api.post("alice", path, body, key=key)
    second = await api.post("alice", path, body, key=key)
    assert (first.status_code, second.status_code) == (202, 202), first.text
    assert second.content == first.content
    assert second.headers[REPLAYED_HEADER] == "true"
    assert await counts(sessionmaker) == (1, 1, 1)
