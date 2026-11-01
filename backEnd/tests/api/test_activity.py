"""``GET /v1/me/activity`` (one user's own actions across their studies) and ``GET
/v1/orgs/{org_id}/activity`` (the firm's own history, ``org_events``), docs/api/
TIMELINE_AND_AUDIT.md "Your activity" and "Firm activity". Real Postgres.

The firm's history is written by the repositories the routes and the operator CLI share,
so every firm action is exercised through its real route here (invite, accept, decline,
revoke, role change, removal, profile) and the CLI's firm creation through
``basis.admin.create_firm``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.admin import create_firm
from basis.db.enums import OrgRole
from basis.db.models import Invitation, OrgEvent, OrgMember, StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E

pytestmark = pytest.mark.integration

PROFILE: dict[str, Any] = {"name": "Example Engineering", "address": "1 Example Way"}


async def activity(api: Api, uid: str = "alice", **params: Any) -> dict[str, Any]:
    response = await api.get(uid, "/v1/me/activity", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def backdated_event(
    sm: async_sessionmaker[AsyncSession], study_id: str, actor: uuid.UUID, days_ago: int
) -> int:
    """An event written ``days_ago`` (the table is append-only, so it is inserted with its
    time rather than updated afterwards)."""
    async with session_scope(sm) as session:
        row = StudyEvent(
            study_id=uuid.UUID(study_id),
            event_type="study.intake_set",
            actor_type="engineer",
            actor_id=actor,
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
        session.add(row)
        await session.flush()
        return row.seq


# --------------------------------------------------------------------------- /me/activity


async def test_my_activity_lists_my_own_events_with_the_study_newest_first(
    api: Api, seed: Seed
) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.OWNER)
    await seed.member(org, "bob", OrgRole.ENGINEER)
    mine = await api.create_study("alice", "Mine", org_id=str(org))
    bobs = await api.create_study("bob", "Bob's", org_id=str(org))
    await api.delete("alice", f"/v1/studies/{bobs['id']}")  # alice acts on bob's study

    page = await activity(api)

    assert [(i["event_type"], i["property_name"]) for i in page["items"]] == [
        ("study.archived", "Bob's"),
        ("study.created", "Mine"),
    ]
    archived, created = page["items"]
    assert archived["study_id"] == bobs["id"] and created["study_id"] == mine["id"]
    assert archived["actor_id"] == created["actor_id"] == mine["created_by"]
    assert archived["seq"] > created["seq"]
    assert archived["request_id"]  # the timeline's members, verbatim
    assert page["next_cursor"] is None and page["until"] is None
    assert datetime.fromisoformat(page["since"]) < datetime.now(UTC) - timedelta(days=89)
    # Bob sees only what he did; his study's archive was alice's action.
    assert [i["event_type"] for i in (await activity(api, "bob"))["items"]] == ["study.created"]


async def test_my_activity_hides_studies_of_a_firm_i_left(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    kept = await api.create_study("alice", "Kept")  # in her own firm
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.ENGINEER)
    gone = await api.create_study("alice", "In the firm", org_id=str(org))
    assert {i["study_id"] for i in (await activity(api))["items"]} == {kept["id"], gone["id"]}
    user = uuid.UUID(kept["created_by"])
    async with session_scope(sessionmaker) as session:
        await session.execute(
            delete(OrgMember).where(OrgMember.org_id == org, OrgMember.user_id == user)
        )

    page = await activity(api)

    assert [i["study_id"] for i in page["items"]] == [kept["id"]]


async def test_my_activity_pages_newest_first_without_gaps_or_repeats(api: Api) -> None:
    created = [(await api.create_study("alice", f"S{i}"))["id"] for i in range(5)]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = await activity(api, **params)
        assert len(page["items"]) <= 2
        seen += [i["study_id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3 and seen == list(reversed(created))


async def test_my_activity_filters_by_event_type_and_window(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice", "Harbor")
    await api.delete("alice", f"/v1/studies/{study['id']}")
    await api.post("alice", f"/v1/studies/{study['id']}/unarchive")

    only = await activity(api, event_type=["study.archived", "study.unarchived"])
    assert [i["event_type"] for i in only["items"]] == ["study.unarchived", "study.archived"]
    assert (await activity(api, event_type="nothing.like_it"))["items"] == []
    # A window around the creation only.
    created_at = datetime.fromisoformat(
        next(i["at"] for i in (await activity(api))["items"] if i["event_type"] == "study.created")
    )
    window = await activity(api, since=created_at.isoformat(), until=created_at.isoformat())
    assert [i["event_type"] for i in window["items"]] == ["study.created"]
    assert window["since"] == window["until"]
    assert datetime.fromisoformat(window["since"]) == created_at
    # since after until: an empty window, not an error.
    later = (created_at + timedelta(seconds=1)).isoformat()
    assert (await activity(api, since=later, until=created_at.isoformat()))["items"] == []
    # A naive time is read as UTC.
    naive = created_at.replace(tzinfo=None).isoformat()
    assert len((await activity(api, since=naive))["items"]) == 3


async def test_my_activity_defaults_to_the_last_90_days(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice", "Old")
    actor = uuid.UUID(study["created_by"])
    old = await backdated_event(sessionmaker, study["id"], actor, days_ago=100)
    recent = await backdated_event(sessionmaker, study["id"], actor, days_ago=80)

    default = await activity(api)
    seqs = [i["seq"] for i in default["items"]]
    assert recent in seqs and old not in seqs
    since = datetime.fromisoformat(default["since"])
    assert timedelta(days=89, hours=23) < datetime.now(UTC) - since < timedelta(days=90, minutes=1)
    # Pinning the window further back brings the old event in; the response echoes it.
    pinned = await activity(api, since=(datetime.now(UTC) - timedelta(days=200)).isoformat())
    assert old in [i["seq"] for i in pinned["items"]]
    assert datetime.now(UTC) - datetime.fromisoformat(pinned["since"]) > timedelta(days=199)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"cursor": ""},
        {"cursor": "x"},
        {"cursor": "9" * 20},
        {"since": "yesterday"},
        {"event_type": "a\x00b"},
        {"event_type": "x" * 101},
    ],
)
async def test_my_activity_rejects_bad_parameters(api: Api, params: dict[str, Any]) -> None:
    response = await api.get("alice", "/v1/me/activity", params=params)
    assert response.status_code == 422
    if "cursor" in params and params["cursor"]:
        assert response.json()["code"] == E.API_INVALID_CURSOR


async def test_my_activity_without_a_firm_is_empty_not_an_error(invite_only_api: Api) -> None:
    page = await activity(invite_only_api, "nina")
    assert page["items"] == [] and page["next_cursor"] is None


# --------------------------------------------------------------------------- /orgs/{id}/activity


async def firm(seed: Seed, owner: str = "olivia", name: str = "Basis") -> str:
    org = await seed.org(name)
    await seed.member(org, owner, OrgRole.OWNER, own_firm=False)
    return str(org)


async def invite(api: Api, owner: str, org: str, email: str, role: str) -> dict[str, Any]:
    response = await api.post(owner, f"/v1/orgs/{org}/invitations", {"email": email, "role": role})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def org_activity(api: Api, uid: str, org: str, **params: Any) -> list[dict[str, Any]]:
    response = await api.get(uid, f"/v1/orgs/{org}/activity", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["org_id"] == org
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def user_id(sm: async_sessionmaker[AsyncSession], org: str, role: OrgRole) -> str:
    async with sm() as session:
        found = await session.scalar(
            select(OrgMember.user_id).where(
                OrgMember.org_id == uuid.UUID(org), OrgMember.role == role.value
            )
        )
    assert found is not None
    return str(found)


async def test_every_firm_action_is_recorded_with_who_did_what(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    api = invite_only_api
    org = await firm(seed)
    owner = await user_id(sessionmaker, org, OrgRole.OWNER)
    # Invite, accept, change role, invite another, revoke, invite a third, decline, profile.
    first = await invite(api, "olivia", org, "ed@example.test", "engineer")
    accepted = await api.post("ed", f"/v1/invitations/{first['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    ed = accepted.json()["membership"] and await user_id(sessionmaker, org, OrgRole.ENGINEER)
    promoted = await api.post("olivia", f"/v1/orgs/{org}/members/{ed}/role", {"role": "owner"})
    assert promoted.status_code == 200, promoted.text
    second = await invite(api, "olivia", org, "vi@example.test", "viewer")
    revoked = await api.delete("olivia", f"/v1/orgs/{org}/invitations/{second['id']}")
    assert revoked.status_code == 200, revoked.text
    third = await invite(api, "olivia", org, "dan@example.test", "viewer")
    declined = await api.post("dan", f"/v1/invitations/{third['id']}/decline")
    assert declined.status_code == 200, declined.text
    profile = await api.client.put(
        f"/v1/orgs/{org}/profile", json=PROFILE, headers=bearer("olivia")
    )
    assert profile.status_code == 200, profile.text

    items = await org_activity(api, "olivia", org)

    assert [i["event_type"] for i in items] == [
        "profile.set",
        "invitation.declined",
        "invitation.created",
        "invitation.revoked",
        "invitation.created",
        "member.role_changed",
        "invitation.accepted",
        "invitation.created",
    ]
    assert [i["seq"] for i in items] == sorted((i["seq"] for i in items), reverse=True)
    by_type = {i["event_type"]: i for i in items}
    created = items[-1]
    assert created["actor_type"] == "engineer" and created["actor_id"] == owner
    assert created["entity_type"] == "invitation" and created["entity_id"] == first["id"]
    assert created["after"]["email"] == "ed@example.test" and created["after"]["role"] == "engineer"
    assert created["detail"] == {"replaced": None} and created["request_id"]
    accepted_event = by_type["invitation.accepted"]
    assert accepted_event["actor_id"] == accepted_event["subject_user_id"] == ed
    assert accepted_event["after"] == {"email": "ed@example.test", "role": "engineer"}
    assert accepted_event["detail"] == {"role_before": None}
    role = by_type["member.role_changed"]
    assert role["actor_id"] == owner and role["subject_user_id"] == ed
    assert role["before"] == {"role": "engineer"} and role["after"] == {"role": "owner"}
    revoked_event = by_type["invitation.revoked"]
    assert revoked_event["entity_id"] == second["id"] and revoked_event["actor_id"] == owner
    assert revoked_event["before"]["email"] == "vi@example.test"
    assert revoked_event["detail"] == {"reason": "revoked"}
    declined_event = by_type["invitation.declined"]
    assert declined_event["entity_id"] == third["id"]
    assert declined_event["actor_id"] == declined_event["subject_user_id"]
    assert declined_event["actor_id"] not in (owner, ed)
    profile_event = by_type["profile.set"]
    assert profile_event["actor_id"] == owner and profile_event["entity_id"] == org
    assert profile_event["detail"] == {
        "version_before": 0,
        "version_after": 1,
        "if_match": False,
        "changed": ["address", "name"],
    }
    assert profile_event["before"] is None and profile_event["after"] is None  # never the logo


async def test_a_removal_records_the_member_and_the_invitations_it_revoked(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    api = invite_only_api
    org = await firm(seed)
    owner = await user_id(sessionmaker, org, OrgRole.OWNER)
    invitation = await invite(api, "olivia", org, "ed@example.test", "engineer")
    assert (await api.post("ed", f"/v1/invitations/{invitation['id']}/accept")).status_code == 200
    ed = await user_id(sessionmaker, org, OrgRole.ENGINEER)
    # An invitation to the same email is refused while ed is a member, so an open one can
    # only predate the membership: seed one directly, as a stale duplicate would be.
    async with session_scope(sessionmaker) as session:
        stale = Invitation(
            org_id=uuid.UUID(org),
            email="ed@example.test",
            role="viewer",
            created_by=uuid.UUID(owner),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(stale)
        await session.flush()
        stale_id = str(stale.id)

    removed = await api.delete("olivia", f"/v1/orgs/{org}/members/{ed}")
    assert removed.status_code == 200, removed.text

    items = await org_activity(
        api, "olivia", org, event_type=["member.removed", "invitation.revoked"]
    )
    assert [i["event_type"] for i in items] == ["invitation.revoked", "member.removed"]
    revoked, removal = items
    assert removal["subject_user_id"] == ed and removal["actor_id"] == owner
    assert removal["before"] == {"role": "engineer"}
    assert removal["detail"] == {"self": False, "invitations_revoked": [stale_id]}
    assert revoked["entity_id"] == stale_id and revoked["subject_user_id"] == ed
    assert revoked["detail"] == {"reason": "member_removed"}


async def test_an_owner_leaving_is_recorded_as_self(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    api = invite_only_api
    org = await firm(seed)
    await seed.member(uuid.UUID(org), "oscar", OrgRole.OWNER, own_firm=False)
    olivia = await user_id(sessionmaker, org, OrgRole.OWNER)
    left = await api.delete("olivia", f"/v1/orgs/{org}/members/{olivia}")
    assert left.status_code == 200, left.text

    [removal] = await org_activity(api, "oscar", org, event_type="member.removed")

    assert removal["actor_id"] == removal["subject_user_id"] == olivia
    assert removal["detail"]["self"] is True
    # olivia is out: the history is no longer hers to read.
    assert (await api.get("olivia", f"/v1/orgs/{org}/activity")).status_code == 404


async def test_the_operator_cli_records_the_firm_and_its_owner_invitation_as_the_system(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(sessionmaker) as session:
        created = await create_firm(
            session, "Fresh Firm", "owner@example.test", ttl=timedelta(days=7)
        )
    org = created["firm"]["org_id"]
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(
                    select(OrgEvent).where(OrgEvent.org_id == uuid.UUID(org)).order_by(OrgEvent.seq)
                )
            ).all()
        )
    assert [(r.event_type, r.actor_type, r.actor_id) for r in rows] == [
        ("org.created", "system", None),
        ("invitation.created", "system", None),
    ]
    assert rows[0].after == {"name": "Fresh Firm"} and rows[0].entity_id == uuid.UUID(org)
    assert rows[1].after["email"] == "owner@example.test" and rows[1].after["role"] == "owner"
    # Once the owner accepts, they read the history, the CLI's rows included.
    api = invite_only_api
    accepted = await api.post("owner", f"/v1/invitations/{rows[1].entity_id}/accept")
    assert accepted.status_code == 200, accepted.text
    types = [i["event_type"] for i in await org_activity(api, "owner", org)]
    assert types == ["invitation.accepted", "invitation.created", "org.created"]


async def test_reinviting_after_expiry_records_the_closed_invitation(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    api = invite_only_api
    org = await firm(seed)
    old = await invite(api, "olivia", org, "late@example.test", "viewer")
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Invitation)
            .where(Invitation.id == uuid.UUID(old["id"]))
            .values(
                created_at=datetime.now(UTC) - timedelta(days=30),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    new = await invite(api, "olivia", org, "late@example.test", "engineer")

    items = await org_activity(api, "olivia", org)

    assert [(i["event_type"], i["entity_id"]) for i in items] == [
        ("invitation.created", new["id"]),
        ("invitation.revoked", old["id"]),
        ("invitation.created", old["id"]),
    ]
    assert items[1]["detail"] == {"reason": "expired"}
    assert items[0]["detail"] == {"replaced": old["id"]}


async def test_org_activity_pages_and_filters(invite_only_api: Api, seed: Seed) -> None:
    api = invite_only_api
    org = await firm(seed)
    invited = [
        (await invite(api, "olivia", org, f"m{i}@example.test", "viewer"))["id"] for i in range(5)
    ]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await api.get("olivia", f"/v1/orgs/{org}/activity", params=params)
        assert response.status_code == 200
        page = response.json()
        seen += [i["entity_id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and seen == list(reversed(invited))

    await api.delete("olivia", f"/v1/orgs/{org}/invitations/{invited[0]}")
    only = await org_activity(api, "olivia", org, event_type="invitation.revoked")
    assert [i["entity_id"] for i in only] == [invited[0]]
    at = datetime.fromisoformat(only[0]["at"])
    window = await org_activity(api, "olivia", org, since=at.isoformat(), until=at.isoformat())
    assert [i["event_type"] for i in window] == ["invitation.revoked"]
    assert (
        await org_activity(
            api, "olivia", org, since=at.isoformat(), until=(at - timedelta(seconds=1)).isoformat()
        )
        == []
    )
    bad = await api.get("olivia", f"/v1/orgs/{org}/activity", params={"cursor": "x"})
    assert bad.status_code == 422 and bad.json()["code"] == E.API_INVALID_CURSOR


async def test_org_activity_is_owner_only_and_404_for_outsiders(
    invite_only_api: Api, seed: Seed
) -> None:
    api = invite_only_api
    org = await firm(seed)
    await seed.member(uuid.UUID(org), "eng", OrgRole.ENGINEER, own_firm=False)
    await seed.member(uuid.UUID(org), "view", OrgRole.VIEWER, own_firm=False)

    assert (await api.get("eng", f"/v1/orgs/{org}/activity")).status_code == 403
    assert (await api.get("view", f"/v1/orgs/{org}/activity")).status_code == 403
    stranger = await api.get("stranger", f"/v1/orgs/{org}/activity")
    assert stranger.status_code == 404 and stranger.json()["code"] == E.API_ORG_NOT_FOUND
    assert (await api.get("olivia", f"/v1/orgs/{uuid.uuid4()}/activity")).status_code == 404
    assert (await api.get("olivia", "/v1/orgs/not-a-uuid/activity")).status_code == 422


async def test_org_events_are_append_only(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    org = await firm(seed)
    await invite(invite_only_api, "olivia", org, "x@example.test", "viewer")
    for statement in (
        update(OrgEvent).where(OrgEvent.org_id == uuid.UUID(org)).values(event_type="x"),
        delete(OrgEvent).where(OrgEvent.org_id == uuid.UUID(org)),
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            async with session_scope(sessionmaker) as session:
                await session.execute(statement)


async def test_a_failed_request_leaves_no_firm_event(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The event is written in the request's transaction: a refused write rolls it back."""
    api = invite_only_api
    org = await firm(seed)
    olivia = await user_id(sessionmaker, org, OrgRole.OWNER)
    last_owner = await api.delete("olivia", f"/v1/orgs/{org}/members/{olivia}")
    assert last_owner.status_code == 409
    assert await org_activity(api, "olivia", org) == []
