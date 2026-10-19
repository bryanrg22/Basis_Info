"""``GET /v1/studies/{id}/documents/{doc_id}/content``: a confirmed document's bytes.

* A viewer (or higher) of the study gets the exact bytes with the registered content type,
  ``nosniff``, an inline ``Content-Disposition`` that carries a non-ASCII filename as
  ``filename*``, a private cache lifetime and the sha256 as a strong ``ETag``;
  ``If-None-Match`` with that tag is 304 with no body.
* Not confirmed yet: 409 ``upload-missing``. Unknown document, a document of another
  study, or a study the caller cannot see: 404. Bytes gone from storage: 404.
* It is a read: no idempotency key, and the read rate limit applies.
"""

from __future__ import annotations

from typing import Any

import pytest
from apitest_support import Api, Seed, bearer, sha256_hex

from basis.db.enums import OrgRole
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

JPEG = b"\xff\xd8\xff\xe0" + b"photo bytes " * 300


async def confirmed(
    api: Api, storage: LocalStorage, uid: str, study_id: str, data: bytes = JPEG, **overrides: Any
) -> dict[str, Any]:
    body = {
        "filename": "kitchen 01.jpg",
        "content_type": "image/jpeg",
        "kind": "photo",
        **overrides,
    }
    ticket = (await api.request_upload(uid, study_id, data, **body)).json()
    storage.put_signed(ticket["upload"]["url"], data, ticket["upload"]["headers"])
    path = f"/v1/studies/{study_id}/documents/{ticket['document']['id']}/complete"
    response = await api.post(uid, path)
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


def content_path(study_id: str, document_id: str) -> str:
    return f"/v1/studies/{study_id}/documents/{document_id}/content"


async def test_returns_the_bytes_with_their_type_and_cache_headers(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    document = await confirmed(api, storage, "alice", study["id"])

    response = await api.get("alice", content_path(study["id"], document["id"]))

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == f'"{sha256_hex(JPEG)}"'
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"kitchen 01.jpg\"; filename*=UTF-8''kitchen%2001.jpg"
    )


async def test_a_non_ascii_filename_is_sent_as_filename_star(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    document = await confirmed(
        api,
        storage,
        "alice",
        study["id"],
        "%PDF-1.7 tasación".encode(),
        filename="tasación.pdf",
        content_type="application/pdf",
        kind="appraisal",
    )
    response = await api.get("alice", content_path(study["id"], document["id"]))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"tasaci?n.pdf\"; filename*=UTF-8''tasaci%C3%B3n.pdf"
    )


async def test_if_none_match_with_the_etag_is_not_modified(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    document = await confirmed(api, storage, "alice", study["id"])
    path = content_path(study["id"], document["id"])
    etag = (await api.get("alice", path)).headers["etag"]

    cached = await api.client.get(path, headers={**bearer("alice"), "If-None-Match": etag})
    stale = await api.client.get(path, headers={**bearer("alice"), "If-None-Match": '"other"'})

    assert cached.status_code == 304 and cached.content == b""
    assert cached.headers["etag"] == etag
    assert stale.status_code == 200 and stale.content == JPEG


async def test_an_unconfirmed_upload_is_a_conflict(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = (
        await api.request_upload(
            "alice", study["id"], JPEG, filename="a.jpg", content_type="image/jpeg", kind="photo"
        )
    ).json()
    response = await api.get("alice", content_path(study["id"], ticket["document"]["id"]))
    assert response.status_code == 409
    assert response.json()["type"].endswith("/upload-missing")


async def test_unknown_or_foreign_documents_are_not_found(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    other = await api.create_study("alice", "Other property")
    document = await confirmed(api, storage, "alice", other["id"])

    unknown = await api.get(
        "alice", content_path(study["id"], "00000000-0000-4000-8000-000000000000")
    )
    foreign = await api.get("alice", content_path(study["id"], document["id"]))

    assert (unknown.status_code, foreign.status_code) == (404, 404)


async def test_viewers_may_read_and_non_members_may_not(
    api: Api, storage: LocalStorage, seed: Seed
) -> None:
    study = await api.create_study("alice")
    document = await confirmed(api, storage, "alice", study["id"])
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    path = content_path(study["id"], document["id"])

    viewer = await api.get("victor", path)
    stranger = await api.get("mallory", path)

    assert viewer.status_code == 200 and viewer.content == JPEG
    assert stranger.status_code == 404


async def test_bytes_missing_from_storage_are_not_found(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    document = await confirmed(api, storage, "alice", study["id"])
    uri = f"gs://basis-local/studies/{study['id']}/{document['id']}/kitchen 01.jpg"
    await storage.delete(uri)

    response = await api.get("alice", content_path(study["id"], document["id"]))

    assert response.status_code == 404
    assert "missing from storage" in response.json()["detail"]


async def test_requires_a_bearer_token(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    document = await confirmed(api, storage, "alice", study["id"])
    response = await api.client.get(content_path(study["id"], document["id"]))
    assert response.status_code == 401
