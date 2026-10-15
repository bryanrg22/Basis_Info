"""Storage protocol, object naming, and shared types (S1_SPEC section 6).

Objects are addressed by ``gs://<bucket>/<name>`` URIs everywhere, including in
:class:`~basis.storage.local.LocalStorage`, so the database stores the same values in
tests and in production.

Uploads never pass through the API: the API hands the browser a :class:`SignedUpload`
(URL plus the headers the browser must send, since they are part of the signature), the
browser PUTs the file, and the API then calls :meth:`Storage.stat` to confirm it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

SCHEME: Final = "gs://"
MAX_SIGNED_URL_EXPIRY: Final = timedelta(days=7)  # GCS V4 maximum
MAX_FILENAME_BYTES: Final = 255
CONTENT_LENGTH_RANGE_HEADER: Final = "x-goog-content-length-range"
# GCS bucket naming: 3-63 chars of lowercase letters, digits, '-', '_' and '.', starting and
# ending with a letter or digit (dotted names up to 222 are not used here).
_BUCKET: Final = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")


class StorageError(RuntimeError):
    """A storage operation failed."""


class ObjectNotFound(StorageError):
    """The object does not exist."""


class UploadRejected(StorageError):
    """A signed upload did not satisfy its signature (used by LocalStorage)."""


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """Size and checksums as GCS reports them: base64 of the big-endian digest.

    ``md5`` is ``None`` for composite objects, which GCS stores without an MD5.
    """

    size: int
    crc32c: str | None
    md5: str | None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class SignedUpload:
    """What the browser needs to upload one object directly to storage."""

    url: str
    expires_at: datetime
    method: str = "PUT"
    headers: dict[str, str] = field(default_factory=dict)


class Storage(Protocol):
    async def signed_put_url(
        self, uri: str, content_type: str, size: int, expires: timedelta
    ) -> SignedUpload:
        """A URL that accepts exactly one PUT of ``size`` bytes of ``content_type``."""
        ...

    async def stat(self, uri: str) -> ObjectStat:
        """Size and checksums; raises :class:`ObjectNotFound`."""
        ...

    async def read(self, uri: str) -> bytes:
        """The object's bytes; raises :class:`ObjectNotFound`."""
        ...

    async def delete(self, uri: str) -> None:
        """Delete the object; deleting a missing object is not an error."""
        ...

    async def put(self, uri: str, data: bytes, content_type: str) -> None:
        """Write ``data`` to ``uri`` from the server side (the worker's exports).

        Overwrites an existing object, so writing the same bytes twice is idempotent.
        """
        ...


def validate_bucket(bucket: str) -> str:
    if not _BUCKET.fullmatch(bucket) or ".." in bucket:
        raise ValueError(f"invalid bucket name: {bucket!r}")
    return bucket


def safe_filename(filename: str) -> str:
    """The last path component of ``filename``, made safe for an object name.

    Both ``/`` and ``\\`` count as separators (browsers on Windows may send either);
    control characters are removed (GCS rejects CR/LF and recommends against the rest);
    the result is NFC-normalized. Empty, ``.`` and ``..`` results, and names over 255
    UTF-8 bytes, raise ``ValueError``.
    """
    normalized = unicodedata.normalize("NFC", filename)
    base = re.split(r"[/\\]", normalized)[-1]
    cleaned = "".join(ch for ch in base if unicodedata.category(ch) != "Cc").strip()
    if cleaned in ("", ".", ".."):
        raise ValueError(f"filename has no usable name: {filename!r}")
    if len(cleaned.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise ValueError(f"filename longer than {MAX_FILENAME_BYTES} bytes")
    return cleaned


def object_name(study_id: UUID, document_id: UUID, filename: str) -> str:
    return f"studies/{study_id}/{document_id}/{safe_filename(filename)}"


def object_uri(bucket: str, study_id: UUID, document_id: UUID, filename: str) -> str:
    """``gs://<bucket>/studies/<study_id>/<document_id>/<filename>``."""
    return f"{SCHEME}{validate_bucket(bucket)}/{object_name(study_id, document_id, filename)}"


def parse_uri(uri: str) -> tuple[str, str]:
    """Split ``gs://bucket/name`` into ``(bucket, name)``; reject anything else.

    Names with empty, ``.`` or ``..`` segments are rejected so no URI can address a
    path outside its bucket in :class:`~basis.storage.local.LocalStorage`.
    """
    if not uri.startswith(SCHEME):
        raise ValueError(f"not a {SCHEME} URI: {uri!r}")
    bucket, sep, name = uri[len(SCHEME) :].partition("/")
    if not sep or not name:
        raise ValueError(f"URI has no object name: {uri!r}")
    validate_bucket(bucket)
    if any(part in ("", ".", "..") for part in name.split("/")) or "\\" in name:
        raise ValueError(f"URI has an unsafe object name: {uri!r}")
    return bucket, name


def check_upload_args(content_type: str, size: int, expires: timedelta) -> None:
    if not content_type or any(ch in content_type for ch in "\r\n"):
        raise ValueError("content_type is required")
    if size <= 0:
        raise ValueError("size must be positive")
    if not timedelta(0) < expires <= MAX_SIGNED_URL_EXPIRY:
        raise ValueError(f"expires must be within (0, {MAX_SIGNED_URL_EXPIRY}]")
