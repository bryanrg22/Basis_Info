"""``Storage.list``: the prefix listing the retention purge relies on.

* ``LocalStorage.list`` returns every object under a ``gs://bucket/prefix/`` URI, sorted,
  as ``gs://`` URIs; a trailing slash keeps a study's prefix from matching another study's
  id that merely starts the same way; an unknown bucket or prefix lists nothing; traversal
  is refused like every other URI.
* ``GcsStorage.list`` asks the client for ``list_blobs(bucket, prefix=name)`` and refuses
  a URI of another bucket.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from basis.storage import GcsStorage, LocalStorage
from basis.storage.base import parse_prefix

BUCKET = "basis-test-bucket"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "objects")


async def test_local_list_returns_sorted_uris_under_the_prefix(storage: LocalStorage) -> None:
    study = "aaaaaaaa-0000-4000-8000-000000000001"
    other = f"{study}0"  # a longer id that starts with the same characters
    uris = [
        f"gs://{BUCKET}/studies/{study}/doc1/photo.jpg",
        f"gs://{BUCKET}/studies/{study}/exports/rev-1.json",
        f"gs://{BUCKET}/studies/{study}/analyses/doc1/r1.json",
        f"gs://{BUCKET}/studies/{other}/doc9/photo.jpg",
        f"gs://{BUCKET}/other/{study}/x.bin",
    ]
    for uri in uris:
        storage.write(uri, b"x")

    listed = await storage.list(f"gs://{BUCKET}/studies/{study}/")

    assert listed == sorted(uris[:3])
    assert listed == sorted(listed)
    # Without the trailing slash the longer id matches too (a name prefix, not a directory).
    assert len(await storage.list(f"gs://{BUCKET}/studies/{study}")) == 4
    assert await storage.list(f"gs://{BUCKET}/studies/{study}/exports/") == [uris[1]]


async def test_local_list_of_nothing(storage: LocalStorage) -> None:
    assert await storage.list(f"gs://{BUCKET}/studies/none/") == []
    assert await storage.list("gs://no-such-bucket/studies/") == []
    storage.write(f"gs://{BUCKET}/studies/a/b", b"x")
    assert await storage.list(f"gs://{BUCKET}/nothing/") == []


@pytest.mark.parametrize(
    "bad", ["gs://bkt/../x/", "gs://bkt/a/../b/", "not-a-uri", "gs://bkt/", "gs://bkt//"]
)
async def test_local_list_rejects_unsafe_prefixes(storage: LocalStorage, bad: str) -> None:
    with pytest.raises(ValueError):
        await storage.list(bad)


def test_parse_prefix_keeps_the_trailing_slash() -> None:
    assert parse_prefix("gs://bkt/studies/x/") == ("bkt", "studies/x/")
    assert parse_prefix("gs://bkt/studies/x") == ("bkt", "studies/x")


async def test_gcs_list_uses_list_blobs_with_the_prefix() -> None:
    client = MagicMock(name="client")
    blob_a, blob_b = MagicMock(), MagicMock()
    blob_a.name, blob_b.name = "studies/s/exports/rev-1.json", "studies/s/d/a.pdf"
    client.list_blobs.return_value = [blob_a, blob_b]
    gcs = GcsStorage(BUCKET, client=client, credentials=MagicMock())

    listed = await gcs.list(f"gs://{BUCKET}/studies/s/")

    client.list_blobs.assert_called_once_with(BUCKET, prefix="studies/s/")
    assert listed == [
        f"gs://{BUCKET}/studies/s/d/a.pdf",
        f"gs://{BUCKET}/studies/s/exports/rev-1.json",
    ]
    with pytest.raises(ValueError, match="not in bucket"):
        await gcs.list("gs://another-bucket/studies/s/")
