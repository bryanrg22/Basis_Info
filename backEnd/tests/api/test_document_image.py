"""``GET /v1/studies/{id}/documents/{doc_id}/image``: a photo as a browser can show it.

* A HEIC or HEIF photo comes back as an upright JPEG (``image/jpeg``), named ``.jpg``, with
  the rule's name in its ``ETag`` (``"<sha256>.display-v1"``); ``If-None-Match`` with it is
  304. ``.../content`` still returns the HEIC's own bytes.
* JPEG, PNG and WebP come back as uploaded, with the sha256 as the ETag (the same response
  as ``.../content``).
* Not a photo: 409 ``not-a-photo``. A HEIC that cannot be decoded: 409
  ``unreadable-photo``. Not confirmed: 409 ``upload-missing``. A study the caller cannot
  see: 404. Bytes gone from storage: 404. A viewer may read it; a token is required.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer, sha256_hex
from fixtures import heic_fixture
from PIL import Image

from basis.db.enums import OrgRole
from basis.errors.codes import E
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

JPEG = b"\xff\xd8\xff\xe0" + b"photo bytes " * 300
HEIC = heic_fixture("rotated_90cw.heic")


async def confirmed(
    api: Api, storage: LocalStorage, uid: str, study_id: str, data: bytes, **body: Any
) -> dict[str, Any]:
    ticket = (await api.request_upload(uid, study_id, data, **body)).json()
    storage.put_signed(ticket["upload"]["url"], data, ticket["upload"]["headers"])
    path = f"/v1/studies/{study_id}/documents/{ticket['document']['id']}/complete"
    response = await api.post(uid, path)
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


async def heic(api: Api, storage: LocalStorage, study_id: str, data: bytes = HEIC) -> str:
    document = await confirmed(
        api,
        storage,
        "alice",
        study_id,
        data,
        filename="IMG_4161.HEIC",
        content_type="image/heic",
        kind="photo",
    )
    return str(document["id"])


def image_path(study_id: str, document_id: str) -> str:
    return f"/v1/studies/{study_id}/documents/{document_id}/image"


async def test_a_heic_comes_back_as_an_upright_jpeg(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"])

    response = await api.get("alice", image_path(study["id"], doc))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == f'"{sha256_hex(HEIC)}.display-v1"'
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"IMG_4161.jpg\"; filename*=UTF-8''IMG_4161.jpg"
    )
    picture = Image.open(io.BytesIO(response.content))
    assert picture.format == "JPEG" and picture.size == (240, 320)
    red = picture.convert("RGB").getpixel((230, 10))
    assert isinstance(red, tuple) and red[0] > 180 and red[2] < 80  # the red block, top right


async def test_the_original_heic_is_still_served_by_content(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"])
    response = await api.get("alice", f"/v1/studies/{study['id']}/documents/{doc}/content")
    assert response.status_code == 200
    assert response.content == HEIC and response.headers["content-type"] == "image/heic"


async def test_if_none_match_with_the_converted_etag_is_not_modified(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"])
    path = image_path(study["id"], doc)
    etag = (await api.get("alice", path)).headers["etag"]
    original = f'"{sha256_hex(HEIC)}"'

    cached = await api.client.get(path, headers={**bearer("alice"), "If-None-Match": etag})
    other_rule = await api.client.get(path, headers={**bearer("alice"), "If-None-Match": original})

    assert cached.status_code == 304 and cached.content == b""
    assert other_rule.status_code == 200  # the original's tag is not the copy's


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("kitchen.jpg", "image/jpeg"), ("plan.png", "image/png"), ("hall.webp", "image/webp")],
)
async def test_browser_formats_come_back_as_uploaded(
    api: Api, storage: LocalStorage, filename: str, content_type: str
) -> None:
    study = await api.create_study("alice")
    body = {"filename": filename, "content_type": content_type, "kind": "photo"}
    document = await confirmed(api, storage, "alice", study["id"], JPEG, **body)

    response = await api.get("alice", image_path(study["id"], document["id"]))

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == content_type
    assert response.headers["etag"] == f'"{sha256_hex(JPEG)}"'


async def test_a_document_that_is_not_a_photo_is_a_conflict(
    api: Api, storage: LocalStorage
) -> None:
    study = await api.create_study("alice")
    body = {"filename": "a.pdf", "content_type": "application/pdf", "kind": "appraisal"}
    document = await confirmed(api, storage, "alice", study["id"], b"%PDF-1.7 report", **body)

    response = await api.get("alice", image_path(study["id"], document["id"]))

    assert response.status_code == 409
    assert response.json()["type"].endswith("/not-a-photo")
    assert response.json()["code"] == E.UPLOAD_NOT_A_PHOTO


async def test_an_unreadable_heic_is_a_conflict(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"], HEIC[:300])

    response = await api.get("alice", image_path(study["id"], doc))

    assert response.status_code == 409
    problem = response.json()
    assert problem["type"].endswith("/unreadable-photo")
    assert problem["code"] == E.UPLOAD_UNREADABLE_PHOTO
    assert "cannot be shown" in problem["detail"]


async def test_an_unconfirmed_upload_is_a_conflict(api: Api) -> None:
    study = await api.create_study("alice")
    ticket = (
        await api.request_upload(
            "alice", study["id"], HEIC, filename="a.heic", content_type="image/heic", kind="photo"
        )
    ).json()
    response = await api.get("alice", image_path(study["id"], ticket["document"]["id"]))
    assert response.status_code == 409
    assert response.json()["type"].endswith("/upload-missing")


async def test_viewers_may_read_and_non_members_may_not(
    api: Api, storage: LocalStorage, seed: Seed
) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"])
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    path = image_path(study["id"], doc)

    viewer = await api.get("victor", path)
    stranger = await api.get("mallory", path)
    anonymous = await api.client.get(path)

    assert viewer.status_code == 200 and viewer.headers["content-type"] == "image/jpeg"
    assert stranger.status_code == 404
    assert anonymous.status_code == 401


async def test_bytes_missing_from_storage_are_not_found(api: Api, storage: LocalStorage) -> None:
    study = await api.create_study("alice")
    doc = await heic(api, storage, study["id"])
    await storage.delete(f"gs://basis-local/studies/{study['id']}/{doc}/IMG_4161.HEIC")

    response = await api.get("alice", image_path(study["id"], doc))

    assert response.status_code == 404
    assert "missing from storage" in response.json()["detail"]
