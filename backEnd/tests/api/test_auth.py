"""Authentication and study authorization on the real routes (S1_SPEC section 4).

* Every ``/v1`` route: no token, a malformed header, an expired or a revoked token is 401
  with ``WWW-Authenticate`` and nothing is written.
* First sight of a uid creates the user, a personal org and an ``owner`` membership;
  later requests reuse them.
* A study in an org the caller does not belong to is 404, exactly like a study that does
  not exist; a visible study with too low a role is 403.

Firebase is replaced at ``get_token_verifier`` (``FakeVerifier``), except in
``test_principal_override`` (``get_principal``) and the emulator test, which runs only
when ``FIREBASE_AUTH_EMULATOR_HOST`` is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, bearer
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.app import create_app
from basis.api.auth import DETAIL_MALFORMED, DETAIL_MISSING, get_principal, get_token_verifier
from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.api.users import DETAIL_NO_DATABASE, LAST_SEEN_RESOLUTION
from basis.auth.firebase import DETAIL_EXPIRED, DETAIL_INVALID, DETAIL_REVOKED, Principal
from basis.config import Settings
from basis.db.enums import OrgRole, Stage
from basis.db.models import Org, OrgMember, User
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

STUDY = "00000000-0000-4000-8000-000000000001"
DOC = "00000000-0000-4000-8000-000000000002"
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/v1/studies", None),
    ("POST", "/v1/studies", {"property_name": "X"}),
    ("GET", f"/v1/studies/{STUDY}", None),
    ("DELETE", f"/v1/studies/{STUDY}", None),
    ("POST", f"/v1/studies/{STUDY}/documents", {"filename": "a.pdf"}),
    ("POST", f"/v1/studies/{STUDY}/documents/{DOC}/complete", None),
    ("POST", f"/v1/studies/{STUDY}/decisions", {"stage": "appraisal_review"}),
    ("GET", f"/v1/studies/{STUDY}/evidence?asset_id={DOC}", None),
    ("GET", f"/v1/studies/{STUDY}/photos/{DOC}/objects", None),
    ("POST", f"/v1/studies/{STUDY}/photos/{DOC}/objects", {"kind": "point"}),
    ("DELETE", f"/v1/studies/{STUDY}/photos/{DOC}/objects/{DOC}", None),
]
ROUTE_IDS = [f"{method} {path.split('?')[0]}" for method, path, _ in ROUTES]


async def table_count(sm: async_sessionmaker[AsyncSession], model: type[Any]) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def send(
    client: httpx.AsyncClient, method: str, path: str, body: Any, headers: dict[str, str]
) -> httpx.Response:
    sent = {IDEMPOTENCY_HEADER: str(uuid.uuid4()), **headers}
    return await client.request(method, path, json=body, headers=sent)


# --------------------------------------------------------------------------- 401


@pytest.mark.parametrize(
    ("authorization", "detail", "challenge"),
    [
        (None, DETAIL_MISSING, "Bearer"),
        ("Basic dXNlcjpwYXNz", DETAIL_MALFORMED, "Bearer"),  # pragma: allowlist secret
        ("Bearer", DETAIL_MALFORMED, "Bearer"),
        ("Bearer expired", DETAIL_EXPIRED, 'Bearer error="invalid_token"'),
        ("Bearer revoked", DETAIL_REVOKED, 'Bearer error="invalid_token"'),
        ("Bearer garbage", DETAIL_INVALID, 'Bearer error="invalid_token"'),
    ],
    ids=["missing", "basic", "empty-bearer", "expired", "revoked", "invalid"],
)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=ROUTE_IDS)
async def test_every_route_needs_a_valid_token(
    http: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    method: str,
    path: str,
    body: Any,
    authorization: str | None,
    detail: str,
    challenge: str,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    response = await send(http, method, path, body, headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["www-authenticate"] == challenge
    assert response.json()["detail"] == detail
    assert await table_count(sessionmaker, User) == 0


# --------------------------------------------------------------------------- users


async def test_first_request_creates_user_personal_org_and_owner_membership(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    assert (await api.get("alice", "/v1/studies")).status_code == 200

    async with sessionmaker() as session:
        (user,) = (await session.scalars(select(User))).all()
        (org,) = (await session.scalars(select(Org))).all()
        (member,) = (await session.scalars(select(OrgMember))).all()
    assert (user.firebase_uid, user.email, user.display_name) == (
        "alice",
        "alice@example.test",
        "Alice",
    )
    assert user.last_seen_at is not None
    assert org.name == "Alice (personal)"
    assert (member.org_id, member.user_id, member.role) == (org.id, user.id, "owner")


async def test_later_requests_reuse_the_user_and_org(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(3):
        assert (await api.get("alice", "/v1/studies")).status_code == 200
    await api.create_study("alice")
    assert await table_count(sessionmaker, User) == 1
    assert await table_count(sessionmaker, Org) == 1


async def test_concurrent_first_requests_create_one_user(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    responses = await asyncio.gather(*(api.get("dana", "/v1/studies") for _ in range(5)))
    assert [r.status_code for r in responses] == [200] * 5
    assert await table_count(sessionmaker, User) == 1
    assert await table_count(sessionmaker, Org) == 1


async def test_last_seen_is_refreshed_only_after_the_resolution(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await api.get("alice", "/v1/studies")
    async with sessionmaker() as session:
        first = (await session.scalars(select(User))).one().last_seen_at
    await api.get("alice", "/v1/studies")
    async with sessionmaker() as session:
        assert (await session.scalars(select(User))).one().last_seen_at == first

    stale = LAST_SEEN_RESOLUTION + timedelta(seconds=1)
    async with sessionmaker() as session, session.begin():
        await session.execute(update(User).values(last_seen_at=User.last_seen_at - stale))
    await api.get("alice", "/v1/studies")
    async with sessionmaker() as session:
        refreshed = (await session.scalars(select(User))).one().last_seen_at
    assert first is not None and refreshed is not None and refreshed > first - stale


async def test_changed_email_claim_is_stored(
    api_app: FastAPI, api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await api.get("alice", "/v1/studies")
    api_app.dependency_overrides[get_principal] = lambda: Principal(
        uid="alice", email="alice@new.example", display_name=None
    )
    assert (await api.get("alice", "/v1/studies")).status_code == 200
    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
    assert (user.email, user.display_name) == ("alice@new.example", "Alice")


async def test_principal_override(
    api_app: FastAPI, http: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Overriding ``get_principal`` bypasses the header entirely (for other test suites)."""
    api_app.dependency_overrides[get_principal] = lambda: Principal(uid="erin")
    response = await http.get("/v1/studies")
    assert response.status_code == 200
    async with sessionmaker() as session:
        assert (await session.scalars(select(User.firebase_uid))).all() == ["erin"]


