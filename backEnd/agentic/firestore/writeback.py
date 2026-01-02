"""
Firestore writeback with evidence backing.

All agent outputs are written with:
1. Structured result data
2. Evidence citations (chunk_ids, table_ids, pages)
3. Confidence scores
4. Review flags
"""

from datetime import datetime
from typing import Any, Optional

from firebase_admin import firestore
from pydantic import BaseModel, Field

from .client import FirestoreClient
from ..agents.base_agent import Citation


class EvidenceBackedUpdate(BaseModel):
    """Wrapper for any update with evidence backing."""

    data: dict[str, Any] = Field(..., description="The actual data to write")
    citations: list[Citation] = Field(
        default_factory=list, description="Evidence citations"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence score"
    )
    needs_review: bool = Field(default=False)
    review_reason: Optional[str] = None
    agent_version: str = Field(default="1.0.0")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FirestoreWriteback:
    """
    Handles evidence-backed writes to Firestore.

    Ensures all agent outputs include proper provenance and review flags.
    """

    def __init__(self):
        self._client: Optional[FirestoreClient] = None

    @property
    def client(self) -> FirestoreClient:
        """Lazy-load Firestore client."""
        if self._client is None:
            self._client = FirestoreClient()
        return self._client

    def update_study_field(
        self,
        study_id: str,
        field: str,
        update: EvidenceBackedUpdate,
    ) -> None:
        """
        Update a study field with evidence backing.

        The update is wrapped with metadata for auditability.

        Args:
            study_id: Study document ID
            field: Field name to update
            update: Evidence-backed update data
        """
        # Build update payload
        payload = {
            field: update.data,
            f"{field}_metadata": {
                "citations": [c.model_dump() for c in update.citations],
                "confidence": update.confidence,
                "needs_review": update.needs_review,
                "review_reason": update.review_reason,
                "agent_version": update.agent_version,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        }

        self.client.update_study(study_id, payload)

    def update_objects_with_classifications(
        self,
        study_id: str,
        classified_objects: list[dict[str, Any]],
    ) -> None:
        """
        Batch update objects with asset classifications.

        Each object gets:
        - asset_classification: The MACRS bucket, life, section, etc.
        - citations: Evidence supporting the classification
        - needs_review: True if no evidence found

        Args:
            study_id: Study document ID
            classified_objects: List of objects with classifications
        """
        objects_with_evidence = []
        needs_review_count = 0

        for obj in classified_objects:
            obj_data = {
                **obj.get("original", {}),
                "asset_classification": obj.get("classification"),
                "citations": obj.get("citations", []),
                "classification_confidence": obj.get("confidence", 0.0),
            }

            if obj.get("needs_review"):
                obj_data["needs_review"] = True
                obj_data["review_reason"] = obj.get("review_reason")
                needs_review_count += 1
            else:
                obj_data["needs_review"] = False

            objects_with_evidence.append(obj_data)

        self.client.update_study(study_id, {
            "objects": objects_with_evidence,
            "classification_summary": {
                "total": len(objects_with_evidence),
                "needs_review": needs_review_count,
                "completed_at": firestore.SERVER_TIMESTAMP,
            },
        })

    def update_rooms(
        self,
        study_id: str,
        rooms: list[dict[str, Any]],
    ) -> None:
        """
        Update study rooms after classification.

        Args:
            study_id: Study document ID
            rooms: List of room classification results
        """
        needs_review_count = sum(1 for r in rooms if r.get("needs_review"))

        self.client.update_study(study_id, {
            "rooms": rooms,
            "rooms_summary": {
                "total": len(rooms),
                "needs_review": needs_review_count,
                "completed_at": firestore.SERVER_TIMESTAMP,
            },
        })

    def update_study_with_costs(
        self,
        study_id: str,
        cost_estimates: list[dict[str, Any]],
        cost_summary: dict[str, Any],
    ) -> None:
        """
        Update study with cost estimates and summary.

        Args:
            study_id: Study document ID
            cost_estimates: List of individual cost estimates with RSMeans citations
            cost_summary: Aggregated cost summary with totals
        """
        estimates_with_evidence = []
        needs_review_count = 0

        for estimate in cost_estimates:
            estimate_data = {
                "component_name": estimate.get("component_name"),
                "estimate": estimate.get("estimate"),
                "citations": estimate.get("citations", []),
                "confidence": estimate.get("confidence", 0.0),
            }

            if estimate.get("needs_review"):
                estimate_data["needs_review"] = True
                needs_review_count += 1
            else:
                estimate_data["needs_review"] = False

            estimates_with_evidence.append(estimate_data)

        self.client.update_study(study_id, {
            "cost_estimates": estimates_with_evidence,
            "cost_summary": {
                **cost_summary,
                "needs_review_count": needs_review_count,
                "completed_at": firestore.SERVER_TIMESTAMP,
            },
        })

    def advance_workflow(
        self,
        study_id: str,
        new_status: str,
    ) -> None:
        """
        Advance workflow status after stage completion.

        Args:
            study_id: Study document ID
            new_status: New workflow status
        """
        self.client.update_workflow_status(study_id, new_status)

    def mark_stage_complete(
        self,
        study_id: str,
        stage: str,
        next_status: str,
        summary: dict[str, Any],
    ) -> None:
        """
        Mark a stage as complete and advance workflow.

        Args:
            study_id: Study document ID
            stage: Completed stage name
            next_status: Next workflow status
            summary: Stage completion summary
        """
        self.client.update_study(study_id, {
            f"{stage}_completed_at": firestore.SERVER_TIMESTAMP,
            f"{stage}_summary": summary,
            "workflowStatus": next_status,
        })
