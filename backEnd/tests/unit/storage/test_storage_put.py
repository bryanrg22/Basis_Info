"""``Storage.put``: server-side writes (the worker's exports), added in Stage 2.

Both implementations overwrite, so writing the same bytes twice is idempotent, and only
URIs in the configured bucket are accepted by ``GcsStorage``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from basis.storage import GcsStorage, LocalStorage

URI = "gs://basis-test/studies/s/exports/rev-1.json"


async def test_local_put_writes_and_overwrites(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.put(URI, b'{"a":1}', "application/json")
    await storage.put(URI, b'{"a":2}', "application/json")
    assert await storage.read(URI) == b'{"a":2}'
    stat = await storage.stat(URI)
    assert (stat.size, stat.content_type) == (7, "application/json")


async def test_gcs_put_uploads_with_the_content_type() -> None:
    client = MagicMock(name="client")
    blob = client.bucket.return_value.blob.return_value
    gcs = GcsStorage("basis-test", client=client)
    await gcs.put(URI, b"{}", "application/json")
    client.bucket.assert_called_with("basis-test")
    client.bucket.return_value.blob.assert_called_with("studies/s/exports/rev-1.json")
    blob.upload_from_string.assert_called_once_with(b"{}", content_type="application/json")


async def test_gcs_put_refuses_another_bucket() -> None:
    gcs = GcsStorage("basis-test", client=MagicMock())
    with pytest.raises(ValueError, match="not in bucket"):
        await gcs.put("gs://other-bucket/x.json", b"{}", "application/json")
