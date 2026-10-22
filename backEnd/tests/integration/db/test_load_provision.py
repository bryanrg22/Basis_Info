"""``tooling/load/provision.py``: the load-test engineers get a firm, through the repositories.

Firms are invite-only, so the ``load-eng-<n>`` users Locust signs in as would get 403
``no-firm``. The script (an operator step with database access, no HTTP route) creates
the "Load test" firm once and gives each uid an engineer membership on the same ``users``
row the API resolves from the token; re-running changes nothing and a higher role is kept.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OrgRole
from basis.db.models import Org, OrgMember
from basis.db.repositories import OrgRepository, UserRepository

pytestmark = pytest.mark.integration

PROVISION = Path(__file__).resolve().parents[4] / "tooling" / "load" / "provision.py"


def load_provision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_provision", PROVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_provision_creates_the_firm_once_and_makes_engineers(session: AsyncSession) -> None:
    provision = load_provision()
    uids = ["load-eng-0", "load-eng-1", "load-eng-2"]

    first = await provision.provision(session, uids, "Load test")
    again = await provision.provision(session, uids, "Load test")

    assert first["created"] is True and again["created"] is False
    assert first["org_id"] == again["org_id"]
    assert first["roles"] == again["roles"] == {"engineer": 3}
    assert await session.scalar(select(func.count()).where(Org.name == "Load test")) == 1
    org_id = uuid.UUID(first["org_id"])
    users = UserRepository(session)
    for uid in uids:
        user = await users.get_by_firebase_uid(uid)
        assert user is not None
        assert (user.email, user.display_name) == (f"{uid}@example.test", "Load Test Engineer")
        assert await users.memberships(user.id) == {org_id: OrgRole.ENGINEER}
    assert len(await OrgRepository(session).members(org_id)) == 3


async def test_provision_keeps_a_higher_role_and_adds_new_users(session: AsyncSession) -> None:
    provision = load_provision()
    first = await provision.provision(session, ["load-eng-0"], "Load test")
    org_id = (await OrgRepository(session).by_name("Load test"))[0].id
    user = await UserRepository(session).get_by_firebase_uid("load-eng-0")
    assert user is not None
    await UserRepository(session).grant_membership(org_id, user.id, OrgRole.OWNER)

    again = await provision.provision(session, ["load-eng-0", "load-eng-1"], "Load test")

    assert again["roles"] == {"owner": 1, "engineer": 1}
    assert str(org_id) == first["org_id"] == again["org_id"]
    assert (
        await session.scalar(
            select(func.count()).select_from(OrgMember).where(OrgMember.org_id == org_id)
        )
        == 2
    )


def test_provision_refuses_no_users() -> None:
    provision = load_provision()
    with pytest.raises(SystemExit):
        provision.main(["--users", "0"])
