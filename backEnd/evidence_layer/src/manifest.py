"""
Document manifest and registration.

Handles document registration with versioning and metadata
for both reference (IRS, RSMeans) and study (appraisals, invoices) corpora.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class Corpus(str, Enum):
    """Document corpus type."""
    
    REFERENCE = "reference"  # IRS, RSMeans - shared across studies
    STUDY = "study"          # Appraisals, invoices - per study


class DocType(str, Enum):
    """Document type classification."""
    
    # Reference corpus types
    IRS = "irs"              # IRS publications (Pub 946, ATG, etc.)
    RSMEANS = "rsmeans"      # RSMeans cost data
    
    # Study corpus types
    APPRAISAL = "appraisal"
    INVOICE = "invoice"
    SKETCH = "sketch"
    PLAN = "plan"
    OTHER = "other"


class DocumentManifest(BaseModel):
    """
    Manifest entry for a registered document.
    
    Tracks provenance, versioning, and processing status.
    """
    
    doc_id: str = Field(..., description="Unique document identifier")
    corpus: Corpus = Field(..., description="Reference or study corpus")
    doc_type: DocType = Field(..., description="Document classification")
    
    # Source info
    original_filename: str = Field(..., description="Original PDF filename")
    sha256: str = Field(..., description="SHA256 hash of PDF content")
    file_size_bytes: int = Field(..., description="File size in bytes")
    page_count: int = Field(..., description="Number of pages")
    
    # Versioning (for reference corpus)
    version_label: Optional[str] = Field(
        None,
        description="Version label (e.g., 'IRS Pub 946 (2024)')"
    )
    
    # Study scope (for study corpus)
    study_id: Optional[str] = Field(
        None,
        description="Study ID this document belongs to"
    )
    
    # Timestamps
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When document was registered"
    )
    processed_at: Optional[datetime] = Field(
        None,
        description="When processing completed"
    )
    
    # Processing status
    is_processed: bool = Field(
        default=False,
        description="Whether all artifacts have been generated"
    )
    processing_error: Optional[str] = Field(
        None,
        description="Error message if processing failed"
    )


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def generate_doc_id(
    corpus: Corpus,
    doc_type: DocType,
    filename: str,
    version_label: Optional[str] = None,
    study_id: Optional[str] = None,
) -> str:
    """
    Generate a deterministic document ID.
    
    Format:
    - Reference: {DOC_TYPE}_{VERSION_SLUG} (e.g., IRS_PUB946_2024)
    - Study: {STUDY_ID}_{DOC_TYPE}_{FILENAME_SLUG} (e.g., STUDY001_APPRAISAL_123main)
    """
    # Clean filename
    stem = Path(filename).stem
    slug = "".join(c if c.isalnum() else "_" for c in stem).strip("_")[:30]
    
    if corpus == Corpus.REFERENCE:
        if version_label:
            version_slug = "".join(c if c.isalnum() else "_" for c in version_label).strip("_")
            return f"{doc_type.value.upper()}_{version_slug}".upper()
        return f"{doc_type.value.upper()}_{slug}".upper()
    else:
        # Study corpus
        if study_id:
            return f"{study_id}_{doc_type.value.upper()}_{slug}".upper()
        return f"STUDY_{doc_type.value.upper()}_{slug}".upper()


def get_data_dir(
    corpus: Corpus,
    study_id: Optional[str] = None,
    use_gcs: bool = False,
) -> Path:
    """
    Get the data directory for a corpus/study.

    Args:
        corpus: Reference or study corpus
        study_id: Required for study corpus
        use_gcs: If True, returns cache directory for GCS mode

    Returns:
        Path to data directory
    """
    if use_gcs:
        # In GCS mode, return local cache directory
        # Actual downloads handled by GCSIndexLoader
        cache_dir = Path(
            os.getenv("LOCAL_CACHE_DIR", tempfile.gettempdir())
        ) / "basis_indexes"

        if corpus == Corpus.REFERENCE:
            return cache_dir / "reference"
        else:
            if study_id:
                return cache_dir / "studies" / study_id
            raise ValueError("study_id required for study corpus")

    # Local mode - use data directory relative to this file
    base = Path(__file__).parent.parent / "data"

    if corpus == Corpus.REFERENCE:
        return base / "reference"
    else:
        if study_id:
            return base / "studies" / study_id
        raise ValueError("study_id required for study corpus")


def register_document(
    pdf_path: Path,
    corpus: Corpus,
    doc_type: DocType,
    study_id: Optional[str] = None,
    version_label: Optional[str] = None,
) -> DocumentManifest:
    """
    Register a document and prepare for processing.
    
    Creates the manifest entry and copies PDF to raw/ storage.
    Does NOT run the ingestion pipeline.
    
    Args:
        pdf_path: Path to the PDF file
        corpus: reference or study
        doc_type: Document type (irs, rsmeans, appraisal, etc.)
        study_id: Required for study corpus
        version_label: Optional version for reference corpus
        
    Returns:
        DocumentManifest with registration info
    """
    import pdfplumber  # Lazy import
    
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    # Validate corpus/study_id
    if corpus == Corpus.STUDY and not study_id:
        raise ValueError("study_id required for study corpus")
    
    # Get page count
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
    
    # Generate IDs and paths
    doc_id = generate_doc_id(corpus, doc_type, pdf_path.name, version_label, study_id)
    data_dir = get_data_dir(corpus, study_id)
    
    # Ensure directories exist
    for subdir in ["raw", "layout", "retrieval", "structured", "indexes"]:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    
    # Copy PDF to raw storage
    raw_path = data_dir / "raw" / f"{doc_id}.pdf"
    if not raw_path.exists():
        import shutil
        shutil.copy2(pdf_path, raw_path)
    
    # Create manifest
    manifest = DocumentManifest(
        doc_id=doc_id,
        corpus=corpus,
        doc_type=doc_type,
        original_filename=pdf_path.name,
        sha256=compute_sha256(pdf_path),
        file_size_bytes=pdf_path.stat().st_size,
        page_count=page_count,
        version_label=version_label,
        study_id=study_id,
    )
    
    # Save manifest
    manifest_path = data_dir / "manifest.json"
    manifests = []
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifests = json.load(f)
    
    # Update or append
    manifests = [m for m in manifests if m["doc_id"] != doc_id]
    manifests.append(manifest.model_dump(mode="json"))
    
    with open(manifest_path, "w") as f:
        json.dump(manifests, f, indent=2, default=str)
    
    return manifest


def load_manifest(doc_id: str, corpus: Corpus, study_id: Optional[str] = None) -> Optional[DocumentManifest]:
    """Load a document manifest by ID."""
    data_dir = get_data_dir(corpus, study_id)
    manifest_path = data_dir / "manifest.json"
    
    if not manifest_path.exists():
        return None
    
    with open(manifest_path) as f:
        manifests = json.load(f)
    
    for m in manifests:
        if m["doc_id"] == doc_id:
            return DocumentManifest.model_validate(m)
    
    return None

