"""
GCS-based index loading with local caching.

Downloads indexes from GCS on first access, caches locally for performance.
Supports both BM25 (.bm25.pkl) and FAISS (.faiss, .meta.json) indexes.
"""

import os
import tempfile
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound


class GCSIndexLoader:
    """
    Loads indexes from Google Cloud Storage with local caching.

    Usage:
        loader = GCSIndexLoader("my-bucket", prefix="reference/indexes")
        bm25_path = loader.get_index_path("IRS_PUB_946", "bm25", ".bm25.pkl")
        faiss_path = loader.get_index_path("IRS_PUB_946", "vector", ".faiss")
    """

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "indexes",
        cache_dir: Optional[str] = None,
    ):
        """
        Initialize GCS loader.

        Args:
            bucket_name: GCS bucket name
            prefix: Path prefix within bucket (e.g., "reference/indexes")
            cache_dir: Local cache directory. Defaults to temp directory.
        """
        self.bucket_name = bucket_name
        self.prefix = prefix
        self._client: Optional[storage.Client] = None
        self._cache_dir = Path(
            cache_dir or os.getenv("LOCAL_CACHE_DIR", tempfile.gettempdir())
        ) / "basis_indexes"

    @property
    def client(self) -> storage.Client:
        """Lazy-load GCS client."""
        if self._client is None:
            self._client = storage.Client()
        return self._client

    @property
    def cache_dir(self) -> Path:
        """Get cache directory, creating if needed."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir

    def get_index_path(
        self,
        doc_id: str,
        index_type: str,
        extension: str,
    ) -> Path:
        """
        Get local path to index, downloading from GCS if needed.

        Args:
            doc_id: Document ID (e.g., "IRS_IRS_PUB_946__2024")
            index_type: Index type ("bm25" or "vector")
            extension: File extension (e.g., ".bm25.pkl", ".faiss", ".meta.json")

        Returns:
            Local path to cached index file

        Raises:
            FileNotFoundError: If file doesn't exist in GCS
        """
        local_path = self.cache_dir / index_type / f"{doc_id}{extension}"

        if local_path.exists():
            return local_path

        # Download from GCS
        gcs_path = f"{self.prefix}/{index_type}/{doc_id}{extension}"
        self._download_blob(gcs_path, local_path)

        return local_path

    def get_bm25_index_path(self, doc_id: str) -> Path:
        """Get path to BM25 index, downloading if needed."""
        return self.get_index_path(doc_id, "bm25", ".bm25.pkl")

    def get_faiss_index_paths(self, doc_id: str) -> tuple[Path, Path]:
        """
        Get paths to FAISS index and metadata, downloading if needed.

        Returns:
            Tuple of (faiss_path, meta_path)
        """
        faiss_path = self.get_index_path(doc_id, "vector", ".faiss")
        meta_path = self.get_index_path(doc_id, "vector", ".meta.json")
        return faiss_path, meta_path

    def _download_blob(self, gcs_path: str, local_path: Path) -> None:
        """
        Download a blob from GCS to local path.

        Args:
            gcs_path: Path within the bucket
            local_path: Local destination path
        """
        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(gcs_path)
            blob.download_to_filename(str(local_path))
        except NotFound:
            raise FileNotFoundError(
                f"Index not found in GCS: gs://{self.bucket_name}/{gcs_path}"
            )

    def clear_cache(self, doc_id: Optional[str] = None) -> None:
        """
        Clear cached indexes.

        Args:
            doc_id: Specific document to clear. If None, clears all.
        """
        import shutil

        if doc_id:
            # Clear specific document
            for index_type in ["bm25", "vector"]:
                type_dir = self.cache_dir / index_type
                if type_dir.exists():
                    for f in type_dir.glob(f"{doc_id}.*"):
                        f.unlink()
        else:
            # Clear entire cache
            if self._cache_dir.exists():
                shutil.rmtree(self._cache_dir)

    def list_available_indexes(self) -> list[str]:
        """
        List document IDs with indexes in GCS.

        Returns:
            List of document IDs that have indexes available
        """
        bucket = self.client.bucket(self.bucket_name)
        blobs = bucket.list_blobs(prefix=f"{self.prefix}/bm25/")

        doc_ids = set()
        for blob in blobs:
            # Extract doc_id from path like "indexes/bm25/DOC_ID.bm25.pkl"
            name = blob.name.split("/")[-1]
            if name.endswith(".bm25.pkl"):
                doc_id = name.replace(".bm25.pkl", "")
                doc_ids.add(doc_id)

        return sorted(doc_ids)
