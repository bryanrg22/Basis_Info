"""Helpers for the database-backed API tests (imported as a top-level module; the name is
unique so it cannot collide with the legacy ``tests`` packages).

* :class:`FakeVerifier` replaces Firebase at ``get_token_verifier``, one level below
  ``get_principal``, so header parsing and 401 mapping still run for real. Tokens:
  ``uid:<uid>`` is that user; ``expired``/``revoked``/anything else is a 401 with the
  Firebase verifier's own detail text.
* :class:`Api` sends requests as a named user and mints a fresh ``Idempotency-Key`` per
  mutation unless one is given.
* :class:`Seed` writes directly through the repositories for states the API cannot reach
  in S1 (memberships in another org, a study waiting at a review stage).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import httpx
from dbtest_support import db_settings
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.app import create_app
from basis.api.auth import get_token_verifier
from basis.api.errors import Unauthorized
from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.auth.firebase import DETAIL_EXPIRED, DETAIL_INVALID, DETAIL_REVOKED, Principal
from basis.config import Settings
from basis.db.enums import OrgRole, Stage, status_for_stage
from basis.db.models import Org, Study, User
from basis.db.repositories import UserRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage
from basis.temporal.service import WorkflowService

TOKEN_PREFIX = "uid:"
# Nothing listens on port 1: connecting fails at once with "connection refused".
REFUSED_DATABASE_URL = "postgresql://basis:x@127.0.0.1:1/none"  # pragma: allowlist secret
UNLIMITED = 1_000_000
BASE_URL = "http://basis.test"


def api_settings(database_url: str, **overrides: Any) -> Settings:
    """Test-database settings with rate limits raised out of the way."""
    values: dict[str, Any] = {
        "rate_limit_reads_per_min": UNLIMITED,
        "rate_limit_mutations_per_min": UNLIMITED,
        **overrides,
    }
    return db_settings(database_url, **values)


def token_for(uid: str) -> str:
    return f"{TOKEN_PREFIX}{uid}"


def bearer(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(uid)}"}


def build_api_app(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    workflows: WorkflowService | None = None,
) -> FastAPI:
    """The real app with the database, storage (and optionally Temporal) injected and
    Firebase faked."""
    app = create_app(settings, db_sessionmaker=sessionmaker, storage=storage, workflows=workflows)
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    return app


class FakeVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, token: str) -> Principal:
        self.calls += 1
        if token.startswith(TOKEN_PREFIX) and len(token) > len(TOKEN_PREFIX):
            uid = token[len(TOKEN_PREFIX) :]
            return Principal(uid=uid, email=f"{uid}@example.test", display_name=uid.title())
        if token == "expired":
            raise Unauthorized(DETAIL_EXPIRED)
        if token == "revoked":
            raise Unauthorized(DETAIL_REVOKED)
        raise Unauthorized(DETAIL_INVALID)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_key() -> str:
    return str(uuid.uuid4())


class Api:
    """A thin client: ``await api.post("alice", "/v1/studies", {...})``."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def get(self, uid: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.client.get(path, headers=bearer(uid), **kwargs)

    async def post(
        self,
        uid: str,
        path: str,
        body: Any = None,
        *,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        sent = {**bearer(uid), IDEMPOTENCY_HEADER: key or new_key(), **(headers or {})}
        return await self.client.post(path, json=body, headers=sent)

    async def delete(self, uid: str, path: str, *, key: str | None = None) -> httpx.Response:
        sent = {**bearer(uid), IDEMPOTENCY_HEADER: key or new_key()}
        return await self.client.delete(path, headers=sent)

    async def create_study(
        self, uid: str, name: str = "12 Main St", **extra: Any
    ) -> dict[str, Any]:
        response = await self.post(uid, "/v1/studies", {"property_name": name, **extra})
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    async def request_upload(
        self, uid: str, study_id: str, data: bytes, **overrides: Any
    ) -> httpx.Response:
        body = {
            "filename": "appraisal.pdf",
            "content_type": "application/pdf",
            "bytes": len(data),
            "sha256": sha256_hex(data),
            "kind": "appraisal",
            **overrides,
        }
        return await self.post(uid, f"/v1/studies/{study_id}/documents", body)


class Seed:
    """Direct writes for preconditions; each call commits."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def user(self, uid: str) -> User:
        async with session_scope(self.sessionmaker) as session:
            return await UserRepository(session).upsert_by_firebase_uid(
                uid, f"{uid}@example.test", uid.title()
            )

    async def personal_org(self, uid: str) -> uuid.UUID:
        user = await self.user(uid)
        async with session_scope(self.sessionmaker) as session:
            memberships = await UserRepository(session).memberships(user.id)
        owned = [org for org, role in memberships.items() if role is OrgRole.OWNER]
        assert len(owned) == 1
        return owned[0]

    async def org(self, name: str = "Acme Engineering") -> uuid.UUID:
        async with session_scope(self.sessionmaker) as session:
            org = Org(name=name)
            session.add(org)
            await session.flush()
            return org.id

    async def member(self, org_id: uuid.UUID, uid: str, role: OrgRole) -> None:
        user = await self.user(uid)
        async with session_scope(self.sessionmaker) as session:
            await UserRepository(session).add_membership(org_id, user.id, role)

    async def stage(self, study_id: str | uuid.UUID, stage: Stage, revision: int = 1) -> None:
        async with session_scope(self.sessionmaker) as session:
            await session.execute(
                update(Study)
                .where(Study.id == uuid.UUID(str(study_id)))
                .values(current_stage=stage, status=status_for_stage(stage), revision=revision)
            )
