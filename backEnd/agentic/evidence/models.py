"""
Evidence models for unified citation tracking.

Phase 6: Provides structured models for evidence entries, packs, and summaries.
"""

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field


class EvidenceEntry(BaseModel):
    """
    A single evidence citation from any workflow stage.

    Tracks the source document, page, excerpt, and which component
    and stage the evidence was collected for.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique evidence entry ID",
    )
    chunk_id: Optional[str] = Field(
        default=None,
        description="Chunk ID from search result",
    )
    table_id: Optional[str] = Field(
        default=None,
        description="Table ID if citing a table",
    )
    doc_id: str = Field(
        ...,
        description="Source document ID",
    )
    page: int = Field(
        ...,
        description="Page number (1-indexed)",
    )
    excerpt: str = Field(
        ...,
        max_length=1000,
        description="Relevant excerpt from source",
    )
    stage: str = Field(
        ...,
        description="Workflow stage where evidence was collected",
    )
    component_id: str = Field(
        ...,
        description="ID of the component this evidence supports",
    )
    component_name: str = Field(
        ...,
        description="Name of the component this evidence supports",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the evidence was collected",
    )

    def to_reference(self) -> str:
        """Format citation as a reference string."""
        ref = f"{self.doc_id}, p.{self.page}"
        if self.table_id:
            ref += f", {self.table_id}"
        return ref

    def signature(self) -> str:
        """
        Generate a signature for deduplication.

        Uses chunk_id if available, otherwise doc_id + page + excerpt hash.
        """
        if self.chunk_id:
            return f"{self.chunk_id}:{self.component_id}"
        return f"{self.doc_id}:{self.page}:{hash(self.excerpt[:100])}:{self.component_id}"

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }


class EvidenceSummary(BaseModel):
    """
    Summary statistics for an evidence pack.

    Provides overview metrics for IRS audit readiness reporting.
    """

    total_citations: int = Field(
        default=0,
        description="Total number of citations in the pack",
    )
    unique_documents: int = Field(
        default=0,
        description="Number of unique source documents cited",
    )
    stages_covered: list[str] = Field(
        default_factory=list,
        description="List of workflow stages with evidence",
    )
    avg_citations_per_component: float = Field(
        default=0.0,
        description="Average citations per component",
    )
    components_without_evidence: list[str] = Field(
        default_factory=list,
        description="Components that have no supporting evidence",
    )
    citation_density_by_stage: dict[str, float] = Field(
        default_factory=dict,
        description="Average citations per component by stage",
    )


class EvidencePack(BaseModel):
    """
    Unified evidence pack for a complete study.

    Organizes all citations by stage, component, and document
    for easy querying and IRS audit support.
    """

    study_id: str = Field(
        ...,
        description="Study this evidence pack belongs to",
    )
    total_citations: int = Field(
        default=0,
        description="Total number of citations",
    )
    entries: list[EvidenceEntry] = Field(
        default_factory=list,
        description="All evidence entries",
    )
    by_stage: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Evidence entry IDs organized by workflow stage",
    )
    by_component: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Evidence entry IDs organized by component ID",
    )
    by_document: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Evidence entry IDs organized by source document",
    )
    summary: EvidenceSummary = Field(
        default_factory=EvidenceSummary,
        description="Summary statistics",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the pack was created",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the pack was last updated",
    )

    def get_entries_by_stage(self, stage: str) -> list[EvidenceEntry]:
        """Get all entries for a specific workflow stage."""
        entry_ids = self.by_stage.get(stage, [])
        id_to_entry = {e.id: e for e in self.entries}
        return [id_to_entry[eid] for eid in entry_ids if eid in id_to_entry]

    def get_entries_by_component(self, component_id: str) -> list[EvidenceEntry]:
        """Get all entries for a specific component."""
        entry_ids = self.by_component.get(component_id, [])
        id_to_entry = {e.id: e for e in self.entries}
        return [id_to_entry[eid] for eid in entry_ids if eid in id_to_entry]

    def get_entries_by_document(self, doc_id: str) -> list[EvidenceEntry]:
        """Get all entries from a specific document."""
        entry_ids = self.by_document.get(doc_id, [])
        id_to_entry = {e.id: e for e in self.entries}
        return [id_to_entry[eid] for eid in entry_ids if eid in id_to_entry]

    def to_firestore_dict(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return {
            "study_id": self.study_id,
            "total_citations": self.total_citations,
            "entries": [e.model_dump() for e in self.entries],
            "by_stage": self.by_stage,
            "by_component": self.by_component,
            "by_document": self.by_document,
            "summary": self.summary.model_dump(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "EvidencePack":
        """Create EvidencePack from Firestore document."""
        entries = [
            EvidenceEntry(**e)
            for e in data.get("entries", [])
        ]
        summary = EvidenceSummary(**data.get("summary", {}))

        return cls(
            study_id=data["study_id"],
            total_citations=data.get("total_citations", 0),
            entries=entries,
            by_stage=data.get("by_stage", {}),
            by_component=data.get("by_component", {}),
            by_document=data.get("by_document", {}),
            summary=summary,
            created_at=data.get("created_at", datetime.now(timezone.utc)),
            updated_at=data.get("updated_at", datetime.now(timezone.utc)),
        )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }
