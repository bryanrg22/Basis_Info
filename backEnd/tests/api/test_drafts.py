"""``GET``/``PUT``/``DELETE /v1/studies/{id}/drafts/{stage}`` (docs/api/GATE_DRAFTS_AND_RESUME.md).

An engineer's pending corrections at a review gate survive a reload because they are on the
server: one document per study, stage and user, validated as the decision route validates a
decision's corrections, versioned as the intake is (``ETag``/``If-Match``), reported stale
once the study has moved on, and deleted by the decision that submits them. Real Postgres;
synthetic outputs (``Seed.outputs``) for the correction targets.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import OrgRole, Stage
from basis.db.models import ExtractionField, ReviewDraft, StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E

pytestmark = pytest.mark.integration


def path(sid: str, stage: str = "appraisal_review") -> str:
    return f"/v1/studies/{sid}/drafts/{stage}"


async def put(
    api: Api,
    uid: str,
    sid: str,
    body: dict[str, Any],
    *,
    stage: str = "appraisal_review",
    if_match: str | None = None,
) -> Any:
    headers = dict(bearer(uid))
    if if_match is not None:
        headers["If-Match"] = if_match
    return await api.client.put(path(sid, stage), json=body, headers=headers)


def correction(targets: dict[str, str], **overrides: Any) -> dict[str, Any]:
    return {
        "entity_type": "extraction_field",
        "entity_id": targets["extraction_field"],
        "before": {"year_built": 1989},
        "after": {"year_built": 1998},
        "reason": "Deed shows 1998.",
        **overrides,
    }


def draft(targets: dict[str, str], revision: int = 1, **overrides: Any) -> dict[str, Any]:
    return {
        "revision": revision,
        "comment": "Checking the deed.",
        "corrections": [correction(targets)],
        **overrides,
    }


async def gate(
    api: Api, seed: Seed, stage: Stage = Stage.APPRAISAL_REVIEW
) -> tuple[str, dict[str, str]]:
    """A study of alice's firm waiting at ``stage`` (revision 1) with one target of every
    kind; bob is an engineer and vera a viewer there."""
    study = await api.create_study("alice")
    sid = str(study["id"])
    await seed.member(uuid.UUID(study["org_id"]), "bob", OrgRole.ENGINEER)
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    await seed.stage(sid, stage, 1, components_gate=True)
    return sid, await seed.outputs(sid)


def problem(response: Any, status: int, suffix: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body: dict[str, Any] = response.json()
    if suffix is not None:
        assert body["type"].endswith(f"/{suffix}"), body
    return body


async def rows(sm: async_sessionmaker[AsyncSession], sid: str) -> list[ReviewDraft]:
    async with sm() as session:
        return list(
            (
                await session.scalars(
                    select(ReviewDraft)
                    .where(ReviewDraft.study_id == uuid.UUID(sid))
                    .order_by(ReviewDraft.created_at)
                )
            ).all()
        )


async def event_count(sm: async_sessionmaker[AsyncSession], sid: str) -> int:
    async with sm() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(StudyEvent)
            .where(StudyEvent.study_id == uuid.UUID(sid))
        )
    return int(count or 0)


# --------------------------------------------------------------------------- save and read


async def test_a_draft_is_saved_on_the_server_and_read_back_after_a_reload(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await gate(api, seed)
    empty = await api.get("alice", path(sid))
    assert empty.status_code == 200, empty.text
    assert empty.headers["etag"] == '"0"' and empty.headers["cache-control"] == "no-store"
    assert empty.json() == {
        "study_id": sid,
        "stage": "appraisal_review",
        "current_revision": 1,
        "draft": None,
        "others": [],
    }

    saved = await put(api, "alice", sid, draft(targets))
    assert saved.status_code == 200, saved.text
    assert saved.headers["etag"] == '"1"' and saved.headers["cache-control"] == "no-store"
    body = saved.json()
    assert body["current_revision"] == 1 and body["others"] == []
    own = body["draft"]
    alice = await seed.user("alice")
    assert own["user_id"] == str(alice.id) and own["study_id"] == sid
    assert (own["stage"], own["revision"], own["version"]) == ("appraisal_review", 1, 1)
    assert own["comment"] == "Checking the deed."
    assert own["corrections"] == [correction(targets)]  # before, after and reason round-trip
    assert (own["stale"], own["stale_reason"]) == (False, None)
    assert own["created_at"] == own["updated_at"]

    # A new client (the reload) reads exactly what was saved.
    read = await api.get("alice", path(sid))
    assert read.json() == body and read.headers["etag"] == '"1"'
    [row] = await rows(sessionmaker, sid)
    assert row.document == {"comment": "Checking the deed.", "corrections": [correction(targets)]}
    assert (row.revision, row.version, row.stage) == (1, 1, Stage.APPRAISAL_REVIEW)


async def test_every_save_replaces_the_whole_draft_and_bumps_the_version(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await gate(api, seed)
    await put(api, "alice", sid, draft(targets))
    replaced = await put(
        api, "alice", sid, {"revision": 1, "comment": None, "corrections": []}, if_match='"1"'
    )
    assert replaced.status_code == 200 and replaced.headers["etag"] == '"2"'
    own = replaced.json()["draft"]
    assert (own["comment"], own["corrections"], own["version"]) == (None, [], 2)
    assert own["updated_at"] >= own["created_at"]
    [row] = await rows(sessionmaker, sid)  # one row per user and stage, rewritten in place
    assert row.version == 2 and row.document == {"comment": None, "corrections": []}
    # A draft may hold the comment alone (no corrections yet: the engineer is still looking).
    comment_only = await put(api, "alice", sid, {"revision": 1, "comment": "Looks fine so far."})
    assert comment_only.status_code == 200 and comment_only.json()["draft"]["corrections"] == []


async def test_stages_are_independent(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    room = {
        "revision": 1,
        "corrections": [
            {"entity_type": "room", "entity_id": targets["room"], "after": {"name": "Pantry"}}
        ],
    }
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await put(api, "alice", sid, room, stage="rooms_review")).status_code == 200
    appraisal = (await api.get("alice", path(sid))).json()["draft"]
    rooms = (await api.get("alice", path(sid, "rooms_review"))).json()["draft"]
    assert appraisal["corrections"][0]["entity_type"] == "extraction_field"
    assert rooms["corrections"][0]["entity_type"] == "room"
    assert (await api.get("alice", path(sid, "assets_review"))).json()["draft"] is None


# --------------------------------------------------------------------------- validation


async def test_a_draft_is_checked_as_a_decision_would_be(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Every check of ``POST .../decisions`` runs at save time, with the same error types
    and locations, so a draft that would be refused at submit time is refused now."""
    sid, targets = await gate(api, seed)
    async with session_scope(sessionmaker) as session:  # a catalogue field the extraction has
        session.add(
            ExtractionField(
                extraction_id=uuid.UUID(targets["extraction_field_add"]),
                study_id=uuid.UUID(sid),
                section="improvements",
                field="year_built",
                value_text="1989",
                source="stub",
            )
        )
    cases = [
        # the type the stage does not accept (rooms at the appraisal review)
        (
            {"entity_type": "room", "entity_id": targets["room"], "after": {"name": "x"}},
            "correction_type",
            "entity_type",
        ),
        # a target that is not in the revision
        (correction(targets, entity_id=str(uuid.uuid4())), "correction_target", "entity_id"),
        # an added field without a reason (the decision's reason rule)
        (
            {
                "entity_type": "extraction_field_add",
                "entity_id": targets["extraction_field_add"],
                "after": {
                    "key": "subject.tax_year",
                    "value_num": 2024,
                    "page": 1,
                    "evidence": "Tax year 2024",
                },
            },
            "correction_reason",
            "reason",
        ),
        # an added field whose key the extraction already has
        (
            {
                "entity_type": "extraction_field_add",
                "entity_id": targets["extraction_field_add"],
                "after": {
                    "key": "improvements.year_built",
                    "value_num": 1998,
                    "page": 1,
                    "evidence": "1998",
                },
                "reason": "missed",
            },
            "correction_field_exists",
            "after",
        ),
        # the study-targeted rebuild is requested through the reprice route only
        (
            {"entity_type": "reprice", "entity_id": sid, "after": {"reason": "x"}},
            "correction_type",
            "entity_type",
        ),
    ]
    for item, kind, member in cases:
        response = await put(api, "alice", sid, {"revision": 1, "corrections": [item]})
        body = problem(response, 422, "validation")
        assert len(body["errors"]) == 1, (item, body["errors"])
        [error] = body["errors"]
        assert (error["type"], error["loc"]) == (kind, ["body", "corrections", 0, member]), item
    assert (await api.get("alice", path(sid))).json()["draft"] is None  # nothing stored

    # The rooms checks at the rooms review: a bad count and a photo move to a room not in
    # the revision.
    rooms_sid, rooms_targets = await gate(api, seed, Stage.COMPONENTS_REVIEW)
    bad_count = {
        "entity_type": "component",
        "entity_id": rooms_targets["component"],
        "after": {"count": 0},
    }
    body = problem(
        await put(
            api,
            "alice",
            rooms_sid,
            {"revision": 1, "corrections": [bad_count]},
            stage="components_review",
        ),
        422,
    )
    assert body["errors"][0]["type"] == "correction_value"
    move = {
        "entity_type": "photo_room",
        "entity_id": rooms_targets["photo_room"],
        "after": {"room_id": str(uuid.uuid4())},
    }
    body = problem(
        await put(
            api, "alice", rooms_sid, {"revision": 1, "corrections": [move]}, stage="rooms_review"
        ),
        422,
    )
    assert body["errors"][0]["type"] == "correction_target"
    # The decision-only rule does not apply: a draft never says approve or reject.
    with_corrections = await put(
        api,
        "alice",
        rooms_sid,
        {"revision": 1, "corrections": [{**bad_count, "after": {"count": 3}}]},
        stage="components_review",
    )
    assert with_corrections.status_code == 200, with_corrections.text


