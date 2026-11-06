"""Engineer overrides on the API (EVAL_REPORT 90, docs/api/OVERRIDES.md).

* ``GET /v1/capabilities``: the flags follow ``OBJECT_EXCLUSION`` and ``UNIT_COST_OVERRIDE``;
  ``room_create`` is always false; any signed-in user, a firm or not.
* ``PATCH .../photos/{document_id}/objects/{object_id}``: Not counted and counted again, on a
  model detection and an engineer's object, with who, when and why on every listing, the
  counts and the rooms' suggested counts leaving it out, the timeline and the audit; every
  refusal (the reason, the role, the stage, the gate, the archive, the photo, the object, a
  candidate, the setting, the idempotency key); the duplicate guard and a re-draw over an
  excluded detection.
* The decision route: a unit price on ``component`` and ``asset`` corrections, each value it
  refuses (``correction_value`` at ``after``), a missing reason (``correction_reason``), a
  concealed row, and the setting off; and the asset's ``cost.override`` read back.

Objects are seeded through the repository as ``localize_photo`` writes them
(``tests/api/test_objects.py``: a counted window, an uncounted window candidate, a cabinet).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import assert_nothing_written, decision, study_at
from test_objects import box, path, seeded_photo

from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.db.enums import Bucket, ClassificationMethod, OrgRole, Stage, StudyStatus
from basis.db.models import (
    Asset,
    CostEstimate,
    PhotoObject,
    PhotoRoom,
    Room,
    Study,
    StudyEvent,
    Takeoff,
)
from basis.db.session import session_scope
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

REASON = "A reflection in the window, not a second window"


@pytest.fixture
async def off_api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> AsyncIterator[Api]:
    """The app with both overrides turned off."""
    settings = api_settings(database_url, object_exclusion=False, unit_cost_override=False)
    app = build_api_app(settings, sessionmaker, storage, verifier)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


async def patch(
    api: Api, uid: str, url: str, body: Any, *, key: str | None = None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer uid:{uid}", IDEMPOTENCY_HEADER: key or str(uuid.uuid4())}
    return await api.client.patch(url, json=body, headers=headers)


def one(sid: str, photo: str, object_id: uuid.UUID) -> str:
    return f"{path(sid, photo)}/{object_id}"


async def events(sm: async_sessionmaker[AsyncSession], sid: str, *types: str) -> list[StudyEvent]:
    async with sm() as session:
        return list(
            (
                await session.scalars(
                    select(StudyEvent)
                    .where(StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type.in_(types))
                    .order_by(StudyEvent.seq)
                )
            ).all()
        )


# --------------------------------------------------------------------------- capabilities


async def test_capabilities_follow_the_settings(api: Api, off_api: Api) -> None:
    on = await api.get("alice", "/v1/capabilities")
    assert on.status_code == 200, on.text
    assert on.json() == {"object_exclusion": True, "unit_cost_override": True, "room_create": False}
    off = await off_api.get("alice", "/v1/capabilities")
    assert off.json() == {
        "object_exclusion": False,
        "unit_cost_override": False,
        "room_create": False,
    }


async def test_capabilities_need_a_sign_in_but_no_firm(
    api: Api, invite_only_api: Api, http: httpx.AsyncClient
) -> None:
    assert (await http.get("/v1/capabilities")).status_code == 401
    # A signed-in user with no firm (invite-only: no firm of their own) still gets them.
    response = await invite_only_api.get("newcomer", "/v1/capabilities")
    assert response.status_code == 200 and response.json()["object_exclusion"] is True


# --------------------------------------------------------------------------- Not counted


async def test_exclude_then_count_again_a_model_detection(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    excluded = await patch(
        api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": f"  {REASON} "}
    )
    assert excluded.status_code == 200, excluded.text
    body = excluded.json()
    assert body["counted"] is False and body["source"] == "model"
    exclusion = body["exclusion"]
    assert (exclusion["reason"], exclusion["by_name"]) == (REASON, "Alice")
    assert exclusion["by"] and exclusion["at"]
    listing = (await api.get("alice", path(sid, photo))).json()
    assert listing["counts"] == {"kitchen_cabinet": 1}  # the window leaves the count
    by_id = {i["id"]: i for i in listing["items"]}
    assert by_id[str(ids["window"])]["exclusion"]["reason"] == REASON
    assert by_id[str(ids["cabinet"])]["exclusion"] is None  # every item carries the member
    assert by_id[str(ids["candidate"])]["exclusion"] is None
    # Nothing is deleted: the row is all there.
    async with sessionmaker() as session:
        row = await session.get(PhotoObject, ids["window"])
    assert row is not None and row.deleted_at is None and row.score == pytest.approx(0.9)
    restored = await patch(api, "alice", one(sid, photo, ids["window"]), {"counted": True})
    assert restored.status_code == 200, restored.text
    assert (restored.json()["counted"], restored.json()["exclusion"]) == (True, None)
    listing = (await api.get("alice", path(sid, photo))).json()
    assert listing["counts"] == {"kitchen_cabinet": 1, "window": 1}
    recorded = await events(sessionmaker, sid, "object.excluded", "object.restored")
    assert [e.event_type for e in recorded] == ["object.excluded", "object.restored"]
    first, second = recorded
    assert first.entity_type == "photo_object" and first.entity_id == ids["window"]
    assert first.before == {"counted": True, "exclusion": None}
    assert first.after["exclusion"]["reason"] == REASON and first.detail["label"] == "window"
    assert second.before["exclusion"]["reason"] == REASON
    assert second.after == {"counted": True, "exclusion": None}
    audit = (await api.get("alice", f"/v1/studies/{sid}/audit")).json()["items"]
    kinds = [(i["kind"], i["action"]) for i in audit if i["entity_type"] == "photo_object"]
    assert kinds == [("object_excluded", "excluded"), ("object_restored", "restored")]


async def test_an_engineers_own_object_can_be_excluded_and_deleted(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    added = await api.post("alice", path(sid, photo), box("mirror", 0.6, 0.6, 0.9, 0.9))
    assert added.status_code == 201, added.text
    manual = uuid.UUID(added.json()["object"]["id"])
    excluded = await patch(
        api, "alice", one(sid, photo, manual), {"counted": False, "reason": "drew it twice"}
    )
    assert excluded.status_code == 200 and excluded.json()["source"] == "manual"
    deleted = await api.delete("alice", one(sid, photo, manual))
    assert deleted.status_code == 200 and deleted.json()["exclusion"]["reason"] == "drew it twice"


async def test_repeating_the_state_records_nothing_and_a_new_reason_replaces_the_old(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    url = one(sid, photo, ids["window"])
    assert (await patch(api, "alice", url, {"counted": True})).status_code == 200  # counted
    assert (await patch(api, "alice", url, {"counted": False, "reason": REASON})).status_code == 200
    again = await patch(api, "alice", url, {"counted": False, "reason": REASON})
    assert again.status_code == 200 and again.json()["exclusion"]["reason"] == REASON
    assert len(await events(sessionmaker, sid, "object.excluded", "object.restored")) == 1
    changed = await patch(api, "alice", url, {"counted": False, "reason": "a mirror image"})
    assert changed.json()["exclusion"]["reason"] == "a mirror image"
    recorded = await events(sessionmaker, sid, "object.excluded")
    assert [e.before["exclusion"] and e.before["exclusion"]["reason"] for e in recorded] == [
        None,
        REASON,
    ]


async def test_a_patch_is_idempotent_by_its_state_and_needs_no_key(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Like ``PATCH /v1/orgs/{id}``, the route sets a state (``Idempotency-Key`` is enforced
    on POST and DELETE only): a repeated request finds the state set and records nothing."""
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    url, key = one(sid, photo, ids["window"]), str(uuid.uuid4())
    first = await patch(api, "alice", url, {"counted": False, "reason": REASON}, key=key)
    replay = await patch(api, "alice", url, {"counted": False, "reason": REASON}, key=key)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert len(await events(sessionmaker, sid, "object.excluded")) == 1
    headers = {"Authorization": "Bearer uid:alice"}
    keyless = await api.client.patch(url, json={"counted": True}, headers=headers)
    assert keyless.status_code == 200 and keyless.json()["counted"] is True
    again = await api.client.patch(url, json={"counted": True}, headers=headers)
    assert again.status_code == 200
    assert len(await events(sessionmaker, sid, "object.restored")) == 1


