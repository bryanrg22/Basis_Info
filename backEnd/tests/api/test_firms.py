"""Invite-only firms on the real routes, with the product's onboarding (``invite_only_api``).

* Signing in creates the user and nothing else; ``GET /v1/me`` says so, and the study list
  and study creation answer 403 ``no-firm`` until an invitation is accepted.
* Owners (the admin role) list members, change roles, remove members, and create, list and
  revoke invitations. Engineers and viewers get 403 on every admin route; non-members 404.
* An invitation is answered by its invitee, signed in with the invited email, verified:
  unverified is 403, another email is 404 (indistinguishable from a missing id), and an
  expired, revoked, declined or other-account invitation is 409. Accepting is idempotent
  and never lowers a higher role.
* A firm always keeps an owner: demoting or removing the last one is 409, also when two
  owners demote each other at the same time. Removing a member ends their access at once.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import REPLAYED_HEADER
from basis.api.users import DETAIL_NO_FIRM
from basis.auth.firebase import Principal
from basis.db.enums import OrgRole
from basis.db.models import Invitation, Org, OrgMember, Study
from basis.db.session import session_scope
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

RANDOM = "00000000-0000-4000-8000-0000000000ff"


async def firm(seed: Seed, owner: str = "olivia", name: str = "Basis") -> str:
    """A firm whose only member is ``owner`` (as the operator CLI plus acceptance would)."""
    org = await seed.org(name)
    await seed.member(org, owner, OrgRole.OWNER, own_firm=False)
    return str(org)


async def invite(api: Api, owner: str, org: str, email: str, role: str) -> dict[str, Any]:
    response = await api.post(owner, f"/v1/orgs/{org}/invitations", {"email": email, "role": role})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def accept(api: Api, uid: str, invitation: str, **kwargs: Any) -> httpx.Response:
    return await api.post(uid, f"/v1/invitations/{invitation}/accept", **kwargs)


async def join(api: Api, owner: str, org: str, uid: str, role: str) -> None:
    invitation = await invite(api, owner, org, f"{uid}@example.test", role)
    response = await accept(api, uid, invitation["id"])
    assert response.status_code == 200, response.text


def problem_type(response: httpx.Response) -> str:
    assert response.headers["content-type"] == PROBLEM_JSON
    return str(response.json()["type"]).rsplit("/", 1)[-1]


async def count(sm: async_sessionmaker[AsyncSession], model: type[Any]) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def expire(sm: async_sessionmaker[AsyncSession], invitation: str) -> None:
    async with session_scope(sm) as session:
        await session.execute(
            update(Invitation)
            .where(Invitation.id == uuid.UUID(invitation))
            .values(
                created_at=func.now() - func.make_interval(0, 0, 0, 20),
                expires_at=func.now() - func.make_interval(0, 0, 0, 6),
            )
        )


# --------------------------------------------------------------------------- no firm


async def test_first_sign_in_creates_the_user_but_no_firm(
    invite_only_api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    response = await invite_only_api.get("nina", "/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user", "email_verified", "memberships", "pending_invitations"}
    assert set(body["user"]) == {"id", "email", "display_name"}
    assert (body["user"]["email"], body["user"]["display_name"]) == ("nina@example.test", "Nina")
    assert body["email_verified"] is True
    assert body["memberships"] == [] and body["pending_invitations"] == []
    assert await count(sessionmaker, Org) == 0
    assert await count(sessionmaker, OrgMember) == 0


async def test_a_user_in_no_firm_gets_no_firm_on_the_firm_wide_study_routes(
    invite_only_api: Api,
) -> None:
    listed = await invite_only_api.get("nina", "/v1/studies")
    created = await invite_only_api.post("nina", "/v1/studies", {"property_name": "12 Main"})
    in_org = await invite_only_api.post(
        "nina", "/v1/studies", {"property_name": "12 Main", "org_id": RANDOM}
    )
    one = await invite_only_api.get("nina", f"/v1/studies/{RANDOM}")

    for response in (listed, created, in_org):
        assert response.status_code == 403
        assert problem_type(response) == "no-firm"
        assert response.json()["detail"] == DETAIL_NO_FIRM
    # A study id is answered as always: not visible is 404, whatever the caller's firms.
    assert one.status_code == 404


async def test_a_member_with_no_studies_gets_an_empty_list(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "engineer")

    response = await invite_only_api.get("erin", "/v1/studies")

    assert response.status_code == 200
    assert response.json()["items"] == []


# --------------------------------------------------------------------------- /v1/me


async def test_me_lists_memberships_and_pending_invitations(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "bob@example.test", "engineer")

    owner = (await invite_only_api.get("olivia", "/v1/me")).json()
    bob = (await invite_only_api.get("bob", "/v1/me")).json()

    assert owner["memberships"] == [{"org_id": org, "org_name": "Basis", "role": "owner"}]
    assert owner["pending_invitations"] == []
    assert bob["memberships"] == []
    (pending,) = bob["pending_invitations"]
    assert pending == {
        "id": sent["id"],
        "org_id": org,
        "org_name": "Basis",
        "role": "engineer",
        "status": "pending",
        "invited_by_name": "Olivia",
        "invited_by_email": "olivia@example.test",
        "created_at": sent["created_at"],
        "expires_at": sent["expires_at"],
    }


async def test_me_lists_no_invitations_for_an_unverified_email(
    invite_only_api: Api, seed: Seed, verifier: FakeVerifier
) -> None:
    org = await firm(seed)
    await invite(invite_only_api, "olivia", org, "bob@example.test", "engineer")
    verifier.identities["bob"] = Principal(uid="bob", email="bob@example.test")

    body = (await invite_only_api.get("bob", "/v1/me")).json()

    assert body["email_verified"] is False
    assert body["pending_invitations"] == []


async def test_me_lists_neither_expired_nor_revoked_invitations(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first, second = await firm(seed, "olivia", "Basis"), await firm(seed, "omar", "Other")
    expired = await invite(invite_only_api, "olivia", first, "bob@example.test", "viewer")
    revoked = await invite(invite_only_api, "omar", second, "bob@example.test", "viewer")
    await expire(sessionmaker, expired["id"])
    await invite_only_api.delete("omar", f"/v1/orgs/{second}/invitations/{revoked['id']}")

    body = (await invite_only_api.get("bob", "/v1/me")).json()

    assert body["pending_invitations"] == []


# --------------------------------------------------------------------------- invitations


async def test_owner_creates_lists_and_revokes_invitations(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)

    created = await invite(invite_only_api, "olivia", org, "  Drew@Example.TEST\t", "engineer")
    assert created["email"] == "drew@example.test"
    assert (created["role"], created["status"], created["delivery"]) == (
        "engineer",
        "pending",
        "not_sent",
    )
    assert created["invited_by_email"] == "olivia@example.test"
    listed = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/invitations")).json()
    assert [i["id"] for i in listed["items"]] == [created["id"]]

    path = f"/v1/orgs/{org}/invitations/{created['id']}"
    revoked = await invite_only_api.delete("olivia", path)
    again = await invite_only_api.delete("olivia", path)

    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert again.status_code == 200 and again.json()["revoked_at"] == revoked.json()["revoked_at"]
    listed = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/invitations")).json()
    assert [i["status"] for i in listed["items"]] == ["revoked"]
    assert (await invite_only_api.get("drew", "/v1/me")).json()["pending_invitations"] == []
    gone = await accept(invite_only_api, "drew", created["id"])
    assert gone.status_code == 409 and problem_type(gone) == "invitation-revoked"


@pytest.mark.parametrize(
    "email",
    [
        "",
        "   ",
        "drew",
        "drew@example",
        "@example.test",
        "drew@@example.test",
        "dr ew@x.test",
        "drew@exämple.test",
        "x" * 250 + "@e.test",
        "drew@example.test\n",
    ],
)
async def test_invalid_emails_are_422(invite_only_api: Api, seed: Seed, email: str) -> None:
    org = await firm(seed)
    response = await invite_only_api.post(
        "olivia", f"/v1/orgs/{org}/invitations", {"email": email, "role": "engineer"}
    )
    assert response.status_code == 422, response.text


async def test_invalid_role_is_422(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    for role in ("admin", "Owner", None):
        response = await invite_only_api.post(
            "olivia", f"/v1/orgs/{org}/invitations", {"email": "a@b.test", "role": role}
        )
        assert response.status_code == 422


async def test_inviting_an_existing_member_is_409(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "viewer")

    for email in ("erin@example.test", " ERIN@example.test", "olivia@example.test"):
        response = await invite_only_api.post(
            "olivia", f"/v1/orgs/{org}/invitations", {"email": email, "role": "owner"}
        )
        assert response.status_code == 409
        assert problem_type(response) == "already-member"


async def test_a_duplicate_pending_invitation_is_409_and_reinviting_after_revoke_works(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    first = await invite(invite_only_api, "olivia", org, "drew@example.test", "engineer")

    duplicate = await invite_only_api.post(
        "olivia", f"/v1/orgs/{org}/invitations", {"email": "DREW@example.test", "role": "owner"}
    )
    assert duplicate.status_code == 409 and problem_type(duplicate) == "invitation-pending"

    await invite_only_api.delete("olivia", f"/v1/orgs/{org}/invitations/{first['id']}")
    second = await invite(invite_only_api, "olivia", org, "drew@example.test", "owner")
    assert second["id"] != first["id"] and second["role"] == "owner"


async def test_reinviting_after_expiry_works_and_the_old_one_stays_expired(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    org = await firm(seed)
    first = await invite(invite_only_api, "olivia", org, "drew@example.test", "engineer")
    await expire(sessionmaker, first["id"])

    expired = await accept(invite_only_api, "drew", first["id"])
    second = await invite(invite_only_api, "olivia", org, "drew@example.test", "engineer")

    assert expired.status_code == 409 and problem_type(expired) == "invitation-expired"
    listed = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/invitations")).json()
    assert [(i["id"], i["status"]) for i in listed["items"]] == [
        (second["id"], "pending"),
        (first["id"], "expired"),
    ]
    assert (await accept(invite_only_api, "drew", second["id"])).status_code == 200


async def test_the_invitation_lifetime_comes_from_settings(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
) -> None:
    app = build_api_app(
        api_settings(database_url, invitation_ttl_days=3),
        sessionmaker,
        storage,
        verifier,
        own_firms=False,
    )
    org = await firm(seed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        body = await invite(Api(client), "olivia", org, "drew@example.test", "viewer")
    async with sessionmaker() as session:
        row = await session.get(Invitation, uuid.UUID(body["id"]))
    assert row is not None
    assert (row.expires_at - row.created_at).days == 3


# --------------------------------------------------------------------------- authorization


ADMIN_ROUTES = [
    ("GET", "/v1/orgs/{org}/members", None),
    ("POST", "/v1/orgs/{org}/members/{user}/role", {"role": "viewer"}),
    ("DELETE", "/v1/orgs/{org}/members/{user}", None),
    ("GET", "/v1/orgs/{org}/invitations", None),
    ("POST", "/v1/orgs/{org}/invitations", {"email": "x@y.test", "role": "viewer"}),
    ("DELETE", "/v1/orgs/{org}/invitations/{invitation}", None),
]


async def send(api: Api, uid: str, method: str, path: str, body: Any) -> httpx.Response:
    if method == "GET":
        return await api.get(uid, path)
    if method == "DELETE":
        return await api.delete(uid, path)
    return await api.post(uid, path, body)


@pytest.mark.parametrize(("method", "template", "body"), ADMIN_ROUTES)
async def test_engineers_and_viewers_get_403_and_non_members_404_on_every_admin_route(
    invite_only_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    method: str,
    template: str,
    body: Any,
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "engineer")
    await join(invite_only_api, "olivia", org, "vic", "viewer")
    await firm(seed, "stan", "Elsewhere")  # an owner, of another firm
    invitation = await invite(invite_only_api, "olivia", org, "drew@example.test", "viewer")
    olivia = (await invite_only_api.get("olivia", "/v1/me")).json()["user"]["id"]
    path = template.format(org=org, user=olivia, invitation=invitation["id"])
    before = (await count(sessionmaker, OrgMember), await count(sessionmaker, Invitation))

    for uid in ("erin", "vic"):
        response = await send(invite_only_api, uid, method, path, body)
        assert response.status_code == 403, (uid, response.text)
        assert problem_type(response) == "forbidden"
    for uid in ("stan", "nina"):  # another firm's owner; a user in no firm
        response = await send(invite_only_api, uid, method, path, body)
        assert response.status_code == 404, (uid, response.text)
        assert response.json()["detail"] == "Organization not found."
    unknown = await send(invite_only_api, "olivia", method, path.replace(org, RANDOM), body)
    assert unknown.status_code == 404
    assert (await count(sessionmaker, OrgMember), await count(sessionmaker, Invitation)) == before


# --------------------------------------------------------------------------- accepting


async def test_accepting_joins_the_firm(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")

    response = await accept(invite_only_api, "erin", sent["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["membership"] == {"org_id": org, "org_name": "Basis", "role": "engineer"}
    assert body["invitation"]["status"] == "accepted"
    me = (await invite_only_api.get("erin", "/v1/me")).json()
    assert me["memberships"] == [body["membership"]] and me["pending_invitations"] == []
    study = await invite_only_api.create_study("erin")  # the firm is their only one
    assert study["org_id"] == org
    listed = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/invitations")).json()
    (row,) = listed["items"]
    assert (row["status"], row["accepted_by"]) == ("accepted", me["user"]["id"])
    members = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/members")).json()["items"]
    assert [(m["email"], m["role"]) for m in members] == [
        ("olivia@example.test", "owner"),
        ("erin@example.test", "engineer"),
    ]
    assert set(members[1]) == {
        "user_id",
        "email",
        "display_name",
        "role",
        "joined_at",
        "last_seen_at",
    }


async def test_accepting_needs_a_verified_email(
    invite_only_api: Api, seed: Seed, verifier: FakeVerifier
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")
    verifier.identities["erin"] = Principal(uid="erin", email="erin@example.test")

    for action in ("accept", "decline"):
        response = await invite_only_api.post("erin", f"/v1/invitations/{sent['id']}/{action}")
        assert response.status_code == 403
        assert problem_type(response) == "email-unverified"
    assert (await invite_only_api.get("erin", "/v1/me")).json()["memberships"] == []


async def test_another_verified_email_cannot_tell_the_invitation_exists(
    invite_only_api: Api, seed: Seed, verifier: FakeVerifier
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")
    verifier.identities["mallory"] = Principal(
        uid="mallory", email="mallory@example.test", email_verified=True
    )

    theirs = await accept(invite_only_api, "mallory", sent["id"])
    missing = await accept(invite_only_api, "mallory", RANDOM)

    assert theirs.status_code == missing.status_code == 404
    strip = lambda r: {k: v for k, v in r.json().items() if k not in ("instance", "request_id")}  # noqa: E731
    assert strip(theirs) == strip(missing)
    assert (await accept(invite_only_api, "erin", sent["id"])).status_code == 200


async def test_an_email_matches_whatever_its_case(
    invite_only_api: Api, seed: Seed, verifier: FakeVerifier
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "Erin@Example.test", "viewer")
    verifier.identities["erin"] = Principal(
        uid="erin", email="ERIN@example.TEST", email_verified=True
    )

    assert (await invite_only_api.get("erin", "/v1/me")).json()["pending_invitations"][0][
        "id"
    ] == sent["id"]
    assert (await accept(invite_only_api, "erin", sent["id"])).status_code == 200


async def test_an_invitation_accepted_by_another_account_is_409(
    invite_only_api: Api, seed: Seed, verifier: FakeVerifier
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")
    # Two sign-ins with the same verified email (e.g. different providers, not linked).
    verifier.identities["erin-2"] = Principal(
        uid="erin-2", email="erin@example.test", email_verified=True
    )
    assert (await accept(invite_only_api, "erin", sent["id"])).status_code == 200

    response = await accept(invite_only_api, "erin-2", sent["id"])

    assert response.status_code == 409 and problem_type(response) == "invitation-accepted"
    assert (await invite_only_api.get("erin-2", "/v1/me")).json()["memberships"] == []


async def test_accepting_again_is_idempotent(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")

    first = await accept(invite_only_api, "erin", sent["id"], key="accept-1")
    replay = await accept(invite_only_api, "erin", sent["id"], key="accept-1")
    again = await accept(invite_only_api, "erin", sent["id"])  # a new key: answered again

    assert first.status_code == replay.status_code == again.status_code == 200
    assert replay.headers[REPLAYED_HEADER] == "true"
    assert replay.json() == first.json() == again.json()


async def test_a_removed_member_replaying_an_accepted_invitation_stays_out(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")
    assert (await accept(invite_only_api, "erin", sent["id"])).status_code == 200
    erin = (await invite_only_api.get("erin", "/v1/me")).json()["user"]["id"]
    assert (
        await invite_only_api.delete("olivia", f"/v1/orgs/{org}/members/{erin}")
    ).status_code == 200

    replay = await accept(invite_only_api, "erin", sent["id"])

    assert replay.status_code == 200 and replay.json()["membership"] is None
    assert (await invite_only_api.get("erin", "/v1/me")).json()["memberships"] == []


async def test_accepting_never_lowers_a_higher_role(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "carol@example.test", "viewer")
    # Carol became an owner some other way while the viewer invitation was pending.
    await seed.member(uuid.UUID(org), "carol", OrgRole.OWNER, own_firm=False)

    response = await accept(invite_only_api, "carol", sent["id"])

    assert response.status_code == 200
    assert response.json()["membership"]["role"] == "owner"
    members = (await invite_only_api.get("carol", f"/v1/orgs/{org}/members")).json()["items"]
    assert {m["email"]: m["role"] for m in members}["carol@example.test"] == "owner"


async def test_a_higher_role_invitation_raises_the_role(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "carol@example.test", "owner")
    await seed.member(uuid.UUID(org), "carol", OrgRole.VIEWER, own_firm=False)

    response = await accept(invite_only_api, "carol", sent["id"])

    assert response.json()["membership"]["role"] == "owner"


async def test_declining(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")

    declined = await invite_only_api.post("erin", f"/v1/invitations/{sent['id']}/decline")
    again = await invite_only_api.post("erin", f"/v1/invitations/{sent['id']}/decline")
    late = await accept(invite_only_api, "erin", sent["id"])
    revoke = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/invitations/{sent['id']}")

    assert declined.status_code == again.status_code == 200
    assert declined.json() == again.json()
    assert declined.json()["invitation"]["status"] == "declined"
    assert declined.json()["membership"] is None
    assert late.status_code == 409 and problem_type(late) == "invitation-declined"
    assert revoke.status_code == 409 and problem_type(revoke) == "invitation-declined"
    listed = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/invitations")).json()
    assert listed["items"][0]["status"] == "declined"
    assert (await invite_only_api.get("erin", "/v1/me")).json()["memberships"] == []
    # Declined does not block inviting again.
    await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")


async def test_an_accepted_invitation_cannot_be_revoked(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    sent = await invite(invite_only_api, "olivia", org, "erin@example.test", "engineer")
    await accept(invite_only_api, "erin", sent["id"])

    response = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/invitations/{sent['id']}")

    assert response.status_code == 409 and problem_type(response) == "invitation-accepted"


async def test_another_firms_invitation_is_404_to_revoke(invite_only_api: Api, seed: Seed) -> None:
    mine, theirs = await firm(seed, "olivia", "Basis"), await firm(seed, "omar", "Other")
    sent = await invite(invite_only_api, "omar", theirs, "erin@example.test", "engineer")

    response = await invite_only_api.delete("olivia", f"/v1/orgs/{mine}/invitations/{sent['id']}")

    assert response.status_code == 404


# --------------------------------------------------------------------------- members


async def member_id(api: Api, uid: str) -> str:
    return str((await api.get(uid, "/v1/me")).json()["user"]["id"])


async def test_owner_changes_roles(invite_only_api: Api, seed: Seed) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "engineer")
    erin = await member_id(invite_only_api, "erin")
    path = f"/v1/orgs/{org}/members/{erin}/role"

    promoted = await invite_only_api.post("olivia", path, {"role": "owner"})
    same = await invite_only_api.post("olivia", path, {"role": "owner"})
    missing = await invite_only_api.post(
        "olivia", f"/v1/orgs/{org}/members/{RANDOM}/role", {"role": "viewer"}
    )
    bad = await invite_only_api.post("olivia", path, {"role": "admin"})

    assert promoted.status_code == same.status_code == 200
    assert promoted.json()["role"] == "owner" and promoted.json()["email"] == "erin@example.test"
    assert missing.status_code == 404 and missing.json()["detail"] == "Member not found."
    assert bad.status_code == 422
    # Two owners now: Erin may administer, and demote Olivia.
    olivia = await member_id(invite_only_api, "olivia")
    demoted = await invite_only_api.post(
        "erin", f"/v1/orgs/{org}/members/{olivia}/role", {"role": "viewer"}
    )
    assert demoted.status_code == 200 and demoted.json()["role"] == "viewer"
    forbidden = await invite_only_api.get("olivia", f"/v1/orgs/{org}/members")
    assert forbidden.status_code == 403


async def test_the_last_owner_cannot_be_demoted_or_removed(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "engineer")
    olivia = await member_id(invite_only_api, "olivia")

    for role in ("engineer", "viewer"):
        demoted = await invite_only_api.post(
            "olivia", f"/v1/orgs/{org}/members/{olivia}/role", {"role": role}
        )
        assert demoted.status_code == 409 and problem_type(demoted) == "last-owner"
    removed = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/members/{olivia}")
    assert removed.status_code == 409 and problem_type(removed) == "last-owner"
    members = (await invite_only_api.get("olivia", f"/v1/orgs/{org}/members")).json()["items"]
    assert [m["role"] for m in members] == ["owner", "engineer"]


async def test_several_owners_and_an_owner_leaving_while_another_remains(
    invite_only_api: Api, seed: Seed
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "otto", "owner")
    await join(invite_only_api, "otto", org, "ophelia", "owner")  # an invited owner invites
    members = (await invite_only_api.get("ophelia", f"/v1/orgs/{org}/members")).json()["items"]
    assert [m["role"] for m in members] == ["owner"] * 3

    olivia = await member_id(invite_only_api, "olivia")
    left = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/members/{olivia}")

    assert left.status_code == 200 and left.json()["role"] == "owner"
    assert (await invite_only_api.get("olivia", f"/v1/orgs/{org}/members")).status_code == 404
    assert len((await invite_only_api.get("otto", f"/v1/orgs/{org}/members")).json()["items"]) == 2


async def test_two_owners_demoting_each_other_at_once_leave_an_owner(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "otto", "owner")
    olivia = await member_id(invite_only_api, "olivia")
    otto = await member_id(invite_only_api, "otto")

    results = await asyncio.gather(
        invite_only_api.post("olivia", f"/v1/orgs/{org}/members/{otto}/role", {"role": "engineer"}),
        invite_only_api.post("otto", f"/v1/orgs/{org}/members/{olivia}/role", {"role": "engineer"}),
    )

    assert sorted(r.status_code for r in results) == [200, 403]
    async with sessionmaker() as session:
        owners = await session.scalar(
            select(func.count()).where(
                OrgMember.org_id == uuid.UUID(org), OrgMember.role == "owner"
            )
        )
    assert owners == 1


async def test_a_removed_member_loses_access_at_once_and_their_studies_stay(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    org = await firm(seed)
    await join(invite_only_api, "olivia", org, "erin", "engineer")
    study = await invite_only_api.create_study("erin", "7 Elm St")
    assert (await invite_only_api.get("erin", f"/v1/studies/{study['id']}")).status_code == 200
    erin = await member_id(invite_only_api, "erin")

    removed = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/members/{erin}")

    assert removed.status_code == 200 and removed.json()["user_id"] == erin
    assert (await invite_only_api.get("erin", f"/v1/studies/{study['id']}")).status_code == 404
    listed = await invite_only_api.get("erin", "/v1/studies")
    assert listed.status_code == 403 and problem_type(listed) == "no-firm"
    kept = await invite_only_api.get("olivia", f"/v1/studies/{study['id']}")
    assert kept.status_code == 200 and kept.json()["created_by"] == erin
    async with sessionmaker() as session:
        assert (await session.get(Study, uuid.UUID(study["id"]))) is not None
    missing = await invite_only_api.delete("olivia", f"/v1/orgs/{org}/members/{erin}")
    assert missing.status_code == 404


# --------------------------------------------------------------------------- rate limits


@pytest.fixture
async def limited(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> AsyncIterator[Api]:
    settings = api_settings(
        database_url, rate_limit_reads_per_min=2, rate_limit_mutations_per_min=1
    )
    app: FastAPI = build_api_app(settings, sessionmaker, storage, verifier, own_firms=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


async def test_firm_routes_are_rate_limited_per_user(limited: Api, seed: Seed) -> None:
    org = await firm(seed)
    reads = [await limited.get("olivia", f"/v1/orgs/{org}/members") for _ in range(3)]
    writes = [
        await limited.post("olivia", f"/v1/orgs/{org}/invitations", {"email": e, "role": "viewer"})
        for e in ("a@x.test", "b@x.test")
    ]
    answers = [await accept(limited, "erin", RANDOM) for _ in range(2)]

    assert [r.status_code for r in reads] == [200, 200, 429]
    assert [r.status_code for r in writes] == [201, 429]
    assert [r.status_code for r in answers] == [404, 429]
    assert (await limited.get("nina", "/v1/me")).status_code == 200  # another user's budget