@pytest.mark.parametrize(
    "body",
    [
        {},  # revision is required
        {"revision": 0},
        {"revision": 1, "comment": "a\x00b"},
        {"revision": 1, "corrections": "none"},
        {"revision": 1, "corrections": [{"entity_type": "asset"}]},  # no id, no after
        {"revision": 1, "unknown": 1},
    ],
)
async def test_malformed_bodies_are_422(api: Api, seed: Seed, body: dict[str, Any]) -> None:
    sid, _ = await gate(api, seed)
    problem(await put(api, "alice", sid, body), 422)


async def test_a_stage_that_is_not_a_review_is_422(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    for stage in ("extracting", "completed", "nope"):
        problem(await api.get("alice", path(sid, stage)), 422)
        problem(await put(api, "alice", sid, draft(targets), stage=stage), 422)
        problem(await api.delete("alice", path(sid, stage)), 422)


# --------------------------------------------------------------------------- stale drafts


async def test_a_draft_for_another_revision_is_refused(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    body = problem(await put(api, "alice", sid, draft(targets, revision=2)), 409, "draft-stale")
    assert body["code"] == E.STUDY_DRAFT_STALE and "revision 1" in body["detail"]
    assert (await api.get("alice", path(sid))).json()["draft"] is None


async def test_a_draft_the_study_has_moved_past_is_reported_stale_not_current(
    api: Api, seed: Seed
) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    await seed.stage(sid, Stage.EXTRACTING, 2)  # a rejection re-ran the appraisal
    read = (await api.get("alice", path(sid))).json()
    assert read["current_revision"] == 2
    assert (read["draft"]["revision"], read["draft"]["stale"], read["draft"]["stale_reason"]) == (
        1,
        True,
        "revision",
    )
    assert read["draft"]["corrections"] == [correction(targets)]  # still readable, to carry over
    # Saving against the old revision is refused; against the new one it replaces the draft.
    problem(await put(api, "alice", sid, draft(targets)), 409, "draft-stale")
    await seed.stage(sid, Stage.APPRAISAL_REVIEW, 2)
    new_targets = await seed.outputs(sid, revision=2)
    fresh = await put(api, "alice", sid, draft(new_targets, revision=2), if_match='"1"')
    assert fresh.status_code == 200 and fresh.json()["draft"]["stale"] is False
    assert fresh.json()["draft"]["revision"] == 2 and fresh.headers["etag"] == '"2"'


async def test_a_draft_for_a_stage_another_engineer_decided_is_stale(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    approved = await api.post(
        "bob",
        f"/v1/studies/{sid}/decisions",
        {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
    )
    assert approved.status_code == 202, approved.text
    read = (await api.get("alice", path(sid))).json()
    assert read["current_revision"] == 1
    assert (read["draft"]["stale"], read["draft"]["stale_reason"]) == (True, "decided")
    body = problem(await put(api, "alice", sid, draft(targets)), 409, "draft-stale")
    assert "already decided" in body["detail"]
    # Alice discards it; the stage's drafts are then none.
    gone = await api.delete("alice", path(sid))
    assert gone.status_code == 200 and gone.json()["draft"] is None


# --------------------------------------------------------------------------- the decision


async def test_the_decision_deletes_the_submitters_draft_in_its_transaction(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await put(api, "bob", sid, {"revision": 1, "comment": "me too"})).status_code == 200

    # A refused decision (a correction the revision does not have) rolls the draft back too.
    refused = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "appraisal_review",
            "revision": 1,
            "decision": "reject",
            "corrections": [correction(targets), correction(targets, entity_id=str(uuid.uuid4()))],
        },
    )
    assert refused.status_code == 422
    assert (await api.get("alice", path(sid))).json()["draft"]["version"] == 1

    sent = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "appraisal_review",
            "revision": 1,
            "decision": "reject",
            "comment": "Checking the deed.",
            "corrections": [correction(targets)],
        },
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["corrections_recorded"] == 1
    read = (await api.get("alice", path(sid))).json()
    assert read["draft"] is None and read["current_revision"] == 2
    [other] = read["others"]  # bob's survives, stale now: the stage was decided
    assert other["stale"] is True
    remaining = await rows(sessionmaker, sid)
    assert [r.user_id for r in remaining] == [(await seed.user("bob")).id]
    async with sessionmaker() as session:
        decided = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type == "review.decided"
                )
            )
        ).one()
    assert decided.detail["draft_discarded"] is True
    assert (await api.delete("alice", path(sid))).status_code == 404  # already gone