# The body is a union tagged by ``counted`` (the published schema requires the reason to
# exclude), so an error inside it is located under its tag; a ``counted`` that is not a
# JSON boolean matches no member and is located at the body.
@pytest.mark.parametrize(
    ("body", "where"),
    [
        ({"counted": False}, ["body", "not_counted", "reason"]),
        ({"counted": False, "reason": "   "}, ["body", "not_counted", "reason"]),
        ({"counted": False, "reason": None}, ["body", "not_counted", "reason"]),
        ({"counted": False, "reason": "x" * 501}, ["body", "not_counted", "reason"]),
        ({"counted": True, "reason": "x" * 501}, ["body", "counted", "reason"]),
        ({"counted": "false", "reason": REASON}, ["body"]),
        ({"reason": REASON}, ["body"]),
        ({"counted": False, "reason": REASON, "label": "door"}, ["body", "not_counted", "label"]),
    ],
)
async def test_a_bad_body_is_422_and_changes_nothing(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    body: dict[str, Any],
    where: list[str],
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    response = await patch(api, "alice", one(sid, photo, ids["window"]), body)
    assert response.status_code == 422, response.text
    assert where in [e["loc"] for e in response.json()["errors"]]
    async with sessionmaker() as session:
        row = await session.get(PhotoObject, ids["window"])
    assert row is not None and row.counted and row.excluded_at is None


async def test_exactly_500_characters_is_a_reason(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    reason = "y" * 500
    response = await patch(
        api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": reason}
    )
    assert response.status_code == 200 and response.json()["exclusion"]["reason"] == reason


async def test_a_candidate_cannot_be_excluded_or_restored(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    url = one(sid, photo, ids["candidate"])
    exclude = await patch(api, "alice", url, {"counted": False, "reason": REASON})
    assert exclude.status_code == 409 and exclude.json()["code"] == "BASIS-OBJECT-010"
    assert exclude.json()["type"].endswith("not-counted")
    restore = await patch(api, "alice", url, {"counted": True})
    assert restore.status_code == 409 and restore.json()["code"] == "BASIS-OBJECT-011"
    assert await events(sessionmaker, sid, "object.excluded", "object.restored") == []


async def test_the_setting_off_refuses_the_route(
    off_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(off_api, seed, sessionmaker)
    response = await patch(
        off_api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": REASON}
    )
    assert response.status_code == 409 and response.json()["code"] == "BASIS-OBJECT-012"


async def test_only_an_engineer_of_the_firm_may_exclude(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    await seed.member(org, "erin", OrgRole.ENGINEER)
    body = {"counted": False, "reason": REASON}
    url = one(sid, photo, ids["window"])
    assert (await patch(api, "victor", url, body)).status_code == 403
    assert (await patch(api, "mallory", url, body)).status_code == 404
    done = await patch(api, "erin", url, body)
    assert done.status_code == 200 and done.json()["exclusion"]["by_name"] == "Erin"


@pytest.mark.parametrize(
    ("stage", "status", "code"),
    [
        (Stage.ASSETS_REVIEW, StudyStatus.WAITING_REVIEW, "BASIS-OBJECT-007"),
        (Stage.COMPONENTS_REVIEW, StudyStatus.WAITING_REVIEW, "BASIS-OBJECT-007"),
        (Stage.ROOMS_REVIEW, StudyStatus.PROCESSING, "BASIS-OBJECT-008"),
    ],
)
async def test_outside_the_open_edit_gate_it_is_409(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: Stage,
    status: StudyStatus,
    code: str,
) -> None:
    """The single rooms review edits at ``rooms_review`` (``components_gate`` null)."""
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study)
            .where(Study.id == uuid.UUID(sid))
            .values(current_stage=stage, status=status)
        )
    response = await patch(
        api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": REASON}
    )
    assert response.status_code == 409 and response.json()["code"] == code


async def test_with_the_components_gate_it_edits_at_the_components_review(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(
        api, seed, sessionmaker, Stage.COMPONENTS_REVIEW, components_gate=True
    )
    body = {"counted": False, "reason": REASON}
    assert (await patch(api, "alice", one(sid, photo, ids["window"]), body)).status_code == 200
    await seed.stage(sid, Stage.ROOMS_REVIEW, components_gate=True)
    refused = await patch(api, "alice", one(sid, photo, ids["cabinet"]), body)
    assert refused.status_code == 409 and refused.json()["code"] == "BASIS-OBJECT-007"


async def test_an_archived_study_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == uuid.UUID(sid)).values(status=StudyStatus.ARCHIVED)
        )
    response = await patch(
        api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": REASON}
    )
    assert response.status_code == 409


async def test_unknown_objects_and_photos_are_404(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    body = {"counted": False, "reason": REASON}
    unknown = await patch(api, "alice", one(sid, photo, uuid.uuid4()), body)
    assert unknown.status_code == 404 and unknown.json()["code"] == "BASIS-OBJECT-004"
    no_photo = await patch(api, "alice", one(sid, str(uuid.uuid4()), ids["window"]), body)
    assert no_photo.status_code == 404 and no_photo.json()["code"] == "BASIS-OBJECT-005"
    # Another photo's object, named under this photo.
    other = (await seed.outputs(sid))["photo"]
    async with session_scope(sessionmaker) as session:
        from basis.db.repositories.photo_object import PhotoObjectRepository

        await PhotoObjectRepository(session).record_localization(
            study_id=uuid.UUID(sid), document_id=uuid.UUID(other), revision=1, status="done"
        )
    elsewhere = await patch(api, "alice", one(sid, other, ids["window"]), body)
    assert elsewhere.status_code == 404 and elsewhere.json()["code"] == "BASIS-OBJECT-004"


async def test_before_the_photo_is_localized_it_is_409(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    sid = str(study["id"])
    photo = (await seed.outputs(sid))["photo"]
    await seed.stage(sid, Stage.ROOMS_REVIEW)
    response = await patch(
        api, "alice", one(sid, photo, uuid.uuid4()), {"counted": False, "reason": REASON}
    )
    assert response.status_code == 409 and response.json()["code"] == "BASIS-OBJECT-001"


async def test_a_type_change_is_an_exclusion_and_an_add(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The app's type change (web/FRONTEND_BACKLOG.md section 14): the detection is marked
    Not counted with the reason, and an engineer object with the new label is added at the
    same box; an excluded detection guards nothing, so a re-draw of its own label at the
    same place counts again (promoted from it, ``derived_from``)."""
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    url = one(sid, photo, ids["window"])
    await patch(api, "alice", url, {"counted": False, "reason": "Type changed to mirror: glass"})
    mirror = await api.post("alice", path(sid, photo), box("mirror", 0.1, 0.1, 0.5, 0.5))
    assert mirror.status_code == 201 and mirror.json()["object"]["label"] == "mirror"
    counts = (await api.get("alice", path(sid, photo))).json()["counts"]
    assert counts == {"kitchen_cabinet": 1, "mirror": 1}
    again = await api.post("alice", path(sid, photo), box("window", 0.1, 0.1, 0.5, 0.5))
    assert again.status_code == 201, again.text
    assert again.json()["created"] is True
    assert again.json()["object"]["derived_from"] == str(ids["window"])


async def test_the_rooms_suggested_counts_leave_an_excluded_object_out(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    async with session_scope(sessionmaker) as session:
        room = (
            await session.scalars(
                select(Room).where(Room.study_id == uuid.UUID(sid), Room.revision == 1)
            )
        ).first()
        assert room is not None
        session.add(
            PhotoRoom(
                study_id=uuid.UUID(sid),
                document_id=uuid.UUID(photo),
                room_id=room.id,
                revision=1,
            )
        )

    async def suggested() -> dict[str, int]:
        rooms = (await api.get("alice", f"/v1/studies/{sid}/rooms")).json()["items"]
        return {s["label"]: s["photo_count"] for r in rooms for s in r["suggested_counts"]}

    assert (await suggested()).get("window") == 1
    await patch(api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": REASON})
    assert "window" not in await suggested()
    await patch(api, "alice", one(sid, photo, ids["window"]), {"counted": True})
    assert (await suggested()).get("window") == 1


# --------------------------------------------------------------------------- unit price


def priced(entity_type: str, target: str, after: dict[str, Any], reason: str | None = "quote"):  # type: ignore[no-untyped-def]
    return {"entity_type": entity_type, "entity_id": target, "after": after, "reason": reason}


QUOTE = {"unit_cost": 512.4, "unit_cost_source": "Acme Cabinets quote 2024-118"}


@pytest.mark.parametrize(
    ("stage", "entity_type"),
    [(Stage.COMPONENTS_REVIEW, "component"), (Stage.ASSETS_REVIEW, "asset")],
)
async def test_a_unit_price_is_accepted_with_its_source_and_reason(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    stage: Stage,
    entity_type: str,
) -> None:
    sid = await study_at(api, seed, stage, revision=2, components_gate=True)
    targets = await seed.outputs(sid, revision=2)
    corrections = [
        priced(entity_type, targets[entity_type], QUOTE),
        priced(entity_type, targets[entity_type], {"unit_cost": None}, "back to the table"),
    ]
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(stage, 2, "reject", corrections=corrections),
    )
    assert response.status_code == 202, response.text
    assert response.json()["corrections_recorded"] == 2


@pytest.mark.parametrize(
    "after",
    [
        {"unit_cost": 0, "unit_cost_source": "x"},
        {"unit_cost": -1, "unit_cost_source": "x"},
        {"unit_cost": 10_000_000.01, "unit_cost_source": "x"},
        {"unit_cost": 12.345, "unit_cost_source": "x"},
        {"unit_cost": "12.50", "unit_cost_source": "x"},
        {"unit_cost": True, "unit_cost_source": "x"},
        {"unit_cost": 12.5},
        {"unit_cost": 12.5, "unit_cost_source": ""},
        {"unit_cost": 12.5, "unit_cost_source": "x" * 301},
        {"unit_cost_source": "a quote"},
        {"unit_cost": None, "unit_cost_source": "a quote"},
    ],
)
async def test_a_bad_unit_price_is_422_at_after_and_nothing_is_written(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], after: dict[str, Any]
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "assets_review", 2, "reject", corrections=[priced("asset", targets["asset"], after)]
        ),
    )
    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert {tuple(e["loc"]) for e in errors} == {("body", "corrections", 0, "after")}
    assert {e["type"] for e in errors} == {"correction_value"}
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)


@pytest.mark.parametrize("reason", [None, "", "  "])
async def test_a_unit_price_without_a_reason_is_422_at_reason(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], reason: str | None
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, revision=2, components_gate=True)
    targets = await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "components_review",
            2,
            "reject",
            corrections=[priced("component", targets["component"], QUOTE, reason)],
        ),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["loc"] == ["body", "corrections", 0, "reason"]
    assert error["type"] == "correction_reason"
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 2)


async def test_a_unit_price_on_a_concealed_row_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    await seed.outputs(sid, revision=2)
    async with session_scope(sessionmaker) as session:
        concealed = Asset(
            study_id=uuid.UUID(sid),
            revision=2,
            component_id=None,
            category="Foundations",
            bucket=Bucket.Y27_5,
            recovery_period=Decimal("27.5"),
            method=ClassificationMethod.RULE,
        )
        session.add(concealed)
        await session.flush()
        concealed_id = str(concealed.id)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("assets_review", 2, "reject", corrections=[priced("asset", concealed_id, QUOTE)]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["loc"] == ["body", "corrections", 0, "after"]
    assert "concealed row" in error["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)


async def test_with_the_setting_off_a_unit_price_is_422_and_the_rest_unchanged(
    off_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(off_api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    refused = await off_api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "assets_review", 2, "reject", corrections=[priced("asset", targets["asset"], QUOTE)]
        ),
    )
    assert refused.status_code == 422 and "turned off" in refused.json()["errors"][0]["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
    # The corrections that existed before are untouched by the setting.
    ok = await off_api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "assets_review",
            2,
            "reject",
            corrections=[priced("asset", targets["asset"], {"bucket": "y15"}, None)],
        ),
    )
    assert ok.status_code == 202, ok.text


async def test_a_draft_is_checked_the_same_way(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    url = f"/v1/studies/{sid}/drafts/assets_review"
    headers = {"Authorization": "Bearer uid:alice"}
    bad = {"revision": 2, "corrections": [priced("asset", targets["asset"], {"unit_cost": 0})]}
    response = await api.client.put(url, json=bad, headers=headers)
    assert response.status_code == 422, response.text
    good = {"revision": 2, "corrections": [priced("asset", targets["asset"], QUOTE)]}
    assert (await api.client.put(url, json=good, headers=headers)).status_code == 200


async def test_the_asset_reports_the_engineers_price_and_the_tables(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """``cost.override`` is the trail's override, ``cost.unit_cost`` the engineer's price
    and ``trail.replaced`` the table's (a trail as process_assets writes it)."""
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW)
    targets = await seed.outputs(sid)
    asset_id = uuid.UUID(targets["asset"])
    override = {
        "unit_cost": "512.4",
        "source": QUOTE["unit_cost_source"],
        "reason": "quote",
        "by": str(uuid.uuid4()),
        "by_name": "Erin Engineer",
        "at": "2026-11-05T18:30:00+00:00",
        "level": "component",
        "correction_id": None,
        "revision": 1,
    }
    quantity = {
        "value": "18",
        "unit": "EA",
        "formula": "18 EA",
        "method": "count",
        "inputs": {"component": "kitchen_cabinet"},
        "assumptions": [],
    }
    trail = {
        "version": 1,
        "kind": "engineer_price",
        "quantity": quantity,
        "base": None,
        "year": None,
        "location": None,
        "structure": None,
        "intake_version": None,
        "property_class": "residential_rental",
        "method": None,
        "cost_year": 2026,
        "location_factor": "1.000",
        "unit_cost": "512.40",
        "total": "9223.20",
        "rounding": "engineer",
        "priced_per": None,
        "missing": None,
        "override": override,
        "replaced": {
            "kind": "unpriced",
            "base": None,
            "unit_cost": None,
            "total": None,
            "rounding": None,
            "priced_per": None,
            "missing": "missing: no cost row for 'kitchen_cabinet' per EA",
        },
    }
    async with session_scope(sessionmaker) as session:
        session.add(
            Takeoff(study_id=uuid.UUID(sid), asset_id=asset_id, quantity=Decimal(18), unit="EA")
        )
        session.add(
            CostEstimate(
                study_id=uuid.UUID(sid),
                asset_id=asset_id,
                unit_cost=Decimal("512.40"),
                total=Decimal("9223.20"),
                source_row="engineer's unit price 512.4 per EA: ...",
                location_factor=Decimal("1.000"),
                cost_year=2026,
                trail=trail,
            )
        )
    response = await api.get("alice", f"/v1/studies/{sid}/assets")
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    cost = item["cost"]
    assert (cost["unit_cost"], cost["total"]) == ("512.40", "9223.20")
    assert cost["override"]["unit_cost"] == "512.4" and cost["override"]["level"] == "component"
    assert cost["override"]["by_name"] == "Erin Engineer"
    assert cost["trail"]["kind"] == "engineer_price"
    assert cost["trail"]["replaced"]["kind"] == "unpriced"


async def test_the_exclusion_is_carried_into_the_next_revision_and_history_is_kept(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A rejection, a reopen and a restore copy the photo's objects
    (``PhotoObjectRepository.carry``): the exclusion goes with the copy, and counting it
    again at the new revision leaves the decided revision's row as it was."""
    from basis.db.repositories.photo_object import PhotoObjectRepository

    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    await patch(api, "alice", one(sid, photo, ids["window"]), {"counted": False, "reason": REASON})
    async with session_scope(sessionmaker) as session:
        await PhotoObjectRepository(session).carry(uuid.UUID(sid), 1, 2, skip_photos=set())
    await seed.stage(sid, Stage.ROOMS_REVIEW, revision=2)
    listing = (await api.get("alice", path(sid, photo))).json()
    assert listing["revision"] == 2 and listing["counts"] == {"kitchen_cabinet": 1}
    [copy] = [i for i in listing["items"] if i["label"] == "window" and i["exclusion"]]
    assert copy["exclusion"]["reason"] == REASON and copy["exclusion"]["by_name"] == "Alice"
    copy_url = one(sid, photo, uuid.UUID(copy["id"]))
    assert (await patch(api, "alice", copy_url, {"counted": True})).status_code == 200
    old = (await api.get("alice", path(sid, photo), params={"revision": 1})).json()
    assert old["counts"] == {"kitchen_cabinet": 1}  # revision 1 keeps its exclusion
    # The decided revision's row cannot be edited from the current one.
    stale = await patch(api, "alice", one(sid, photo, ids["window"]), {"counted": True})
    assert stale.status_code == 404
