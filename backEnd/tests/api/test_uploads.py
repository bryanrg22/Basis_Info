"""Document uploads: signed URL, direct upload, confirmation (S1_SPEC sections 5 and 6).

The browser's PUT is played by ``LocalStorage.put_signed``, which enforces what a GCS V4
signed URL with a content-length range enforces (exact size, signed content type).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apitest_support import Api, Seed, sha256_hex
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.schemas.documents import MAX_UPLOAD_BYTES
from basis.db.enums import OrgRole
from basis.db.models import Document
from basis.storage import LocalStorage, ObjectNotFound, UploadRejected

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"appraisal body " * 200


async def document_row(sm: async_sessionmaker[AsyncSession], document_id: str) -> Document:
    async with sm() as session:
        row = await session.get(Document, uuid.UUID(document_id))
    assert row is not None
    return row


async def upload(
    api: Api, uid: str, study_id: str, data: bytes = PDF, **overrides: Any
) -> dict[str, Any]:
    response = await api.request_upload(uid, study_id, data, **overrides)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def complete_path(study_id: str, document_id: str) -> str:
    return f"/v1/studies/{study_id}/documents/{document_id}/complete"


# --------------------------------------------------------------------------- happy path


async def test_request_upload_registers_the_document_and_signs_a_put(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    before = datetime.now(UTC)

    ticket = await upload(api, "alice", study["id"])

    document, signed = ticket["document"], ticket["upload"]
    assert document["study_id"] == study["id"]
    assert (document["kind"], document["filename"], document["content_type"]) == (
        "appraisal",
        "appraisal.pdf",
        "application/pdf",
    )
    assert (document["bytes"], document["sha256"]) == (len(PDF), sha256_hex(PDF))
    assert document["uploaded_at"] is None
    assert signed["method"] == "PUT"
    assert signed["url"].startswith("local-upload://")
    assert signed["headers"] == {
        "Content-Type": "application/pdf",
        "x-goog-content-length-range": f"{len(PDF)},{len(PDF)}",
    }
    expires = datetime.fromisoformat(signed["expires_at"])
    assert before + timedelta(minutes=14) < expires <= datetime.now(UTC) + timedelta(minutes=15)

    row = await document_row(sessionmaker, document["id"])
    alice = await seed.user("alice")
    assert row.gcs_uri == (f"gs://basis-local/studies/{study['id']}/{document['id']}/appraisal.pdf")
    assert row.uploaded_by == alice.id
    assert row.uploaded_at is None


async def test_full_upload_flow(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]

    uri = storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    response = await api.post("alice", complete_path(study["id"], doc_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == doc_id and body["uploaded_at"] is not None
    row = await document_row(sessionmaker, doc_id)
    assert row.gcs_uri == uri and row.uploaded_at is not None
    listed = (await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]
    assert [(d["id"], d["uploaded_at"]) for d in listed] == [(doc_id, body["uploaded_at"])]


async def test_confirming_twice_returns_the_same_document(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    path = complete_path(study["id"], ticket["document"]["id"])

    first = await api.post("alice", path)
    second = await api.post("alice", path)  # new idempotency key: a real second call

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()


async def test_photo_and_other_kinds(api: Api) -> None:
    study = await api.create_study("alice")
    photo = await upload(
        api,
        "alice",
        study["id"],
        b"\xff\xd8 jpeg",
        kind="photo",
        filename="IMG_1.jpg",
        content_type="image/jpeg",
    )
    other = await upload(
        api,
        "alice",
        study["id"],
        b"a,b\n1,2\n",
        kind="other",
        filename="rooms.csv",
        content_type="text/csv",
    )
    assert photo["document"]["kind"] == "photo"
    assert other["document"]["content_type"] == "text/csv"


# --------------------------------------------------------------------------- duplicates


async def test_duplicate_sha256_in_the_same_study_is_409(api: Api) -> None:
    study = await api.create_study("alice")
    first = await upload(api, "alice", study["id"])

    response = await api.request_upload("alice", study["id"], PDF, filename="copy.pdf")

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["type"].endswith("/duplicate-document")
    assert first["document"]["id"] in problem["detail"]


async def test_duplicate_detection_ignores_hex_case(api: Api) -> None:
    study = await api.create_study("alice")
    await upload(api, "alice", study["id"])
    response = await api.request_upload("alice", study["id"], PDF, sha256=sha256_hex(PDF).upper())
    assert response.status_code == 409


async def test_uppercase_sha256_is_stored_lowercase(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"], sha256=sha256_hex(PDF).upper())
    assert ticket["document"]["sha256"] == sha256_hex(PDF)


async def test_same_file_in_another_study_is_allowed(api: Api) -> None:
    one = await api.create_study("alice", "One")
    two = await api.create_study("alice", "Two")
    await upload(api, "alice", one["id"])
    await upload(api, "alice", two["id"])


# --------------------------------------------------------------------------- limits


@pytest.mark.parametrize(
    "overrides",
    [
        {"bytes": 0},
        {"bytes": -1},
        {"bytes": MAX_UPLOAD_BYTES + 1},
        {"bytes": "12"},
        {"sha256": "abc"},
        {"sha256": "g" * 64},
        {"kind": "video"},
        {"content_type": "image/png"},  # not allowed for an appraisal
        {"content_type": "application/zip", "kind": "other"},
        {"content_type": "application/pdf", "kind": "photo"},
        {"content_type": "APPLICATION/PDF"},
        {"filename": ""},
        {"filename": "."},
        {"filename": ".."},
        {"filename": "   "},
        {"filename": "dir/appraisal.pdf"},
        {"filename": "C:\\fakepath\\appraisal.pdf"},
        {"filename": "bad\x00name.pdf"},
        {"filename": "x" * 256},
        {"unexpected": 1},
    ],
)
async def test_size_type_and_name_limits(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], overrides: dict[str, Any]
) -> None:
    study = await api.create_study("alice")
    response = await api.request_upload("alice", study["id"], PDF, **overrides)
    assert response.status_code == 422, response.text
    async with sessionmaker() as session:
        assert (await session.scalars(select(Document))).all() == []


async def test_largest_allowed_size_is_accepted(api: Api) -> None:
    study = await api.create_study("alice")
    response = await api.request_upload("alice", study["id"], PDF, bytes=MAX_UPLOAD_BYTES)
    assert response.status_code == 201


async def test_long_multibyte_filename_is_shortened_keeping_the_extension(api: Api) -> None:
    study = await api.create_study("alice")
    name = "é" * 250 + ".pdf"  # 504 UTF-8 bytes
    ticket = await upload(api, "alice", study["id"], filename=name)
    stored = ticket["document"]["filename"]
    assert stored.endswith(".pdf")
    assert len(stored.encode("utf-8")) <= 255
    assert stored == "é" * 125 + ".pdf"


async def test_filename_is_trimmed_and_nfc_normalized(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"], filename="  Cafe\u0301.pdf ")
    assert ticket["document"]["filename"] == "Caf\u00e9.pdf"


async def test_signed_url_accepts_only_the_declared_size_and_type(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    url, headers = ticket["upload"]["url"], ticket["upload"]["headers"]
    with pytest.raises(UploadRejected):
        storage.put_signed(url, PDF + b"x", headers)
    with pytest.raises(UploadRejected):
        storage.put_signed(url, PDF, {**headers, "Content-Type": "image/png"})


# --------------------------------------------------------------------------- verification


async def test_complete_before_the_upload_is_409(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    response = await api.post("alice", complete_path(study["id"], ticket["document"]["id"]))
    assert response.status_code == 409
    assert response.json()["type"].endswith("/upload-missing")


@pytest.mark.parametrize(
    ("stored", "content_type", "problem"),
    [
        (PDF + b"extra", "application/pdf", "bytes"),
        (PDF[:-1] + b"X", "application/pdf", "sha256"),
        (PDF, "image/png", "content type"),
    ],
    ids=["size", "checksum", "content-type"],
)
async def test_mismatched_object_is_409_and_deleted(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    stored: bytes,
    content_type: str,
    problem: str,
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]
    row = await document_row(sessionmaker, doc_id)
    storage.write(row.gcs_uri, stored, content_type)

    response = await api.post("alice", complete_path(study["id"], doc_id))

    assert response.status_code == 409
    assert response.json()["type"].endswith("/upload-mismatch")
    assert problem in response.json()["detail"]
    with pytest.raises(ObjectNotFound):
        await storage.stat(row.gcs_uri)
    assert (await document_row(sessionmaker, doc_id)).uploaded_at is None

    # Uploading the right bytes and confirming with a new key then succeeds.
    storage.write(row.gcs_uri, PDF, "application/pdf")
    retry = await api.post("alice", complete_path(study["id"], doc_id))
    assert retry.status_code == 200
    assert retry.json()["uploaded_at"] is not None


async def test_object_without_a_reported_content_type_is_verified_by_size_and_hash(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    row = await document_row(sessionmaker, ticket["document"]["id"])
    storage.write(row.gcs_uri, PDF)  # no content type recorded
    response = await api.post("alice", complete_path(study["id"], ticket["document"]["id"]))
    assert response.status_code == 200


async def test_complete_an_unknown_document_is_404(api: Api) -> None:
    study = await api.create_study("alice")
    response = await api.post("alice", complete_path(study["id"], str(uuid.uuid4())))
    assert response.status_code == 404


async def test_complete_a_document_of_another_study_is_404(api: Api, storage: LocalStorage) -> None:
    one = await api.create_study("alice", "One")
    two = await api.create_study("alice", "Two")
    ticket = await upload(api, "alice", one["id"])
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    response = await api.post("alice", complete_path(two["id"], ticket["document"]["id"]))
    assert response.status_code == 404


# --------------------------------------------------------------------------- access


async def test_archived_study_takes_no_uploads(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    assert (await api.delete("alice", f"/v1/studies/{study['id']}")).status_code == 200

    new = await api.request_upload("alice", study["id"], b"other bytes")
    done = await api.post("alice", complete_path(study["id"], ticket["document"]["id"]))

    assert new.status_code == 409 and new.json()["type"].endswith("/study-archived")
    assert done.status_code == 409


@pytest.mark.parametrize(("role", "expected"), [(OrgRole.VIEWER, 403), (OrgRole.ENGINEER, 201)])
async def test_uploads_need_the_engineer_role(
    api: Api, seed: Seed, role: OrgRole, expected: int
) -> None:
    org = await seed.org()
    await seed.member(org, "carol", OrgRole.OWNER)
    await seed.member(org, "alice", role)
    study = await api.create_study("carol", org_id=str(org))
    response = await api.request_upload("alice", study["id"], PDF)
    assert response.status_code == expected


async def test_non_member_cannot_upload_or_confirm(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    assert (await api.request_upload("bob", study["id"], b"x")).status_code == 404
    response = await api.post("bob", complete_path(study["id"], ticket["document"]["id"]))
    assert response.status_code == 404
