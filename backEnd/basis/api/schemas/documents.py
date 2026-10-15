"""Document upload request and response bodies (S1_SPEC sections 5 and 6).

Limits: at most :data:`MAX_UPLOAD_BYTES` per file, and only the media types allowed for
the document's kind (appraisal: PDF; photo: JPEG, PNG, HEIC, HEIF, WebP; other: any of
those plus plain text and CSV). The request body is a union discriminated by ``kind``, so the
OpenAPI document states exactly which ``content_type`` each kind accepts.

The size bound also caps memory: the completion check reads the object once to verify
its sha256 (GCS does not compute sha256).
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import ConfigDict, Field, field_validator

from basis.api.schemas.common import JSON_NUMBER, RequestModel, ResponseModel
from basis.db.enums import DocumentKind
from basis.storage import safe_filename
from basis.storage.base import MAX_FILENAME_BYTES

MAX_UPLOAD_BYTES: Final = 50 * 1024 * 1024
# A file name, not a path: no separators and no control characters (C0, DEL, C1), and at
# least one character that is not a dot or whitespace (so "", ".", ".." are impossible).
_NAME_CHAR: Final = r"[^\x00-\x1f\x7f-\x9f/\\]"
_NAME_KEY_CHAR: Final = r"[^\x00-\x1f\x7f-\x9f/\\.\s]"
FILENAME_PATTERN: Final = rf"^{_NAME_CHAR}*{_NAME_KEY_CHAR}{_NAME_CHAR}*$"
MAX_FILENAME_CHARS: Final = 255
_EXTENSION_KEEP: Final = 16

PdfType = Literal["application/pdf"]
ImageType = Literal["image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"]
TextType = Literal["text/plain", "text/csv"]


def fit_filename(value: str) -> str:
    """NFC, then shorten to :data:`MAX_FILENAME_BYTES` UTF-8 bytes keeping a short
    extension, then :func:`basis.storage.safe_filename`."""
    name = unicodedata.normalize("NFC", value).strip()
    if len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem or len(ext) > _EXTENSION_KEEP:
            stem, dot, ext = name, "", ""
        suffix = f"{dot}{ext}".encode()
        budget = MAX_FILENAME_BYTES - len(suffix)
        stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore")
        name = f"{stem}{dot}{ext}"
    return safe_filename(name)


# Low-entropy placeholder (a real digest would trip secret scanners in the OpenAPI file).
_EXAMPLE_SHA256: Final = "3a7b" * 16


class _UploadBase(RequestModel):
    filename: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_FILENAME_CHARS,
            pattern=FILENAME_PATTERN,
            description="File name (not a path). Stored NFC-normalized, trimmed, and "
            f"shortened to {MAX_FILENAME_BYTES} UTF-8 bytes if longer.",
        ),
    ]
    bytes: Annotated[
        int, Field(ge=1, le=MAX_UPLOAD_BYTES, description="Exact size in bytes."), JSON_NUMBER
    ]
    sha256: Annotated[
        str,
        Field(
            min_length=64,
            max_length=64,
            pattern=r"^[0-9a-fA-F]{64}$",
            description="Hex SHA-256 of the file's bytes (stored lowercase).",
        ),
    ]

    @field_validator("filename")
    @classmethod
    def _fit_filename(cls, value: str) -> str:
        return fit_filename(value)

    @field_validator("sha256")
    @classmethod
    def _lower_sha256(cls, value: str) -> str:
        return value.lower()


class AppraisalUpload(_UploadBase):
    model_config = ConfigDict(
        title="AppraisalUpload",
        json_schema_extra={
            "examples": [
                {
                    "kind": "appraisal",
                    "filename": "appraisal.pdf",
                    "content_type": "application/pdf",
                    "bytes": 2483021,
                    "sha256": _EXAMPLE_SHA256,
                }
            ]
        },
    )

    kind: Literal["appraisal"]
    content_type: PdfType


class PhotoUpload(_UploadBase):
    model_config = ConfigDict(title="PhotoUpload")

    kind: Literal["photo"]
    content_type: ImageType


class OtherUpload(_UploadBase):
    model_config = ConfigDict(title="OtherUpload")

    kind: Literal["other"]
    content_type: PdfType | ImageType | TextType


UploadRequest = Annotated[AppraisalUpload | PhotoUpload | OtherUpload, Field(discriminator="kind")]


class DocumentOut(ResponseModel):
    id: uuid.UUID
    study_id: uuid.UUID
    kind: DocumentKind
    filename: str
    content_type: str
    bytes: int
    sha256: str
    uploaded_at: datetime | None = Field(description="Null until the upload is confirmed.")
    created_at: datetime


class SignedUploadOut(ResponseModel):
    url: str = Field(description="Send the file here with `method` and exactly `headers`.")
    method: str
    headers: dict[str, str]
    expires_at: datetime


class UploadTicket(ResponseModel):
    """201: the document is registered; upload the bytes, then call `.../complete`."""

    document: DocumentOut
    upload: SignedUploadOut
