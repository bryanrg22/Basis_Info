"""Helpers for the database-backed API tests (imported as a top-level module; the name is
unique so it cannot collide with the legacy ``tests`` packages).

* :class:`FakeVerifier` replaces Firebase at ``get_token_verifier``, one level below
  ``get_principal``, so header parsing and 401 mapping still run for real. Tokens:
  ``uid:<uid>`` is that user (email ``<uid>@example.test``, verified, unless
  ``verifier.identities[uid]`` says otherwise); ``expired``/``revoked``/anything else is a
  401 with the Firebase verifier's own detail text.
* :func:`build_api_app` gives every user who belongs to no org a firm of their own on
  their first request (``dbtest_support.ensure_own_firm``), so the study tests keep one
  firm per user as before firms became invite-only. ``own_firms=False`` is the product's
  real onboarding (``tests/api/test_firms.py``).
* :class:`Api` sends requests as a named user and mints a fresh ``Idempotency-Key`` per
  mutation unless one is given.
* :class:`Seed` writes directly through the repositories for states the API cannot reach
  in S1 (memberships in another org, a study waiting at a review stage).
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any, Final

import httpx
from dbtest_support import db_settings, ensure_own_firm
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.app import create_app
from basis.api.auth import CurrentPrincipal, get_token_verifier
from basis.api.errors import Unauthorized
from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.api.users import CurrentUser, SessionmakerDep, get_current_user
from basis.auth.firebase import DETAIL_EXPIRED, DETAIL_INVALID, DETAIL_REVOKED, Principal
from basis.config import Settings
from basis.db.enums import (
    Bucket,
    ClassificationMethod,
    DocumentKind,
    OrgRole,
    Stage,
    status_for_stage,
)
from basis.db.models import (
    Asset,
    Component,
    Extraction,
    ExtractionField,
    Org,
    Room,
    Study,
    User,
)
from basis.db.repositories import DocumentRepository, UserRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage
from basis.temporal.service import WorkflowService
from basis.vision.localize import Localizer

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
    *,
    own_firms: bool = True,
    localizer: Localizer | None = None,
) -> FastAPI:
    """The real app with the database, storage (and optionally Temporal and a localizer)
    injected and Firebase faked; with ``own_firms`` each new user owns a firm of their own.
    The localizer is always the one given (none by default), never one built from the
    environment, so a shell that exports a Replicate token cannot reach the real model."""
    app = create_app(settings, db_sessionmaker=sessionmaker, storage=storage, workflows=workflows)
    app.state.localizer = localizer
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    if own_firms:
        app.dependency_overrides[get_current_user] = current_user_with_own_firm
    return app


async def current_user_with_own_firm(
    principal: CurrentPrincipal, sessionmaker: SessionmakerDep
) -> CurrentUser:
    """The real ``get_current_user``, then (in its own transaction) a firm if they have none."""
    current = await get_current_user(principal, sessionmaker)
    async with session_scope(sessionmaker) as session:
        await ensure_own_firm(session, current.user)
    return current


class FakeVerifier:
    def __init__(self) -> None:
        self.calls = 0
        # Per-uid principals for tests that need another email or an unverified one.
        self.identities: dict[str, Principal] = {}

    def verify(self, token: str) -> Principal:
        self.calls += 1
        if token.startswith(TOKEN_PREFIX) and len(token) > len(TOKEN_PREFIX):
            uid = token[len(TOKEN_PREFIX) :]
            if uid in self.identities:
                return self.identities[uid]
            return Principal(
                uid=uid,
                email=f"{uid}@example.test",
                display_name=uid.title(),
                email_verified=True,
            )
        if token == "expired":
            raise Unauthorized(DETAIL_EXPIRED)
        if token == "revoked":
            raise Unauthorized(DETAIL_REVOKED)
        raise Unauthorized(DETAIL_INVALID)


# An intake with every field a start needs (basis.domain.intake.missing_for_start); the
# same document tests/api/test_studies.py stores and reads back.
COMPLETE_INTAKE: Final[dict[str, Any]] = {
    "purchase_price": "650000.00",
    "acquisition_costs": [
        {"category": "title_insurance_owner", "amount": "1250.00"},
        {"category": "recording_fees", "amount": 180},
    ],
    "land_method": "appraisal_site_value",
    "appraisal_site_value": "90000.00",
    "appraisal_effective_date": "2025-03-14",
    "property_use": "residential_rental",
    "dwelling_unit_income_share": "1",
    "placed_in_service_date": "2025-05-01",
    "acquisition_date": "2025-04-15",
    "state": "NY",
    "zip": "12345",
}


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

    async def user(self, uid: str, *, own_firm: bool = True) -> User:
        """The user, owning a firm of their own unless ``own_firm`` is False."""
        async with session_scope(self.sessionmaker) as session:
            user = await UserRepository(session).upsert_by_firebase_uid(
                uid, f"{uid}@example.test", uid.title()
            )
            if own_firm:
                await ensure_own_firm(session, user)
            return user

    async def personal_org(self, uid: str) -> uuid.UUID:
        """The firm :meth:`user` gave them (formerly their personal org)."""
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

    async def member(
        self, org_id: uuid.UUID, uid: str, role: OrgRole, *, own_firm: bool = True
    ) -> None:
        user = await self.user(uid, own_firm=own_firm)
        async with session_scope(self.sessionmaker) as session:
            await UserRepository(session).add_membership(org_id, user.id, role)

    async def outputs(self, study_id: str | uuid.UUID, revision: int = 1) -> dict[str, str]:
        """One correction target of every type for ``revision`` of the study.

        Synthetic rows only: an appraisal and a photo document (random sha256, URIs that
        point at nothing), an extraction with one field, a room, a component of the photo
        in that room, and an asset of the component. Returns ``{entity_type: id}`` for
        ``extraction_field``, ``room``, ``component``, ``asset`` and ``photo``,
        ``room_component`` (the room: a component is added to a room) and ``photo_room``
        (the photo; the room it moves to is in ``after``).
        """
        sid = uuid.UUID(str(study_id))
        async with session_scope(self.sessionmaker) as session:
            documents = DocumentRepository(session)
            ids: dict[str, uuid.UUID] = {}
            for kind in (DocumentKind.APPRAISAL, DocumentKind.PHOTO):
                document_id = uuid.uuid4()
                await documents.create(
                    study_id=sid,
                    kind=kind,
                    filename=f"{kind.value}-r{revision}-{document_id.hex[:6]}.bin",
                    content_type="application/octet-stream",
                    size_bytes=10,
                    sha256=uuid.uuid4().hex * 2,
                    gcs_uri=f"gs://basis-local/studies/{sid}/{document_id}/file",
                    uploaded_by=None,
                    document_id=document_id,
                )
                ids[kind.value] = document_id
            extraction = Extraction(
                study_id=sid,
                document_id=ids["appraisal"],
                revision=revision,
                method="stub",
                overall_confidence=None,
                raw={},
            )
            room = Room(
                study_id=sid, revision=revision, name="Kitchen", room_type="kitchen", source="stub"
            )
            session.add_all([extraction, room])
            await session.flush()
            field = ExtractionField(
                extraction_id=extraction.id,
                study_id=sid,
                section="subject",
                field="year_built",
                value_text="1989",
                source="stub",
            )
            component = Component(
                study_id=sid,
                revision=revision,
                document_id=ids["photo"],
                room_id=room.id,
                label="cabinet",
            )
            session.add_all([field, component])
            await session.flush()
            asset = Asset(
                study_id=sid,
                revision=revision,
                component_id=component.id,
                category="cabinetry",
                bucket=Bucket.Y5,
                recovery_period=Decimal("5"),
                method=ClassificationMethod.RULE,
            )
            session.add(asset)
            await session.flush()
            return {
                "extraction_field": str(field.id),
                "room": str(room.id),
                "component": str(component.id),
                "room_component": str(room.id),
                "asset": str(asset.id),
                "photo": str(ids["photo"]),
                "photo_room": str(ids["photo"]),
            }

    async def stage(
        self,
        study_id: str | uuid.UUID,
        stage: Stage,
        revision: int = 1,
        *,
        components_gate: bool | None = None,
    ) -> None:
        """Put the study at ``stage``; ``components_gate`` as ``record_run_start`` records
        it (patch components-gate-v1; left as it is when not given: null for a new study,
        which the API treats as the single rooms review)."""
        values: dict[str, Any] = {
            "current_stage": stage,
            "status": status_for_stage(stage),
            "revision": revision,
        }
        if components_gate is not None:
            values["components_gate"] = components_gate
        async with session_scope(self.sessionmaker) as session:
            await session.execute(
                update(Study).where(Study.id == uuid.UUID(str(study_id))).values(values)
            )
