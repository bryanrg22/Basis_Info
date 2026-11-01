"""``GET``/``PUT /v1/orgs/{org_id}/retention`` on the real app
(docs/api/RETENTION_AND_ALERTS.md 1.1).

Members read the rule (null by default); owners set it (1 to 3650 days, or null to keep
everything); an engineer or viewer gets 403 on the write, a non-member 404 on both; the
value is validated (0, a negative, a float, a string, too large: 422); the write is
idempotent under its key; ``StudyOut`` carries ``purged_at``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole
from basis.db.models import Org

pytestmark = pytest.mark.integration


async def put(api: Api, uid: str, org: uuid.UUID, body: Any, **kwargs: Any) -> Any:
    return await api.client.put(
        f"/v1/orgs/{org}/retention",
        json=body,
        headers={**bearer(uid), "Idempotency-Key": kwargs.get("key") or str(uuid.uuid4())},
    )


async def test_owner_sets_members_read(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    org = await seed.personal_org("olivia")
    await seed.member(org, "erin", OrgRole.ENGINEER)
    await seed.member(org, "vic", OrgRole.VIEWER)

    before = await api.get("vic", f"/v1/orgs/{org}/retention")
    assert before.status_code == 200
    assert before.json() == {"org_id": str(org), "retention_days": None}

    response = await put(api, "olivia", org, {"retention_days": 365})
    assert response.status_code == 200, response.text
    assert response.json() == {"org_id": str(org), "retention_days": 365}
    async with sessionmaker() as session:
        assert await session.scalar(select(Org.retention_days).where(Org.id == org)) == 365

    for uid in ("erin", "vic", "olivia"):
        read = await api.get(uid, f"/v1/orgs/{org}/retention")
        assert read.status_code == 200 and read.json()["retention_days"] == 365

    cleared = await put(api, "olivia", org, {"retention_days": None})
    assert cleared.status_code == 200 and cleared.json()["retention_days"] is None


async def test_only_owners_write_and_non_members_see_nothing(api: Api, seed: Seed) -> None:
    org = await seed.personal_org("olivia")
    await seed.member(org, "erin", OrgRole.ENGINEER)
    await seed.member(org, "vic", OrgRole.VIEWER)
    await seed.user("stranger")

    assert (await put(api, "erin", org, {"retention_days": 30})).status_code == 403
    assert (await put(api, "vic", org, {"retention_days": 30})).status_code == 403
    assert (await put(api, "stranger", org, {"retention_days": 30})).status_code == 404
    assert (await api.get("stranger", f"/v1/orgs/{org}/retention")).status_code == 404
    assert (await api.get("olivia", f"/v1/orgs/{uuid.uuid4()}/retention")).status_code == 404
    assert (await api.get("olivia", f"/v1/orgs/{org}/retention")).json()["retention_days"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"retention_days": 0},
        {"retention_days": -1},
        {"retention_days": 3651},
        {"retention_days": 1.5},
        {"retention_days": "30"},
        {"retention_days": True},
        {},
        {"retention_days": 30, "extra": 1},
    ],
)
async def test_invalid_rules_are_422(api: Api, seed: Seed, body: Any) -> None:
    org = await seed.personal_org("olivia")
    response = await put(api, "olivia", org, body)
    assert response.status_code == 422, response.text
    assert (await api.get("olivia", f"/v1/orgs/{org}/retention")).json()["retention_days"] is None


async def test_the_write_is_idempotent_under_its_key(api: Api, seed: Seed) -> None:
    org = await seed.personal_org("olivia")
    key = str(uuid.uuid4())
    first = await put(api, "olivia", org, {"retention_days": 7}, key=key)
    second = await put(api, "olivia", org, {"retention_days": 7}, key=key)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == first.json()
    bounds = await put(api, "olivia", org, {"retention_days": 3650})
    assert bounds.status_code == 200 and bounds.json()["retention_days"] == 3650
    one = await put(api, "olivia", org, {"retention_days": 1})
    assert one.status_code == 200 and one.json()["retention_days"] == 1


async def test_study_out_carries_purged_at(api: Api) -> None:
    created = await api.post("olivia", "/v1/studies", {"property_name": "12 Main"})
    assert created.status_code == 201, created.text
    assert created.json()["purged_at"] is None
    got = await api.get("olivia", f"/v1/studies/{created.json()['id']}")
    assert got.json()["purged_at"] is None
