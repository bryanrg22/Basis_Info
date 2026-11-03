"""``GET /v1/ops/studies/{id}/documents`` and ``.../documents/{doc_id}/content``: Basis
support opens a firm's uploaded files (docs/api/OPS_DOCUMENTS.md; Bryan, 2026-11-02).

* An operator who is not a member of the firm lists the study's documents (with the
  uploader and the firm) and opens one: the exact bytes, the registered content type,
  ``nosniff``, the filename, never cached (``no-store``, no ``ETag``, no 304).
* Every open is recorded before the bytes are read: one ``operator.document_accessed`` row
  per open on the study's timeline (the operator, the document, the reason, the record it
  came from, the file's name, kind, sha256 and size), which the firm reads on its own
  ``GET .../timeline``; an open whose bytes are gone is still recorded (the access was
  granted), a refused one (no reason, unknown, not confirmed) records nothing.
* A firm role, owner included, never grants it (403, nothing recorded).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    bearer,
    build_api_app,
)
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_document_content import JPEG, confirmed

from basis.api.routes.ops_documents import ACCESS_EVENT, MAX_REASON_CHARS
from basis.db.models import Document, StudyEvent
from basis.errors.codes import E
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

OPERATOR = "ops-admin"
REASON = "support: study stuck on a missing living area"


@pytest.fixture
def ops_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return build_api_app(
        api_settings(database_url, ops_operator_uids=(OPERATOR,)),
        sessionmaker,
        storage,
        verifier,
    )


@pytest.fixture
async def ops(ops_app: FastAPI) -> AsyncIterator[Api]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ops_app), base_url=BASE_URL
    ) as client:
        yield Api(client)


def listing(study_id: str) -> str:
    return f"/v1/ops/studies/{study_id}/documents"


def content(study_id: str, document_id: str) -> str:
    return f"/v1/ops/studies/{study_id}/documents/{document_id}/content"


async def accesses(
    sessionmaker: async_sessionmaker[AsyncSession], study_id: str
) -> list[StudyEvent]:
    async with sessionmaker() as session:
        stmt = (
            select(StudyEvent)
            .where(StudyEvent.study_id == uuid.UUID(study_id))
            .where(StudyEvent.event_type == ACCESS_EVENT)
            .order_by(StudyEvent.seq)
        )
        return list((await session.scalars(stmt)).all())


async def firm_study(api: Api, ops: Api, storage: LocalStorage) -> tuple[str, dict[str, Any]]:
    """Alice's study with one confirmed photo; the operator is not a member of her firm."""
    study = await api.create_study("alice", "Harbor House")
    document = await confirmed(api, storage, "alice", study["id"])
    return study["id"], document


async def test_an_operator_lists_a_firms_documents_with_the_uploader(
    api: Api, ops: Api, seed: Seed, storage: LocalStorage
) -> None:
    sid, document = await firm_study(api, ops, storage)
    pending = (await api.request_upload("alice", sid, b"%PDF-1.7 not yet")).json()["document"]
    response = await ops.get(OPERATOR, listing(sid))
    assert response.status_code == 200, response.text
    body = response.json()
    alice = await seed.user("alice")
    assert body["study_id"] == sid and body["property_name"] == "Harbor House"
    assert body["org_id"] == str(await seed.personal_org("alice")) and body["firm_name"]
    by_id = {d["id"]: d for d in body["documents"]}
    assert set(by_id) == {document["id"], pending["id"]}
    assert by_id[document["id"]]["uploaded_by"] == str(alice.id)
    assert by_id[document["id"]]["uploaded_at"] and by_id[pending["id"]]["uploaded_at"] is None


