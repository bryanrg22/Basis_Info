"""Object storage for uploaded documents and photos (S1_SPEC section 6)."""

from basis.storage.base import (
    ObjectNotFound,
    ObjectStat,
    SignedUpload,
    Storage,
    StorageError,
    UploadRejected,
    object_uri,
    parse_uri,
    safe_filename,
)
from basis.storage.gcs import GcsStorage
from basis.storage.local import LocalStorage

__all__ = [
    "GcsStorage",
    "LocalStorage",
    "ObjectNotFound",
    "ObjectStat",
    "SignedUpload",
    "Storage",
    "StorageError",
    "UploadRejected",
    "object_uri",
    "parse_uri",
    "safe_filename",
]
