"""Google Cloud Storage backend.

Signed URLs are V4, method ``PUT``, with the content type and an exact
``x-goog-content-length-range`` of ``size,size`` among the signed headers, so the URL
accepts only the declared type and size. The browser must send both headers; they are
returned in :class:`~basis.storage.base.SignedUpload.headers`.

Signing credentials: a service-account key (or impersonated credentials) can sign
locally. Cloud Run's metadata-server credentials cannot; for those the token is
refreshed and the URL is signed through the IAM ``signBlob`` API by passing
``service_account_email`` and ``access_token`` (Google's documented pattern; the
runtime service account needs ``roles/iam.serviceAccountTokenCreator`` on itself).

The client library is synchronous; every call runs in a worker thread.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio.to_thread
import google.auth
import google.auth.credentials
import google.auth.transport.requests
from google.api_core import exceptions as gexc
from google.cloud import storage

from basis.storage.base import (
    CONTENT_LENGTH_RANGE_HEADER,
    ObjectNotFound,
    ObjectStat,
    SignedUpload,
    check_upload_args,
    parse_uri,
    validate_bucket,
)

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _in_thread[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


class GcsStorage:
    """Objects in one bucket; URIs naming any other bucket are rejected."""

    def __init__(
        self,
        bucket: str,
        *,
        client: storage.Client | None = None,
        credentials: google.auth.credentials.Credentials | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._bucket_name = validate_bucket(bucket)
        self._client = client
        self._credentials = credentials
        self._clock = clock

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    def _get_client(self) -> storage.Client:
        if self._client is None:
            if self._credentials is None:
                self._credentials, _ = google.auth.default(scopes=_SCOPES)
            self._client = storage.Client(credentials=self._credentials)
        return self._client

    def _signing_credentials(self) -> Any:
        if self._credentials is not None:
            return self._credentials
        # The client's own credentials; google-cloud-core keeps them on ``_credentials``.
        return getattr(self._get_client(), "_credentials", None)

    def _blob(self, uri: str) -> storage.Blob:
        bucket, name = parse_uri(uri)
        if bucket != self._bucket_name:
            raise ValueError(f"URI is not in bucket {self._bucket_name!r}: {uri!r}")
        return self._get_client().bucket(bucket).blob(name)

    def _signing_kwargs(self) -> dict[str, Any]:
        """Extra ``generate_signed_url`` arguments for credentials that cannot sign."""
        credentials = self._signing_credentials()
        if credentials is None or isinstance(credentials, google.auth.credentials.Signing):
            return {}
        if not credentials.valid or not credentials.token:
            credentials.refresh(google.auth.transport.requests.Request())
        return {
            "service_account_email": credentials.service_account_email,
            "access_token": credentials.token,
        }

    def _sign_put(self, uri: str, content_type: str, size: int, expires: timedelta) -> str:
        blob = self._blob(uri)
        url: str = blob.generate_signed_url(
            version="v4",
            method="PUT",
            expiration=expires,
            content_type=content_type,
            headers={CONTENT_LENGTH_RANGE_HEADER: f"{size},{size}"},
            **self._signing_kwargs(),
        )
        return url

    async def signed_put_url(
        self, uri: str, content_type: str, size: int, expires: timedelta
    ) -> SignedUpload:
        check_upload_args(content_type, size, expires)
        issued_at = self._clock()
        url = await _in_thread(self._sign_put, uri, content_type, size, expires)
        return SignedUpload(
            url=url,
            expires_at=issued_at + expires,
            headers={"Content-Type": content_type, CONTENT_LENGTH_RANGE_HEADER: f"{size},{size}"},
        )

    def _stat(self, uri: str) -> ObjectStat:
        blob = self._blob(uri)
        loaded = blob.bucket.get_blob(blob.name)
        if loaded is None:
            raise ObjectNotFound(uri)
        return ObjectStat(
            size=int(loaded.size or 0),
            crc32c=loaded.crc32c,
            md5=loaded.md5_hash,
            content_type=loaded.content_type,
        )

    async def stat(self, uri: str) -> ObjectStat:
        return await _in_thread(self._stat, uri)

    def _read(self, uri: str) -> bytes:
        try:
            data: bytes = self._blob(uri).download_as_bytes()
        except gexc.NotFound as exc:
            raise ObjectNotFound(uri) from exc
        return data

    async def read(self, uri: str) -> bytes:
        return await _in_thread(self._read, uri)

    def _delete(self, uri: str) -> None:
        try:
            self._blob(uri).delete()
        except gexc.NotFound:
            pass

    async def delete(self, uri: str) -> None:
        await _in_thread(self._delete, uri)
