"""The operator bootstrap ``python -m basis.admin`` (invite-only firms).

* ``create-firm`` creates the firm and a pending owner invitation (no member is added:
  the owner accepts in the app); re-running it reports the existing state; another email
  for an existing name, or a second firm with the name, is refused unless
  ``--allow-duplicate-name``; an expired owner invitation is replaced on re-run.
* ``invite`` issues an invitation, reports a pending one with the same role, and refuses
  members, another pending role and unknown firms.
* ``list-firms`` lists firms with owners, members and pending invitations.
* ``main`` runs them against ``DATABASE_URL`` and prints JSON lines (exit 2 on refusal).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from dbtest_support import Factory, db_settings, truncate_all
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis import admin
from basis.admin import AdminError, create_firm, invite, list_firms
from basis.db.engine import create_engine
from basis.db.enums import OrgRole
from basis.db.models import Invitation, Org, OrgMember

pytestmark = pytest.mark.integration

TTL = timedelta(days=14)


async def test_create_firm_creates_the_firm_and_a_pending_owner_invitation(
    session: AsyncSession,
) -> None:
    result = await create_firm(session, "  Basis ", " Owner@Example.com", ttl=TTL)

    assert result["action"] == "created"
    assert result["firm"]["name"] == "Basis"
    owner = result["owner"]
    assert (owner["email"], owner["role"], owner["status"], owner["delivery"]) == (
        "owner@example.com",
        "owner",
        "pending",
        "not_sent",
    )
    row = await session.get(Invitation, uuid.UUID(owner["invitation_id"]))
    assert row is not None and row.created_by is None
    assert await session.scalar(select(func.count()).select_from(OrgMember)) == 0


async def test_create_firm_again_reports_the_existing_state(session: AsyncSession) -> None:
    first = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)
    again = await create_firm(session, "Basis", "OWNER@example.com", ttl=TTL)

    assert again["action"] == "exists"
    assert again["firm"] == first["firm"]
    assert again["owner"]["invitation_id"] == first["owner"]["invitation_id"]
    assert await session.scalar(select(func.count()).select_from(Org)) == 1


async def test_create_firm_after_acceptance_reports_the_owner(session: AsyncSession) -> None:
    first = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)
    make = Factory(session)
    user = await make.user("owner-uid", "owner@example.com")
    await make.member(uuid.UUID(first["firm"]["org_id"]), user, OrgRole.OWNER)

    again = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)

    assert again["action"] == "exists"
    assert again["owner"] == {
        "email": "owner@example.com",
        "status": "member",
        "user_id": str(user.id),
    }


async def test_create_firm_replaces_an_expired_owner_invitation(session: AsyncSession) -> None:
    first = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)
    await session.execute(
        update(Invitation)
        .where(Invitation.id == uuid.UUID(first["owner"]["invitation_id"]))
        .values(created_at=func.now() - TTL - TTL, expires_at=func.now() - TTL)
    )

    again = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)

    assert again["action"] == "exists" and again["reinvited"] is True
    assert again["owner"]["status"] == "pending"
    assert again["owner"]["invitation_id"] != first["owner"]["invitation_id"]


async def test_a_duplicate_name_is_refused_unless_allowed(session: AsyncSession) -> None:
    await create_firm(session, "Basis", "owner@example.com", ttl=TTL)

    with pytest.raises(AdminError, match="already exists"):
        await create_firm(session, "Basis", "someone-else@example.com", ttl=TTL)
    second = await create_firm(
        session, "Basis", "someone-else@example.com", ttl=TTL, allow_duplicate_name=True
    )
    assert second["action"] == "created"
    with pytest.raises(AdminError, match="2 firms are named"):
        await create_firm(session, "Basis", "owner@example.com", ttl=TTL)


@pytest.mark.parametrize(("name", "email"), [("", "a@b.co"), ("x" * 201, "a@b.co"), ("B", "nope")])
async def test_create_firm_refuses_bad_input(session: AsyncSession, name: str, email: str) -> None:
    with pytest.raises(AdminError):
        await create_firm(session, name, email, ttl=TTL)


async def test_invite(session: AsyncSession) -> None:
    firm = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)
    org = uuid.UUID(firm["firm"]["org_id"])

    first = await invite(session, org, "Erin@Example.com", OrgRole.ENGINEER, ttl=TTL)
    again = await invite(session, org, "erin@example.com", OrgRole.ENGINEER, ttl=TTL)

    assert first["action"] == "invited" and first["invitation"]["email"] == "erin@example.com"
    assert again["action"] == "exists"
    assert again["invitation"]["invitation_id"] == first["invitation"]["invitation_id"]
    with pytest.raises(AdminError, match="another role"):
        await invite(session, org, "erin@example.com", OrgRole.VIEWER, ttl=TTL)
    with pytest.raises(AdminError, match="no firm"):
        await invite(session, uuid.uuid4(), "erin@example.com", OrgRole.VIEWER, ttl=TTL)
    with pytest.raises(AdminError, match="not an email"):
        await invite(session, org, "erin", OrgRole.VIEWER, ttl=TTL)
    make = Factory(session)
    await make.member(org, await make.user("v-uid", "vic@example.com"), OrgRole.VIEWER)
    with pytest.raises(AdminError, match="already a member"):
        await invite(session, org, "vic@example.com", OrgRole.OWNER, ttl=TTL)


async def test_list_firms(session: AsyncSession) -> None:
    firm = await create_firm(session, "Basis", "owner@example.com", ttl=TTL)
    org = uuid.UUID(firm["firm"]["org_id"])
    make = Factory(session)
    await make.member(org, await make.user("o-uid", "olivia@example.com"), OrgRole.OWNER)

    (row,) = [f for f in await list_firms(session) if f["org_id"] == str(org)]

    assert row["name"] == "Basis"
    assert row["owners"] == ["olivia@example.com"]
    assert (row["members"], row["pending_invitations"]) == (1, 1)


def truncate(database_url: str) -> None:
    async def run() -> None:
        engine = create_engine(db_settings(database_url))
        try:
            await truncate_all(engine)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_main_prints_json_lines_and_exits_2_on_refusal(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("INVITATION_TTL_DAYS", "3")
    truncate(database_url)
    try:
        assert admin.main(["create-firm", "--name", "Basis", "--owner-email", "o@b.co"]) == 0
        created = json.loads(capsys.readouterr().out)
        assert created["action"] == "created"
        assert admin.main(["create-firm", "--name", "Basis", "--owner-email", "o@b.co"]) == 0
        assert json.loads(capsys.readouterr().out)["action"] == "exists"
        assert admin.main(["create-firm", "--name", "Basis", "--owner-email", "x@b.co"]) == 2
        assert "already exists" in capsys.readouterr().err
        org = created["firm"]["org_id"]
        args = ["invite", "--org-id", org, "--email", "e@b.co", "--role", "engineer"]
        assert admin.main(args) == 0
        invited = json.loads(capsys.readouterr().out)["invitation"]
        assert invited["role"] == "engineer"
        assert admin.main(["list-firms"]) == 0
        (listed,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert (listed["org_id"], listed["pending_invitations"]) == (org, 2)
        with pytest.raises(SystemExit):  # argparse: an unknown role
            admin.main(["invite", "--org-id", org, "--email", "e@b.co", "--role", "admin"])
    finally:
        truncate(database_url)