async def test_without_a_database_v1_routes_are_503(settings: Settings, tmp_path: Any) -> None:
    app = create_app(settings, storage=LocalStorage(tmp_path))
    app.dependency_overrides[get_token_verifier] = FakeVerifier
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        response = await client.get("/v1/studies", headers=bearer("alice"))
    assert response.status_code == 503
    assert response.json()["detail"] == DETAIL_NO_DATABASE


# --------------------------------------------------------------------------- visibility


def study_routes(study_id: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    return [
        ("GET", f"/v1/studies/{study_id}", None),
        ("DELETE", f"/v1/studies/{study_id}", None),
        (
            "POST",
            f"/v1/studies/{study_id}/documents",
            {
                "filename": "a.pdf",
                "content_type": "application/pdf",
                "bytes": 3,
                "sha256": "a" * 64,
                "kind": "appraisal",
            },
        ),
        ("POST", f"/v1/studies/{study_id}/documents/{DOC}/complete", None),
        (
            "POST",
            f"/v1/studies/{study_id}/decisions",
            {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
        ),
        ("GET", f"/v1/studies/{study_id}/evidence?asset_id={DOC}", None),
        ("GET", f"/v1/studies/{study_id}/photos/{DOC}/objects", None),
        (
            "POST",
            f"/v1/studies/{study_id}/photos/{DOC}/objects",
            {"kind": "point", "label": "window", "point": {"x": 0.5, "y": 0.5}},
        ),
        ("DELETE", f"/v1/studies/{study_id}/photos/{DOC}/objects/{DOC}", None),
    ]


async def test_study_in_another_org_is_indistinguishable_from_a_missing_one(
    api: Api, seed: Seed, http: httpx.AsyncClient
) -> None:
    study = await api.create_study("alice")
    await seed.stage(study["id"], Stage.APPRAISAL_REVIEW)
    await seed.user("bob")  # bob exists, with his own org, and is not in alice's
    missing = str(uuid.uuid4())

    for (method, path, body), (_, missing_path, _) in zip(
        study_routes(study["id"]), study_routes(missing), strict=True
    ):
        hidden = await send(http, method, path, body, bearer("bob"))
        absent = await send(http, method, missing_path, body, bearer("bob"))
        assert (hidden.status_code, absent.status_code) == (404, 404), path
        hidden_body, absent_body = hidden.json(), absent.json()
        for problem in (hidden_body, absent_body):
            problem.pop("request_id")
            problem.pop("instance")
        assert hidden_body == absent_body, path

    # Nothing changed for alice.
    detail = (await api.get("alice", f"/v1/studies/{study['id']}")).json()
    assert (detail["status"], detail["documents"], detail["latest_reviews"]) == (
        "waiting_review",
        [],
        [],
    )


async def test_membership_in_a_different_org_grants_nothing(api: Api, seed: Seed) -> None:
    """Bob is an owner somewhere else; that org has nothing to do with alice's study."""
    other = await seed.org("Other Co")
    await seed.member(other, "bob", OrgRole.OWNER)
    study = await api.create_study("alice")
    assert (await api.get("bob", f"/v1/studies/{study['id']}")).status_code == 404
    assert (await api.get("bob", "/v1/studies")).json()["items"] == []


async def test_adding_a_membership_makes_the_study_visible(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    assert (await api.get("bob", f"/v1/studies/{study['id']}")).status_code == 404
    await seed.member(uuid.UUID(study["org_id"]), "bob", OrgRole.VIEWER)
    assert (await api.get("bob", f"/v1/studies/{study['id']}")).status_code == 200


@pytest.mark.parametrize(
    ("role", "method", "suffix", "body", "allowed"),
    [
        (OrgRole.VIEWER, "GET", "", None, True),
        (OrgRole.VIEWER, "GET", f"/evidence?asset_id={DOC}", None, True),
        (OrgRole.VIEWER, "GET", "/evidence", None, True),
        (OrgRole.VIEWER, "POST", "/documents", "upload", False),
        (OrgRole.VIEWER, "POST", f"/documents/{DOC}/complete", None, False),
        (OrgRole.VIEWER, "POST", "/decisions", "decision", False),
        (OrgRole.VIEWER, "DELETE", "", None, False),
        (OrgRole.VIEWER, "POST", f"/photos/{DOC}/objects", "selection", False),
        (OrgRole.VIEWER, "DELETE", f"/photos/{DOC}/objects/{DOC}", None, False),
        (OrgRole.ENGINEER, "POST", "/documents", "upload", True),
        (OrgRole.ENGINEER, "POST", "/decisions", "decision", True),
        (OrgRole.ENGINEER, "DELETE", "", None, False),
    ],
)
async def test_role_requirements(
    api: Api,
    seed: Seed,
    http: httpx.AsyncClient,
    role: OrgRole,
    method: str,
    suffix: str,
    body: str | None,
    allowed: bool,
) -> None:
    org = await seed.org()
    await seed.member(org, "carol", OrgRole.OWNER)
    await seed.member(org, "alice", role)
    study = await api.create_study("carol", org_id=str(org))
    await seed.stage(study["id"], Stage.APPRAISAL_REVIEW)
    payloads: dict[str, dict[str, Any]] = {
        "upload": {
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "bytes": 3,
            "sha256": "a" * 64,
            "kind": "appraisal",
        },
        "decision": {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
        "selection": {"kind": "point", "label": "window", "point": {"x": 0.5, "y": 0.5}},
    }

    response = await send(
        http,
        method,
        f"/v1/studies/{study['id']}{suffix}",
        payloads[body] if body else None,
        bearer("alice"),
    )

    if allowed:
        assert response.status_code < 400, response.text
    else:
        assert response.status_code == 403
        assert response.headers["content-type"] == PROBLEM_JSON
        assert role.value in response.json()["detail"]


# --------------------------------------------------------------------------- emulator


@pytest.mark.skipif(
    not os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"),
    reason="needs the Firebase Auth emulator (FIREBASE_AUTH_EMULATOR_HOST)",
)
async def test_real_verifier_against_the_auth_emulator(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Any,
) -> None:
    """Sign up a user on the emulator and call the API with its real ID token."""
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
    project = os.environ.get("GCLOUD_PROJECT", "demo-basis")
    async with httpx.AsyncClient() as emulator:
        signup = await emulator.post(
            f"http://{host}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=fake-key",
            json={
                "email": f"{uuid.uuid4().hex}@example.test",
                "password": "emulator-only",  # pragma: allowlist secret
                "returnSecureToken": True,
            },
        )
    signup.raise_for_status()
    token = signup.json()["idToken"]
    app = create_app(
        api_settings(database_url, firebase_project_id=project),
        db_sessionmaker=sessionmaker,
        storage=LocalStorage(tmp_path),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        ok = await client.get("/v1/studies", headers={"Authorization": f"Bearer {token}"})
        bad = await client.get("/v1/studies", headers={"Authorization": "Bearer not-a-jwt"})
    assert ok.status_code == 200, ok.text
    assert bad.status_code == 401
