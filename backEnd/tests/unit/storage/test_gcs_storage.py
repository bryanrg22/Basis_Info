"""Invariants for GcsStorage with the client mocked (S1_SPEC section 6).

* Signed URLs are V4 ``PUT`` with the content type and an exact
  ``x-goog-content-length-range`` signed in, and the returned headers are exactly what
  the uploader must send.
* Credentials that can sign (service-account keys) sign locally; credentials that cannot
  (Cloud Run's metadata-server credentials) are refreshed and sign through IAM with
  ``service_account_email`` and ``access_token``.
* Only URIs in the configured bucket are served; object names come from ``parse_uri``.
* ``stat`` returns GCS's size, CRC32C and MD5; missing objects raise ``ObjectNotFound``;
  ``delete`` of a missing object is not an error.
* No real client or credentials are created until first use.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import google.auth.compute_engine
import google.auth.credentials
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.api_core import exceptions as gexc
from google.cloud import storage
from google.oauth2 import service_account

from basis.storage import GcsStorage, ObjectNotFound, object_uri
from basis.storage.base import CONTENT_LENGTH_RANGE_HEADER

BUCKET = "basis-test-bucket"
STUDY = uuid.UUID("11111111-1111-4111-8111-111111111111")
DOC = uuid.UUID("22222222-2222-4222-8222-222222222222")
URI = object_uri(BUCKET, STUDY, DOC, "appraisal.pdf")
NAME = f"studies/{STUDY}/{DOC}/appraisal.pdf"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
SIGNED = "https://storage.googleapis.com/basis-test-bucket/x?X-Goog-Signature=abc"


class SigningCredentials(google.auth.credentials.Credentials, google.auth.credentials.Signing):
    """Stands in for service-account key credentials."""


def make_client() -> tuple[MagicMock, MagicMock, MagicMock]:
    client = MagicMock(name="client")
    bucket = client.bucket.return_value
    blob = bucket.blob.return_value
    blob.bucket = bucket
    blob.name = NAME
    blob.generate_signed_url.return_value = SIGNED
    return client, bucket, blob


def storage_with(credentials: Any) -> tuple[GcsStorage, MagicMock, MagicMock, MagicMock]:
    client, bucket, blob = make_client()
    gcs = GcsStorage(BUCKET, client=client, credentials=credentials, clock=lambda: NOW)
    return gcs, client, bucket, blob


# --------------------------------------------------------------------------- signing


async def test_signed_put_url_with_signing_credentials() -> None:
    gcs, client, bucket, blob = storage_with(MagicMock(spec=SigningCredentials))
    upload = await gcs.signed_put_url(URI, "application/pdf", 1234, timedelta(minutes=15))

    client.bucket.assert_called_once_with(BUCKET)
    bucket.blob.assert_called_once_with(NAME)
    blob.generate_signed_url.assert_called_once_with(
        version="v4",
        method="PUT",
        expiration=timedelta(minutes=15),
        content_type="application/pdf",
        headers={CONTENT_LENGTH_RANGE_HEADER: "1234,1234"},
    )
    assert upload.url == SIGNED
    assert upload.method == "PUT"
    assert upload.expires_at == NOW + timedelta(minutes=15)
    assert upload.headers == {
        "Content-Type": "application/pdf",
        CONTENT_LENGTH_RANGE_HEADER: "1234,1234",
    }


async def test_signed_put_url_with_metadata_credentials_uses_iam() -> None:
    credentials = MagicMock(spec=google.auth.compute_engine.Credentials)
    credentials.valid = False
    credentials.token = None

    def refresh(request: Any) -> None:
        credentials.valid = True
        credentials.token = "ya29.fake"  # pragma: allowlist secret
        credentials.service_account_email = "api@proj.iam.gserviceaccount.com"

    credentials.refresh.side_effect = refresh
    gcs, _, _, blob = storage_with(credentials)
    await gcs.signed_put_url(URI, "image/jpeg", 10, timedelta(minutes=5))

    credentials.refresh.assert_called_once()
    kwargs = blob.generate_signed_url.call_args.kwargs
    assert kwargs["service_account_email"] == "api@proj.iam.gserviceaccount.com"
    assert kwargs["access_token"] == "ya29.fake"  # pragma: allowlist secret
    assert kwargs["version"] == "v4"


async def test_valid_metadata_credentials_are_not_refreshed() -> None:
    credentials = MagicMock(spec=google.auth.compute_engine.Credentials)
    credentials.valid = True
    credentials.token = "ya29.cached"  # pragma: allowlist secret
    credentials.service_account_email = "api@proj.iam.gserviceaccount.com"
    gcs, _, _, blob = storage_with(credentials)
    await gcs.signed_put_url(URI, "image/jpeg", 10, timedelta(minutes=5))
    credentials.refresh.assert_not_called()
    assert blob.generate_signed_url.call_args.kwargs["access_token"] == "ya29.cached"


async def test_client_credentials_used_when_none_given() -> None:
    client, _, blob = make_client()
    client._credentials = MagicMock(spec=SigningCredentials)
    gcs = GcsStorage(BUCKET, client=client)
    await gcs.signed_put_url(URI, "application/pdf", 1, timedelta(minutes=1))
    assert "access_token" not in blob.generate_signed_url.call_args.kwargs


@pytest.mark.parametrize(
    ("content_type", "size", "expires"),
    [
        ("", 1, timedelta(minutes=1)),
        ("application/pdf", 0, timedelta(minutes=1)),
        ("application/pdf", 1, timedelta(days=8)),
    ],
)
async def test_signed_put_url_validates_before_calling_gcs(
    content_type: str, size: int, expires: timedelta
) -> None:
    gcs, client, _, blob = storage_with(MagicMock(spec=SigningCredentials))
    with pytest.raises(ValueError):
        await gcs.signed_put_url(URI, content_type, size, expires)
    blob.generate_signed_url.assert_not_called()


# --------------------------------------------------------------------------- bucket scoping


@pytest.mark.parametrize(
    "uri",
    [
        f"gs://other-bucket/{NAME}",
        f"gs://{BUCKET}/../x",
        "https://storage.googleapis.com/basis-test-bucket/x",
    ],
)
async def test_rejects_uris_outside_bucket(uri: str) -> None:
    gcs, client, _, _ = storage_with(MagicMock(spec=SigningCredentials))
    with pytest.raises(ValueError):
        await gcs.read(uri)
    with pytest.raises(ValueError):
        await gcs.signed_put_url(uri, "application/pdf", 1, timedelta(minutes=1))
    client.bucket.assert_not_called()


def test_rejects_invalid_bucket_name() -> None:
    with pytest.raises(ValueError):
        GcsStorage("Not_A_Bucket!")


def test_no_client_created_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must be lazy")

    monkeypatch.setattr("google.auth.default", forbidden)
    monkeypatch.setattr("google.cloud.storage.Client", forbidden)
    assert GcsStorage(BUCKET).bucket_name == BUCKET


# --------------------------------------------------------------------------- stat/read/delete


async def test_stat_reports_gcs_metadata() -> None:
    gcs, _, bucket, _ = storage_with(MagicMock(spec=SigningCredentials))
    loaded = MagicMock(size=11, crc32c="yZRlqg==", md5_hash="XrY7u+Ae7tCTyyK7j1rNww==")
    loaded.content_type = "application/pdf"
    bucket.get_blob.return_value = loaded
    stat = await gcs.stat(URI)
    bucket.get_blob.assert_called_once_with(NAME)
    assert (stat.size, stat.crc32c, stat.md5, stat.content_type) == (
        11,
        "yZRlqg==",
        "XrY7u+Ae7tCTyyK7j1rNww==",
        "application/pdf",
    )


async def test_stat_composite_object_has_no_md5() -> None:
    gcs, _, bucket, _ = storage_with(MagicMock(spec=SigningCredentials))
    bucket.get_blob.return_value = MagicMock(size=3, crc32c="AAAAAA==", md5_hash=None)
    assert (await gcs.stat(URI)).md5 is None


async def test_stat_missing_raises() -> None:
    gcs, _, bucket, _ = storage_with(MagicMock(spec=SigningCredentials))
    bucket.get_blob.return_value = None
    with pytest.raises(ObjectNotFound):
        await gcs.stat(URI)


async def test_read_returns_bytes() -> None:
    gcs, _, _, blob = storage_with(MagicMock(spec=SigningCredentials))
    blob.download_as_bytes.return_value = b"data"
    assert await gcs.read(URI) == b"data"


async def test_read_missing_raises() -> None:
    gcs, _, _, blob = storage_with(MagicMock(spec=SigningCredentials))
    blob.download_as_bytes.side_effect = gexc.NotFound("gone")
    with pytest.raises(ObjectNotFound):
        await gcs.read(URI)


async def test_delete_missing_is_not_an_error() -> None:
    gcs, _, _, blob = storage_with(MagicMock(spec=SigningCredentials))
    blob.delete.side_effect = gexc.NotFound("gone")
    await gcs.delete(URI)
    blob.delete.assert_called_once_with()


async def test_other_errors_propagate() -> None:
    gcs, _, _, blob = storage_with(MagicMock(spec=SigningCredentials))
    blob.download_as_bytes.side_effect = gexc.Forbidden("no access")
    with pytest.raises(gexc.Forbidden):
        await gcs.read(URI)


# --------------------------------------------------------------------------- real signer


async def test_real_v4_signature_covers_type_and_length() -> None:
    """The real library, offline, with a throwaway key: both headers are signed in."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "project_id": "demo",
            "private_key_id": "k1",
            "private_key": pem,
            "client_email": "signer@demo.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    client = storage.Client(project="demo", credentials=credentials)
    gcs = GcsStorage(BUCKET, client=client, credentials=credentials)
    upload = await gcs.signed_put_url(URI, "application/pdf", 42, timedelta(minutes=15))

    url = urlparse(upload.url)
    query = parse_qs(url.query)
    assert url.netloc == "storage.googleapis.com"
    assert url.path == f"/{BUCKET}/{NAME}"
    assert query["X-Goog-Algorithm"] == ["GOOG4-RSA-SHA256"]
    assert query["X-Goog-Expires"] == ["900"]
    assert query["X-Goog-SignedHeaders"] == ["content-type;host;x-goog-content-length-range"]
    assert query["X-Goog-Credential"][0].startswith("signer@demo.iam.gserviceaccount.com/")
