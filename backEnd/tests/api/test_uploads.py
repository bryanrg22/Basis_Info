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
from dbtest_support import queued_behind_a_lock
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.schemas.documents import MAX_UPLOAD_BYTES
from basis.db.enums import OrgRole
from basis.db.models import Document
from basis.errors.codes import E
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


# --------------------------------------------------------------------------- resuming
# docs/api/GATE_DRAFTS_AND_RESUME.md: a registration survives a reload (``uploaded_at`` null in
# the study's document list), ``POST .../documents/{id}/upload-url`` re-issues its signed URL,
# and a repeated registration of the same bytes is the 409 that names the document.


def upload_url_path(study_id: str, document_id: str) -> str:
    return f"/v1/studies/{study_id}/documents/{document_id}/upload-url"


async def test_a_registration_survives_a_reload_and_its_url_is_reissued(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])  # the browser then reloads: ticket gone
    doc_id = ticket["document"]["id"]

    # What a fresh client sees: the document registered, unconfirmed, with its sha256.
    listed = (await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]
    assert [(d["id"], d["uploaded_at"], d["sha256"]) for d in listed] == [
        (doc_id, None, sha256_hex(PDF))
    ]
    reissued = await api.post("alice", upload_url_path(study["id"], doc_id))
    assert reissued.status_code == 201, reissued.text
    body = reissued.json()
    assert body["document"] == ticket["document"]  # the same registration, not a new one
    assert body["upload"]["url"] != ticket["upload"]["url"]  # a fresh signature
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"] == ticket["upload"]["headers"]  # same size and type
    expires = datetime.fromisoformat(body["upload"]["expires_at"])
    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)

    storage.put_signed(body["upload"]["url"], PDF, body["upload"]["headers"])
    confirmed = await api.post("alice", complete_path(study["id"], doc_id))
    assert confirmed.status_code == 200 and confirmed.json()["uploaded_at"] is not None
    assert (await document_row(sessionmaker, doc_id)).uploaded_at is not None
    assert len((await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]) == 1


async def test_reissue_after_a_rejected_upload_then_the_right_bytes_confirm(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]
    storage.put_signed(ticket["upload"]["url"], PDF[:-1] + b"X", ticket["upload"]["headers"])
    rejected = await api.post("alice", complete_path(study["id"], doc_id))
    assert rejected.status_code == 409 and rejected.json()["type"].endswith("/upload-mismatch")
    body = (await api.post("alice", upload_url_path(study["id"], doc_id))).json()
    storage.put_signed(body["upload"]["url"], PDF, body["upload"]["headers"])
    confirmed = await api.post("alice", complete_path(study["id"], doc_id))
    assert confirmed.status_code == 200 and confirmed.json()["uploaded_at"] is not None


