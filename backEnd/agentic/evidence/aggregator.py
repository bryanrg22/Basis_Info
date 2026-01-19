"""
Evidence aggregator for collecting and deduplicating citations.

Phase 6: Provides unified evidence collection across all workflow stages.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .models import EvidenceEntry, EvidencePack, EvidenceSummary

logger = logging.getLogger(__name__)


class EvidenceAggregator:
    """
    Aggregates and deduplicates evidence citations from all workflow stages.

    Usage:
        aggregator = EvidenceAggregator(study_id="study_123")

        # Add citations from different stages
        aggregator.add_citations(
            citations=room_agent_citations,
            stage="room",
            component_id="room_1",
            component_name="Kitchen",
        )

        # Get the organized pack
        pack = aggregator.get_organized_pack()
    """

    def __init__(self, study_id: str):
        """
        Initialize the evidence aggregator.

        Args:
            study_id: Study ID this aggregator is collecting evidence for
        """
        self.study_id = study_id
        self._entries: dict[str, EvidenceEntry] = {}  # signature -> entry
        self._seen_signatures: set[str] = set()
        self._components_seen: set[str] = set()
        self._all_component_ids: set[str] = set()

    def add_citations(
        self,
        citations: list[dict[str, Any]],
        stage: str,
        component_id: str,
        component_name: str,
    ) -> int:
        """
        Add citations from a workflow stage.

        Automatically deduplicates citations based on their signature.

        Args:
            citations: List of citation dicts from agent output
            stage: Workflow stage (e.g., "room", "object", "classification")
            component_id: ID of the component these citations support
            component_name: Name of the component these citations support

        Returns:
            Number of new (non-duplicate) citations added
        """
        self._all_component_ids.add(component_id)
        new_count = 0

        for citation in citations:
            # Handle both dict and Citation model
            if hasattr(citation, "model_dump"):
                citation = citation.model_dump()

            # Skip invalid citations
            if not citation.get("doc_id") or citation.get("page") is None:
                continue

            # Create entry
            entry = EvidenceEntry(
                chunk_id=citation.get("chunk_id"),
                table_id=citation.get("table_id"),
                doc_id=citation["doc_id"],
                page=citation["page"],
                excerpt=citation.get("excerpt", "")[:1000],  # Truncate long excerpts
                stage=stage,
                component_id=component_id,
                component_name=component_name,
            )

            # Check for duplicates
            signature = entry.signature()
            if signature in self._seen_signatures:
                logger.debug(f"Duplicate citation skipped: {signature[:50]}...")
                continue

            # Add entry
            self._entries[entry.id] = entry
            self._seen_signatures.add(signature)
            self._components_seen.add(component_id)
            new_count += 1

        if new_count > 0:
            logger.debug(
                f"Added {new_count} citations for {component_name} "
                f"(stage={stage}, component_id={component_id})"
            )

        return new_count

    def add_entry(self, entry: EvidenceEntry) -> bool:
        """
        Add a single pre-formed evidence entry.

        Args:
            entry: Evidence entry to add

        Returns:
            True if added, False if duplicate
        """
        self._all_component_ids.add(entry.component_id)

        signature = entry.signature()
        if signature in self._seen_signatures:
            return False

        self._entries[entry.id] = entry
        self._seen_signatures.add(signature)
        self._components_seen.add(entry.component_id)
        return True

    def register_component(self, component_id: str) -> None:
        """
        Register a component that should have evidence.

        Call this for all components in the workflow so we can track
        which ones lack supporting evidence.

        Args:
            component_id: Component ID to register
        """
        self._all_component_ids.add(component_id)

    def get_entries(self) -> list[EvidenceEntry]:
        """Get all collected evidence entries."""
        return list(self._entries.values())

    def get_entries_by_stage(self, stage: str) -> list[EvidenceEntry]:
        """Get entries for a specific workflow stage."""
        return [e for e in self._entries.values() if e.stage == stage]

    def get_entries_by_component(self, component_id: str) -> list[EvidenceEntry]:
        """Get entries for a specific component."""
        return [e for e in self._entries.values() if e.component_id == component_id]

    def get_entry_count(self) -> int:
        """Get total number of entries."""
        return len(self._entries)

    def _build_summary(self) -> EvidenceSummary:
        """Build summary statistics for the evidence pack."""
        entries = list(self._entries.values())

        if not entries:
            return EvidenceSummary(
                total_citations=0,
                unique_documents=0,
                stages_covered=[],
                avg_citations_per_component=0.0,
                components_without_evidence=list(self._all_component_ids),
            )

        # Unique documents
        unique_docs = set(e.doc_id for e in entries)

        # Stages covered
        stages = set(e.stage for e in entries)

        # Citations per component
        citations_per_component: dict[str, int] = {}
        for entry in entries:
            citations_per_component[entry.component_id] = (
                citations_per_component.get(entry.component_id, 0) + 1
            )

        # Components without evidence
        components_without = [
            cid
            for cid in self._all_component_ids
            if cid not in self._components_seen
        ]

        # Average citations per component
        avg_citations = (
            sum(citations_per_component.values()) / len(citations_per_component)
            if citations_per_component
            else 0.0
        )

        # Citation density by stage
        stage_component_counts: dict[str, set[str]] = {}
        stage_citation_counts: dict[str, int] = {}
        for entry in entries:
            if entry.stage not in stage_component_counts:
                stage_component_counts[entry.stage] = set()
                stage_citation_counts[entry.stage] = 0
            stage_component_counts[entry.stage].add(entry.component_id)
            stage_citation_counts[entry.stage] += 1

        density_by_stage = {
            stage: (
                stage_citation_counts[stage] / len(components)
                if components
                else 0.0
            )
            for stage, components in stage_component_counts.items()
        }

        return EvidenceSummary(
            total_citations=len(entries),
            unique_documents=len(unique_docs),
            stages_covered=sorted(stages),
            avg_citations_per_component=round(avg_citations, 2),
            components_without_evidence=components_without,
            citation_density_by_stage=density_by_stage,
        )

    def get_organized_pack(self) -> EvidencePack:
        """
        Get the complete organized evidence pack.

        Includes entries organized by stage, component, and document
        along with summary statistics.

        Returns:
            Complete EvidencePack ready for persistence
        """
        entries = list(self._entries.values())

        # Build indexes
        by_stage: dict[str, list[str]] = {}
        by_component: dict[str, list[str]] = {}
        by_document: dict[str, list[str]] = {}

        for entry in entries:
            # By stage
            if entry.stage not in by_stage:
                by_stage[entry.stage] = []
            by_stage[entry.stage].append(entry.id)

            # By component
            if entry.component_id not in by_component:
                by_component[entry.component_id] = []
            by_component[entry.component_id].append(entry.id)

            # By document
            if entry.doc_id not in by_document:
                by_document[entry.doc_id] = []
            by_document[entry.doc_id].append(entry.id)

        # Build summary
        summary = self._build_summary()

        return EvidencePack(
            study_id=self.study_id,
            total_citations=len(entries),
            entries=entries,
            by_stage=by_stage,
            by_component=by_component,
            by_document=by_document,
            summary=summary,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    @classmethod
    def from_evidence_pack(cls, pack: EvidencePack) -> "EvidenceAggregator":
        """
        Create an aggregator from an existing evidence pack.

        Useful for resuming evidence collection after a checkpoint.

        Args:
            pack: Existing evidence pack

        Returns:
            Aggregator with pack's entries loaded
        """
        aggregator = cls(study_id=pack.study_id)

        for entry in pack.entries:
            aggregator.add_entry(entry)

        # Register components without evidence
        for component_id in pack.summary.components_without_evidence:
            aggregator.register_component(component_id)

        return aggregator
