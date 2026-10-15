"""Filesystem-backed :class:`~basis.storage.base.Storage` for tests and local runs.

``gs://<bucket>/<name>`` maps to ``<root>/<bucket>/<name>``. Signed uploads are
simulated: :meth:`LocalStorage.signed_put_url` returns an opaque ``local-upload://``
URL, and :meth:`LocalStorage.put_signed` plays the browser's PUT, enforcing what GCS
enforces for a V4 signed URL with a content-length range (expiry, exact size, the signed
``Content-Type``). :meth:`LocalStorage.write` puts bytes directly.

Checksums are computed the way GCS reports them (base64 CRC32C and MD5), so code that
compares them behaves the same against both backends. File I/O is synchronous; this
backend is for tests and small local files only.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import google_crc32c

from basis.storage.base import (
    CONTENT_LENGTH_RANGE_HEADER,
    ObjectNotFound,
    ObjectStat,
    SignedUpload,
    UploadRejected,
    check_upload_args,
    parse_uri,
)

LOCAL_UPLOAD_SCHEME: Final = "local-upload://"


def crc32c_b64(data: bytes) -> str:
    digest: bytes = google_crc32c.Checksum(data).digest()  # type: ignore[no-untyped-call]
    return base64.b64encode(digest).decode("ascii")


def md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode("ascii")


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _PendingUpload:
    uri: str
    content_type: str
    size: int
    expires_at: datetime


class LocalStorage:
    def __init__(self, root_dir: str | Path, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._root = Path(root_dir).resolve()
        self._clock = clock
        self._uploads: dict[str, _PendingUpload] = {}
        self._content_types: dict[str, str] = {}

    def path_for(self, uri: str) -> Path:
        bucket, name = parse_uri(uri)
        path = (self._root / bucket / name).resolve()
        if not path.is_relative_to(self._root):  # parse_uri already prevents this
            raise ValueError(f"URI escapes the storage root: {uri!r}")
        return path

    # ------------------------------------------------------------------ Storage

    async def signed_put_url(
        self, uri: str, content_type: str, size: int, expires: timedelta
    ) -> SignedUpload:
        check_upload_args(content_type, size, expires)
        self.path_for(uri)
        token = secrets.token_urlsafe(24)
        expires_at = self._clock() + expires
        self._uploads[token] = _PendingUpload(uri, content_type, size, expires_at)
        return SignedUpload(
            url=f"{LOCAL_UPLOAD_SCHEME}{token}",
            expires_at=expires_at,
            headers={"Content-Type": content_type, CONTENT_LENGTH_RANGE_HEADER: f"{size},{size}"},
        )

    async def stat(self, uri: str) -> ObjectStat:
        data = self._read(uri)
        return ObjectStat(
            size=len(data),
            crc32c=crc32c_b64(data),
            md5=md5_b64(data),
            content_type=self._content_types.get(uri),
        )

    async def read(self, uri: str) -> bytes:
        return self._read(uri)

    async def delete(self, uri: str) -> None:
        self.path_for(uri).unlink(missing_ok=True)
        self._content_types.pop(uri, None)

    # ------------------------------------------------------------------ test helpers

    def write(self, uri: str, data: bytes, content_type: str | None = None) -> None:
        """Store ``data`` at ``uri`` directly, bypassing signed-URL checks."""
        path = self.path_for(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if content_type is None:
            self._content_types.pop(uri, None)
        else:
            self._content_types[uri] = content_type

    def put_signed(self, url: str, data: bytes, headers: Mapping[str, str]) -> str:
        """Simulate the browser's PUT to a signed URL; returns the object URI.

        Raises :class:`UploadRejected` where GCS would answer 400/403: unknown or
        expired URL, a ``Content-Type`` or content-length-range header that differs from
        the signed one, or a body whose size is outside the signed range.
        """
        if not url.startswith(LOCAL_UPLOAD_SCHEME):
            raise UploadRejected("not a local signed URL")
        upload = self._uploads.get(url[len(LOCAL_UPLOAD_SCHEME) :])
        if upload is None:
            raise UploadRejected("unknown signed URL")
        if self._clock() >= upload.expires_at:
            raise UploadRejected("signed URL expired")
        lowered = {k.lower(): v for k, v in headers.items()}
        if lowered.get("content-type") != upload.content_type:
            raise UploadRejected("Content-Type does not match the signed value")
        if lowered.get(CONTENT_LENGTH_RANGE_HEADER) != f"{upload.size},{upload.size}":
            raise UploadRejected("content-length range does not match the signed value")
        if len(data) != upload.size:
            raise UploadRejected("body size outside the signed content-length range")
        self.write(upload.uri, data, upload.content_type)
        return upload.uri

    def _read(self, uri: str) -> bytes:
        path = self.path_for(uri)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise ObjectNotFound(uri) from exc