async def test_an_operator_opens_a_file_and_the_open_is_recorded_for_the_firm(
    api: Api,
    ops: Api,
    seed: Seed,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, document = await firm_study(api, ops, storage)
    record_id = str(uuid.uuid4())
    response = await ops.get(
        OPERATOR,
        content(sid, document["id"]),
        params={"reason": f"  {REASON} ", "client_error_id": record_id},
    )
    assert response.status_code == 200, response.text
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "etag" not in response.headers
    assert response.headers["content-disposition"].startswith('inline; filename="kitchen 01.jpg"')

    operator = await seed.user(OPERATOR, own_firm=False)
    [row] = await accesses(sessionmaker, sid)
    assert (row.actor_type, row.actor_id) == ("operator", operator.id)
    assert (row.entity_type, row.entity_id) == ("document", uuid.UUID(document["id"]))
    assert row.revision == 1 and row.stage is not None
    assert row.detail == {
        "reason": REASON,
        "client_error_id": record_id,
        "filename": "kitchen 01.jpg",
        "kind": "photo",
        "sha256": document["sha256"],
        "bytes": len(JPEG),
    }
    # The firm sees who opened what, and why, on its own timeline.
    timeline = await api.get("alice", f"/v1/studies/{sid}/timeline")
    assert timeline.status_code == 200, timeline.text
    [seen] = [e for e in timeline.json()["items"] if e["event_type"] == ACCESS_EVENT]
    assert seen["actor_type"] == "operator" and seen["actor_id"] == str(operator.id)
    assert seen["detail"]["reason"] == REASON


async def test_every_open_is_recorded_and_never_answered_from_a_cache(
    api: Api, ops: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, document = await firm_study(api, ops, storage)
    path = content(sid, document["id"])
    first = await ops.get(OPERATOR, path, params={"reason": REASON})
    again = await ops.client.get(
        path,
        params={"reason": REASON},
        headers={**bearer(OPERATOR), "If-None-Match": f'"{document["sha256"]}"'},
    )
    assert (first.status_code, again.status_code) == (200, 200)
    assert again.content == JPEG
    assert len(await accesses(sessionmaker, sid)) == 2


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"reason": ""},
        {"reason": "ok"},
        {"reason": "x" * (MAX_REASON_CHARS + 1)},
        {"reason": REASON, "client_error_id": "not-a-uuid"},
    ],
)
async def test_an_open_without_a_usable_reason_is_refused_and_not_recorded(
    api: Api,
    ops: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    params: dict[str, str],
) -> None:
    sid, document = await firm_study(api, ops, storage)
    response = await ops.get(OPERATOR, content(sid, document["id"]), params=params)
    assert response.status_code == 422, response.text
    assert await accesses(sessionmaker, sid) == []


async def test_a_firm_role_never_grants_it(
    api: Api, ops: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, document = await firm_study(api, ops, storage)
    for uid in ("alice", "bob"):  # the study's owner, and another firm's owner
        for path in (listing(sid), content(sid, document["id"])):
            response = await ops.get(uid, path, params={"reason": REASON})
            assert response.status_code == 403, (uid, path, response.text)
            assert response.json()["code"] == E.API_NOT_OPERATOR
    assert await accesses(sessionmaker, sid) == []


async def test_unknown_study_or_document_and_unconfirmed_uploads_record_nothing(
    api: Api, ops: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, document = await firm_study(api, ops, storage)
    other = await api.create_study("alice", "Other House")
    pending = (await api.request_upload("alice", sid, b"%PDF-1.7 not yet")).json()["document"]
    reason = {"reason": REASON}
    missing_study = await ops.get(OPERATOR, listing(str(uuid.uuid4())))
    assert missing_study.status_code == 404
    assert missing_study.json()["code"] == E.STUDY_NOT_FOUND
    cases = [
        (content(str(uuid.uuid4()), document["id"]), 404, E.STUDY_NOT_FOUND),
        (content(sid, str(uuid.uuid4())), 404, E.UPLOAD_DOCUMENT_NOT_FOUND),
        (content(other["id"], document["id"]), 404, E.UPLOAD_DOCUMENT_NOT_FOUND),
        (content(sid, pending["id"]), 409, None),
    ]
    for path, status, code in cases:
        response = await ops.get(OPERATOR, path, params=reason)
        assert response.status_code == status, (path, response.text)
        if code is not None:
            assert response.json()["code"] == code
    assert await accesses(sessionmaker, sid) == []
    assert await accesses(sessionmaker, other["id"]) == []


async def test_bytes_gone_from_storage_are_404_and_the_granted_access_stays_recorded(
    api: Api, ops: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, document = await firm_study(api, ops, storage)
    async with sessionmaker() as session:
        uri = (await session.get(Document, uuid.UUID(document["id"]))).gcs_uri  # type: ignore[union-attr]
    await storage.delete(uri)
    response = await ops.get(OPERATOR, content(sid, document["id"]), params={"reason": REASON})
    assert response.status_code == 404
    assert response.json()["code"] == E.UPLOAD_BYTES_MISSING
    assert len(await accesses(sessionmaker, sid)) == 1