async def test_a_decision_without_a_draft_records_none_discarded(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _ = await gate(api, seed)
    sent = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
    )
    assert sent.status_code == 202
    async with sessionmaker() as session:
        decided = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type == "review.decided"
                )
            )
        ).one()
    assert decided.detail["draft_discarded"] is False


# --------------------------------------------------------------------------- per user


async def test_two_engineers_draft_apart_and_see_each_other_listed(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await put(api, "bob", sid, {"revision": 1, "corrections": []})).status_code == 200
    bob = await seed.user("bob")
    alice_view = (await api.get("alice", path(sid))).json()
    assert alice_view["draft"]["user_id"] == str((await seed.user("alice")).id)
    [other] = alice_view["others"]
    assert other == {
        "user_id": str(bob.id),
        "display_name": "Bob",
        "email": "bob@example.test",
        "revision": 1,
        "corrections": 0,
        "has_comment": False,
        "stale": False,
        "updated_at": other["updated_at"],
    }
    assert "comment" not in other and "entity_id" not in str(other)  # who, not what
    bob_view = (await api.get("bob", path(sid))).json()
    assert bob_view["draft"]["corrections"] == [] and bob_view["draft"]["version"] == 1
    [alices] = bob_view["others"]
    assert (alices["display_name"], alices["corrections"], alices["has_comment"]) == (
        "Alice",
        1,
        True,
    )
    # Bob's save does not touch alice's version, and his delete does not touch her draft.
    assert (await api.get("alice", path(sid))).headers["etag"] == '"1"'
    assert (await api.delete("bob", path(sid))).status_code == 200
    assert (await api.get("alice", path(sid))).json()["draft"]["version"] == 1
    assert (await api.get("alice", path(sid))).json()["others"] == []


