"""Invariants for LocalStorage and object naming (S1_SPEC section 6).

* Object names are ``studies/<study_id>/<document_id>/<filename>`` with the filename's
  path components stripped (``/`` and ``\\``), control characters removed, and empty,
  ``.``/``..`` or over-long names rejected.
* URIs are ``gs://<bucket>/<name>``; anything else, or a name with empty/``.``/``..``
  segments, is rejected, so no URI escapes the storage root.
* A signed upload accepts exactly the signed size and content type until it expires,
  and the returned headers are the ones the uploader must send.
* ``stat`` reports size, base64 CRC32C and base64 MD5 the way GCS does; ``read`` and
  ``stat`` raise ``ObjectNotFound``; ``delete`` is idempotent.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from basis.storage import (
    LocalStorage,
    ObjectNotFound,
    UploadRejected,
    object_uri,
    parse_uri,
    safe_filename,
)
from basis.storage.base import CONTENT_LENGTH_RANGE_HEADER, object_name
from basis.storage.local import crc32c_b64, md5_b64

STUDY = uuid.UUID("11111111-1111-4111-8111-111111111111")
DOC = uuid.UUID("22222222-2222-4222-8222-222222222222")
BUCKET = "basis-test-bucket"
PDF = "application/pdf"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def storage(tmp_path: Path, clock: FakeClock) -> LocalStorage:
    return LocalStorage(tmp_path, clock=clock)


@pytest.fixture
def uri() -> str:
    return object_uri(BUCKET, STUDY, DOC, "appraisal.pdf")


# --------------------------------------------------------------------------- naming


def test_object_uri_layout() -> None:
    assert object_uri(BUCKET, STUDY, DOC, "appraisal.pdf") == (
        f"gs://{BUCKET}/studies/{STUDY}/{DOC}/appraisal.pdf"
    )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("/abs/path/file.pdf", "file.pdf"),
        ("C:\\Users\\me\\Desktop\\scan.pdf", "scan.pdf"),
        ("mixed/sep\\name.pdf", "name.pdf"),
        ("line\nbreak\r.pdf", "linebreak.pdf"),
        ("nul\x00byte.pdf", "nulbyte.pdf"),
        ("  spaced name.pdf  ", "spaced name.pdf"),
        ("Cafe\u0301.pdf", "Caf\u00e9.pdf"),  # NFC
        (".hidden", ".hidden"),
        ("unicode \u00fc\u00df.pdf", "unicode \u00fc\u00df.pdf"),
    ],
)
def test_safe_filename_strips_paths_and_controls(given: str, expected: str) -> None:
    assert safe_filename(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "dir/", "a/..", "\\", "\x00\x01"])
def test_safe_filename_rejects_unusable(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_filename(bad)


def test_safe_filename_length_limit() -> None:
    assert safe_filename("a" * 251 + ".pdf") == "a" * 251 + ".pdf"
    with pytest.raises(ValueError):
        safe_filename("a" * 252 + ".pdf")
    with pytest.raises(ValueError):
        safe_filename("\u00fc" * 128)  # 256 UTF-8 bytes


def test_object_name_never_has_traversal() -> None:
    name = object_name(STUDY, DOC, "../../../other-study/x.pdf")
    assert name == f"studies/{STUDY}/{DOC}/x.pdf"


@pytest.mark.parametrize("bucket", ["", "A", "ab", "Upper-Case", "-lead", "trail-", "a..b", "x/y"])
def test_object_uri_rejects_bad_buckets(bucket: str) -> None:
    with pytest.raises(ValueError):
        object_uri(bucket, STUDY, DOC, "f.pdf")


def test_parse_uri_round_trip(uri: str) -> None:
    assert parse_uri(uri) == (BUCKET, f"studies/{STUDY}/{DOC}/appraisal.pdf")


@pytest.mark.parametrize(
    "bad",
    [
        "s3://bucket/key",
        "gs://bucket",
        "gs://bucket/",
        f"gs://{BUCKET}/a/../b",
        f"gs://{BUCKET}/a//b",
        f"gs://{BUCKET}/./a",
        f"gs://{BUCKET}/a\\b",
        "gs://Bad_Bucket!/a",
        "/etc/passwd",
    ],
)
def test_parse_uri_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_uri(bad)


# --------------------------------------------------------------------------- read/stat/delete


async def test_write_read_stat(storage: LocalStorage, uri: str, tmp_path: Path) -> None:
    data = b"%PDF-1.7 hello"
    storage.write(uri, data, PDF)
    assert await storage.read(uri) == data
    stat = await storage.stat(uri)
    assert stat.size == len(data)
    assert stat.md5 == base64.b64encode(hashlib.md5(data).digest()).decode()
    assert stat.crc32c == crc32c_b64(data)
    assert stat.content_type == PDF
    assert (tmp_path / BUCKET / f"studies/{STUDY}/{DOC}/appraisal.pdf").read_bytes() == data


def test_checksums_match_gcs_encoding() -> None:
    # Known values: GCS reports CRC32C("hello world") as "yZRlqg==" and MD5 as
    # "XrY7u+Ae7tCTyyK7j1rNww==".
    assert crc32c_b64(b"hello world") == "yZRlqg=="
    assert md5_b64(b"hello world") == "XrY7u+Ae7tCTyyK7j1rNww=="


async def test_missing_object_raises(storage: LocalStorage, uri: str) -> None:
    with pytest.raises(ObjectNotFound):
        await storage.read(uri)
    with pytest.raises(ObjectNotFound):
        await storage.stat(uri)


async def test_delete_is_idempotent(storage: LocalStorage, uri: str) -> None:
    storage.write(uri, b"x")
    await storage.delete(uri)
    await storage.delete(uri)
    with pytest.raises(ObjectNotFound):
        await storage.read(uri)


async def test_prefix_is_not_an_object(storage: LocalStorage, uri: str) -> None:
    storage.write(uri, b"x")
    with pytest.raises(ObjectNotFound):
        await storage.read(f"gs://{BUCKET}/studies/{STUDY}")


async def test_rejects_traversal_uris(storage: LocalStorage) -> None:
    with pytest.raises(ValueError):
        await storage.read(f"gs://{BUCKET}/../outside")


# --------------------------------------------------------------------------- signed uploads


async def test_signed_upload_round_trip(storage: LocalStorage, uri: str, clock: FakeClock) -> None:
    data = b"0123456789"
    upload = await storage.signed_put_url(uri, PDF, len(data), timedelta(minutes=15))
    assert upload.method == "PUT"
    assert upload.expires_at == clock.now + timedelta(minutes=15)
    assert upload.headers == {"Content-Type": PDF, CONTENT_LENGTH_RANGE_HEADER: "10,10"}
    assert storage.put_signed(upload.url, data, upload.headers) == uri
    stat = await storage.stat(uri)
    assert (stat.size, stat.content_type) == (10, PDF)


async def test_signed_url_is_opaque(storage: LocalStorage, uri: str) -> None:
    upload = await storage.signed_put_url(uri, PDF, 1, timedelta(minutes=1))
    assert str(STUDY) not in upload.url


@pytest.mark.parametrize("size", [9, 11, 0])
async def test_signed_upload_rejects_wrong_size(storage: LocalStorage, uri: str, size: int) -> None:
    upload = await storage.signed_put_url(uri, PDF, 10, timedelta(minutes=15))
    with pytest.raises(UploadRejected, match="size"):
        storage.put_signed(upload.url, b"x" * size, upload.headers)


async def test_signed_upload_rejects_other_content_type(storage: LocalStorage, uri: str) -> None:
    upload = await storage.signed_put_url(uri, PDF, 1, timedelta(minutes=15))
    headers = {**upload.headers, "Content-Type": "text/html"}
    with pytest.raises(UploadRejected, match="Content-Type"):
        storage.put_signed(upload.url, b"x", headers)


async def test_signed_upload_requires_length_range_header(storage: LocalStorage, uri: str) -> None:
    upload = await storage.signed_put_url(uri, PDF, 1, timedelta(minutes=15))
    with pytest.raises(UploadRejected, match="content-length range"):
        storage.put_signed(upload.url, b"x", {"Content-Type": PDF})


async def test_signed_upload_header_names_are_case_insensitive(
    storage: LocalStorage, uri: str
) -> None:
    upload = await storage.signed_put_url(uri, PDF, 1, timedelta(minutes=15))
    headers = {k.upper(): v for k, v in upload.headers.items()}
    storage.put_signed(upload.url, b"x", headers)


async def test_signed_upload_expires(storage: LocalStorage, uri: str, clock: FakeClock) -> None:
    upload = await storage.signed_put_url(uri, PDF, 1, timedelta(minutes=15))
    clock.now += timedelta(minutes=15)
    with pytest.raises(UploadRejected, match="expired"):
        storage.put_signed(upload.url, b"x", upload.headers)


async def test_unknown_signed_url_rejected(storage: LocalStorage) -> None:
    with pytest.raises(UploadRejected):
        storage.put_signed("local-upload://forged", b"x", {})
    with pytest.raises(UploadRejected):
        storage.put_signed("https://example.com/x", b"x", {})


@pytest.mark.parametrize(
    ("content_type", "size", "expires"),
    [
        ("", 1, timedelta(minutes=1)),
        ("text/plain\r\nX-Evil: 1", 1, timedelta(minutes=1)),
        (PDF, 0, timedelta(minutes=1)),
        (PDF, -1, timedelta(minutes=1)),
        (PDF, 1, timedelta(0)),
        (PDF, 1, timedelta(days=7, seconds=1)),
    ],
)
async def test_signed_put_url_validates_arguments(
    storage: LocalStorage, uri: str, content_type: str, size: int, expires: timedelta
) -> None:
    with pytest.raises(ValueError):
        await storage.signed_put_url(uri, content_type, size, expires)


async def test_signed_put_url_validates_uri(storage: LocalStorage) -> None:
    with pytest.raises(ValueError):
        await storage.signed_put_url("gs://b/../x", PDF, 1, timedelta(minutes=1))
