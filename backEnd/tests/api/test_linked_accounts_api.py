"""The Team page's duplicate, end to end: a firm member signs in with a new account (a new
Firebase uid, same verified email). With ``AUTH_LINK_VERIFIED_EMAIL`` on they are the same
user (``GET /v1/me`` lists their firm, the members list has one row); off (the default) they
are a new user in no firm, as before (EVAL_REPORT section 93, docs/api/FIRMS.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.auth.firebase import Principal
from basis.db.enums import OrgRole
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration


async def _api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    link: bool,
) -> AsyncIterator[Api]:
    settings = api_settings(database_url, auth_link_verified_email=link)
    app = build_api_app(settings, sessionmaker, storage, verifier, own_firms=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


@pytest.fixture(params=[True, False], ids=["linking-on", "linking-off"])
async def api_and_setting(
    request: pytest.FixtureRequest,
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> AsyncIterator[tuple[Api, bool]]:
    link = bool(request.param)
    async for api in _api(database_url, sessionmaker, storage, verifier, link):
        yield api, link


async def test_a_member_back_with_a_new_account(
    api_and_setting: tuple[Api, bool], seed: Seed, verifier: FakeVerifier
) -> None:
    api, link = api_and_setting
    org = await seed.org("Acme Engineering")
    await seed.member(org, "ada", OrgRole.OWNER, own_firm=False)
    # The same person, a new account; the password sign-in carries no display name.
    verifier.identities["ada-new"] = Principal(
        uid="ada-new", email="ADA@example.test", display_name=None, email_verified=True
    )

    me = (await api.get("ada-new", "/v1/me")).json()
    members = (await api.get("ada", f"/v1/orgs/{org}/members")).json()["items"]
    ada = (await api.get("ada", "/v1/me")).json()["user"]

    if link:
        assert me["user"]["id"] == ada["id"]
        assert me["user"]["display_name"] == "Ada"
        assert [m["org_id"] for m in me["memberships"]] == [str(org)]
        # The new account can act as the owner it is.
        assert (await api.get("ada-new", f"/v1/orgs/{org}/members")).status_code == 200
    else:
        assert me["user"]["id"] != ada["id"]
        assert me["memberships"] == []
        assert (await api.get("ada-new", f"/v1/orgs/{org}/members")).status_code == 404
    assert len(members) == 1


async def test_an_unverified_new_account_is_not_linked(
    api_and_setting: tuple[Api, bool], seed: Seed, verifier: FakeVerifier
) -> None:
    api, _ = api_and_setting
    org = await seed.org("Acme Engineering")
    await seed.member(org, "ada", OrgRole.OWNER, own_firm=False)
    verifier.identities["ada-new"] = Principal(
        uid="ada-new", email="ada@example.test", display_name=None, email_verified=False
    )
    me = (await api.get("ada-new", "/v1/me")).json()
    assert me["memberships"] == []
    assert (await api.get("ada-new", f"/v1/orgs/{org}/members")).status_code == 404