# --------------------------------------------------------------------------- versions


async def test_a_stale_if_match_is_412_and_writes_nothing(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    before = (await api.get("alice", path(sid))).json()
    for tag in ('"0"', '"2"', 'W/"1"', "1", '"abc"'):
        refused = await put(api, "alice", sid, {"revision": 1, "comment": "lost?"}, if_match=tag)
        body = problem(refused, 412, "precondition-failed")
        assert body["code"] == E.STUDY_DRAFT_PRECONDITION_FAILED
        assert refused.headers["etag"] == '"1"' and '"1"' in body["detail"]
    assert (await api.get("alice", path(sid))).json() == before
    # The current tag writes; a list with the current tag writes.
    assert (await put(api, "alice", sid, {"revision": 1}, if_match='"1"')).status_code == 200
    assert (await put(api, "alice", sid, {"revision": 1}, if_match='"1", "2"')).status_code == 200
    assert (await api.get("alice", path(sid))).headers["etag"] == '"3"'
    # The second of two saves from the same read loses.
    assert (await put(api, "alice", sid, {"revision": 1}, if_match='"3"')).status_code == 200
    problem(await put(api, "alice", sid, {"revision": 1}, if_match='"3"'), 412)


async def test_if_match_zero_creates_only_while_none_exists_and_star_only_over_one(
    api: Api, seed: Seed
) -> None:
    sid, _ = await gate(api, seed)
    star = problem(await put(api, "alice", sid, {"revision": 1}, if_match="*"), 412)
    assert "If-Match: *" in star["detail"] and star["code"] == E.STUDY_DRAFT_PRECONDITION_FAILED
    assert (await put(api, "alice", sid, {"revision": 1}, if_match='"0"')).status_code == 200
    problem(await put(api, "alice", sid, {"revision": 1}, if_match='"0"'), 412)  # now exists
    assert (await put(api, "alice", sid, {"revision": 1}, if_match="*")).status_code == 200
    assert (await api.get("alice", path(sid))).headers["etag"] == '"2"'


async def test_concurrent_first_saves_with_if_match_zero_create_exactly_one(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _ = await gate(api, seed)
    responses = await asyncio.gather(
        *[
            put(api, "alice", sid, {"revision": 1, "comment": f"tab {n}"}, if_match='"0"')
            for n in range(3)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 412, 412]
    [row] = await rows(sessionmaker, sid)
    assert row.version == 1
    winner = next(r for r in responses if r.status_code == 200).json()["draft"]["comment"]
    assert row.document["comment"] == winner


async def test_concurrent_saves_with_the_same_if_match_cannot_both_succeed(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _ = await gate(api, seed)
    assert (await put(api, "alice", sid, {"revision": 1})).status_code == 200
    responses = await asyncio.gather(
        *[
            put(api, "alice", sid, {"revision": 1, "comment": f"tab {n}"}, if_match='"1"')
            for n in range(3)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 412, 412]
    [row] = await rows(sessionmaker, sid)
    assert row.version == 2


# --------------------------------------------------------------------------- discard


async def test_delete_discards_only_your_draft_with_the_same_precondition(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await gate(api, seed)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await put(api, "bob", sid, {"revision": 1})).status_code == 200
    stale = problem(
        await api.client.delete(
            path(sid),
            headers={**bearer("alice"), "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"7"'},
        ),
        412,
        "precondition-failed",
    )
    assert stale["code"] == E.STUDY_DRAFT_PRECONDITION_FAILED
    assert len(await rows(sessionmaker, sid)) == 2  # nothing deleted
    gone = await api.client.delete(
        path(sid),
        headers={**bearer("alice"), "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
    )
    assert gone.status_code == 200, gone.text
    assert gone.headers["etag"] == '"0"' and gone.json()["draft"] is None
    assert len(gone.json()["others"]) == 1  # bob's is untouched
    again = problem(await api.delete("alice", path(sid)), 404, "not-found")
    assert again["code"] == E.STUDY_DRAFT_NOT_FOUND
    assert [r.user_id for r in await rows(sessionmaker, sid)] == [(await seed.user("bob")).id]
    # If-Match: * with no draft is a 412 (there is no current one), not a 404.
    problem(
        await api.client.delete(
            path(sid),
            headers={**bearer("alice"), "Idempotency-Key": str(uuid.uuid4()), "If-Match": "*"},
        ),
        412,
    )


# --------------------------------------------------------------------------- access and state


async def test_roles_visibility_and_archived_studies(api: Api, seed: Seed) -> None:
    sid, targets = await gate(api, seed)
    assert (await api.get("vera", path(sid))).status_code == 403  # a viewer cannot draft
    assert (await put(api, "vera", sid, draft(targets))).status_code == 403
    assert (await api.delete("vera", path(sid))).status_code == 403
    assert (await api.get("mallory", path(sid))).status_code == 404  # not a member
    assert (await put(api, "mallory", sid, draft(targets))).status_code == 404
    assert (await api.get("alice", path(str(uuid.uuid4())))).status_code == 404
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200  # archived
    problem(await put(api, "alice", sid, draft(targets)), 409, "study-archived")
    assert (await api.get("alice", path(sid))).json()["draft"]["version"] == 1  # still readable
    assert (await api.delete("alice", path(sid))).status_code == 200  # and discardable


async def test_a_draft_may_be_saved_before_the_gate_opens(api: Api, seed: Seed) -> None:
    """The gate (``waiting_review``) governs the decision, not the draft: an engineer may
    line up corrections while the study is still ``processing`` at the stage's revision."""
    sid, targets = await gate(api, seed, Stage.EXTRACTING)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await api.get("alice", path(sid))).json()["draft"]["stale"] is False


async def test_drafts_write_no_timeline_event(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A draft is work in progress, not history (docs/api/GATE_DRAFTS_AND_RESUME.md): the
    timeline records the decision and its corrections, not every autosave."""
    sid, targets = await gate(api, seed)
    before = await event_count(sessionmaker, sid)
    assert (await put(api, "alice", sid, draft(targets))).status_code == 200
    assert (await put(api, "alice", sid, {"revision": 1})).status_code == 200
    assert (await api.delete("alice", path(sid))).status_code == 200
    assert await event_count(sessionmaker, sid) == before
    audit = (await api.get("alice", f"/v1/studies/{sid}/audit")).json()
    assert audit["items"] == []