async def test_reissue_for_a_confirmed_document_is_409(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    assert (await api.post("alice", complete_path(study["id"], doc_id))).status_code == 200
    refused = await api.post("alice", upload_url_path(study["id"], doc_id))
    assert refused.status_code == 409, refused.text
    assert refused.json()["type"].endswith("/upload-complete")
    assert refused.json()["code"] == E.UPLOAD_ALREADY_CONFIRMED
    # The confirmed bytes are untouched and still served.
    assert (
        await api.get("alice", f"/v1/studies/{study['id']}/documents/{doc_id}/content")
    ).content == PDF


async def test_reissue_errors(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    other = await api.create_study("alice", "Other")
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]

    unknown = await api.post("alice", upload_url_path(study["id"], str(uuid.uuid4())))
    assert unknown.status_code == 404 and unknown.json()["code"] == E.UPLOAD_DOCUMENT_NOT_FOUND
    assert (await api.post("alice", upload_url_path(other["id"], doc_id))).status_code == 404
    assert (await api.post("vera", upload_url_path(study["id"], doc_id))).status_code == 403
    assert (await api.post("mallory", upload_url_path(study["id"], doc_id))).status_code == 404
    assert (await api.delete("alice", f"/v1/studies/{study['id']}")).status_code == 200
    archived = await api.post("alice", upload_url_path(study["id"], doc_id))
    assert archived.status_code == 409 and archived.json()["type"].endswith("/study-archived")


async def test_registering_the_same_bytes_again_names_the_unconfirmed_document(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """``POST .../documents`` is not replayed for the same sha256: it is the 409 that names the
    existing registration, and the study's document list carries the sha256 and ``uploaded_at``
    null, which is how a client matches a re-selected file to it and resumes."""
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]
    again = await api.request_upload("alice", study["id"], PDF)
    assert again.status_code == 409, again.text
    problem = again.json()
    assert problem["type"].endswith("/duplicate-document") and doc_id in problem["detail"]
    listed = (await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]
    assert len(listed) == 1  # nothing registered twice
    [match] = [d for d in listed if d["sha256"] == sha256_hex(PDF) and d["uploaded_at"] is None]
    assert match["id"] == doc_id
    async with sessionmaker() as session:
        rows = (await session.scalars(select(Document))).all()
    assert [str(r.id) for r in rows] == [doc_id]


async def test_the_resume_sequence_for_a_draft_study(api: Api, storage: LocalStorage) -> None:
    """The documented sequence: a client with no memory of the upload run finishes it from the
    study's document list alone: confirm what is uploaded, re-issue and upload the rest."""
    study = await api.create_study("alice")
    appraisal = await upload(api, "alice", study["id"])
    photo_bytes = b"\xff\xd8\xff" + b"jpeg body " * 100
    photo = await upload(
        api,
        "alice",
        study["id"],
        photo_bytes,
        kind="photo",
        content_type="image/jpeg",
        filename="kitchen.jpg",
    )
    # The appraisal's bytes went up before the reload; its confirmation did not.
    storage.put_signed(appraisal["upload"]["url"], PDF, appraisal["upload"]["headers"])
    local_files = {sha256_hex(PDF): PDF, sha256_hex(photo_bytes): photo_bytes}

    # ---- the reload: nothing but the study id and the files the engineer re-selects
    detail = (await api.get("alice", f"/v1/studies/{study['id']}")).json()
    assert detail["status"] == "draft" and detail["current_stage"] == "uploading"
    pending = [d for d in detail["documents"] if d["uploaded_at"] is None]
    assert {d["id"] for d in pending} == {appraisal["document"]["id"], photo["document"]["id"]}
    for document in pending:
        done = await api.post("alice", complete_path(study["id"], document["id"]))
        if done.status_code == 200:
            continue  # the bytes were already there: confirmed without a second upload
        assert done.status_code == 409 and done.json()["type"].endswith("/upload-missing")
        reissued = (await api.post("alice", upload_url_path(study["id"], document["id"]))).json()
        storage.put_signed(
            reissued["upload"]["url"],
            local_files[document["sha256"]],
            reissued["upload"]["headers"],
        )
        assert (
            await api.post("alice", complete_path(study["id"], document["id"]))
        ).status_code == 200
    after = (await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]
    assert len(after) == 2 and all(d["uploaded_at"] is not None for d in after)


# --------------------------------------------------------------------- races (EVAL_REPORT 60)


async def document_rows(sm: async_sessionmaker[AsyncSession], study_id: str) -> list[Document]:
    async with sm() as session:
        rows = await session.scalars(
            select(Document).where(Document.study_id == uuid.UUID(study_id))
        )
        return list(rows.all())


async def test_two_registrations_of_one_sha256_at_once_create_one_document(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Two tabs register the same file at the same moment. Nothing is locked before the
    insert, so the race is made real with a table lock both inserts wait on; released, the
    two inserts are granted together (their own locks do not conflict) and the unique key
    ``(study_id, sha256)`` arbitrates: one inserts, the other waits on the key until the
    first commits, then fails inside its savepoint and the route answers 409
    ``duplicate-document`` naming the winner. Which request wins is the index's choice, so
    the assertions are order-free. One row; only the winner got an upload URL. Without the
    unique key (verified by dropping it) both are 201 and two rows exist."""
    study = await api.create_study("alice")
    lock = text("LOCK TABLE documents IN SHARE ROW EXCLUSIVE MODE")
    responses = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: api.request_upload("alice", study["id"], PDF),
        lambda: api.request_upload("alice", study["id"], PDF),
    )
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    created = next(r for r in responses if r.status_code == 201)
    refused = next(r for r in responses if r.status_code == 409)
    winner = created.json()["document"]["id"]
    problem = refused.json()
    assert problem["type"].endswith("/duplicate-document") and winner in problem["detail"]
    assert [str(row.id) for row in await document_rows(sessionmaker, study["id"])] == [winner]
    listed = (await api.get("alice", f"/v1/studies/{study['id']}")).json()["documents"]
    assert [d["id"] for d in listed] == [winner]


async def test_two_confirmations_of_one_upload_at_once_record_one_event(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Both confirmations read the document unconfirmed, both verify the object (it is
    immutable), both reach the compare-and-set; held on the document's row lock until both
    are there, exactly one sets ``uploaded_at``. Both answer 200 with the same document and
    the timeline says ``document.uploaded`` once (it said it twice before the route asked
    the repository which call confirmed; EVAL_REPORT 60)."""
    study = await api.create_study("alice")
    ticket = await upload(api, "alice", study["id"])
    doc_id = ticket["document"]["id"]
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    path = complete_path(study["id"], doc_id)
    lock = select(Document).where(Document.id == uuid.UUID(doc_id)).with_for_update()
    first, second = await queued_behind_a_lock(
        sessionmaker, lock, lambda: api.post("alice", path), lambda: api.post("alice", path)
    )
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json() and first.json()["uploaded_at"] is not None
    timeline = await api.get(
        "alice", f"/v1/studies/{study['id']}/timeline", params={"event_type": "document.uploaded"}
    )
    [event] = timeline.json()["items"]
    assert event["entity_id"] == doc_id
