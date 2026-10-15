"""GcsStorage against a real bucket (S1_SPEC section 6, BUILD_PLAN 6.5).

Skipped unless ``BASIS_GCS_TEST_BUCKET`` names a bucket the Application Default
Credentials can write to and sign for. Everything is written under a random prefix and
deleted afterwards.

* A browser-style PUT to the signed URL with the returned headers succeeds, and ``stat``
  then reports the uploaded size and MD5.
* A body of a different size is rejected by GCS (the length range is signed in).
* ``read`` returns the bytes; ``delete`` removes the object and is idempotent.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest

from basis.storage import GcsStorage, ObjectNotFound, object_uri

BUCKET = os.environ.get("BASIS_GCS_TEST_BUCKET")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not BUCKET, reason="BASIS_GCS_TEST_BUCKET is not set"),
]


@pytest.fixture
async def gcs_uri() -> AsyncIterator[tuple[GcsStorage, str]]:
    assert BUCKET is not None
    storage = GcsStorage(BUCKET)
    uri = object_uri(BUCKET, uuid.uuid4(), uuid.uuid4(), "integration.pdf")
    try:
        yield storage, uri
    finally:
        await storage.delete(uri)


async def test_signed_upload_stat_read_delete(gcs_uri: tuple[GcsStorage, str]) -> None:
    storage, uri = gcs_uri
    data = b"%PDF-1.7 basis integration " + uuid.uuid4().bytes
    upload = await storage.signed_put_url(uri, "application/pdf", len(data), timedelta(minutes=5))

    async with httpx.AsyncClient(timeout=30) as http:
        wrong = await http.put(upload.url, content=data + b"x", headers=upload.headers)
        assert wrong.status_code in (400, 403), wrong.text
        ok = await http.put(upload.url, content=data, headers=upload.headers)
        assert ok.status_code == 200, ok.text

    stat = await storage.stat(uri)
    assert stat.size == len(data)
    assert stat.md5 == base64.b64encode(hashlib.md5(data).digest()).decode()
    assert stat.content_type == "application/pdf"
    assert await storage.read(uri) == data

    await storage.delete(uri)
    await storage.delete(uri)
    with pytest.raises(ObjectNotFound):
        await storage.stat(uri)
