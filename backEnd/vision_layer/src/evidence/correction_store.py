"""Firestore storage for engineer corrections on VisionArtifacts.

Captures every edit an engineer makes to an artifact, enabling:
1. Tracking correction patterns for model improvement
2. Audit trail of all changes
3. Fine-tuning data collection
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .artifact_store import _get_study_ref, get_artifact_by_id, update_artifact

logger = logging.getLogger(__name__)


class Correction(BaseModel):
    """A single correction made by an engineer."""

    correction_id: str = Field(
        default_factory=lambda: f"corr_{uuid4().hex[:12]}",
        description="Unique correction identifier",
    )
    artifact_id: str = Field(..., description="The artifact that was corrected")
    field: str = Field(
        ..., description="Field that was corrected (e.g., 'component_type', 'material')"
    )
    old_value: Any = Field(..., description="Original value before correction")
    new_value: Any = Field(..., description="New value after correction")
    corrected_by: str = Field(..., description="Engineer ID or email")
    corrected_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp of correction",
    )
    notes: Optional[str] = Field(
        default=None, description="Optional notes explaining the correction"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dictionary."""
        return {
            "correction_id": self.correction_id,
            "artifact_id": self.artifact_id,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "corrected_by": self.corrected_by,
            "corrected_at": self.corrected_at.isoformat(),
            "notes": self.notes,
        }


def save_correction(
    study_id: str,
    correction: Correction,
    apply_to_artifact: bool = True,
) -> str:
    """Save an engineer correction.

    Args:
        study_id: Study document ID.
        correction: Correction object to save.
        apply_to_artifact: If True, also update the artifact with the new value.

    Returns:
        The correction_id of the saved correction.
    """
    study_ref = _get_study_ref(study_id)
    doc = study_ref.get()

    # Get existing corrections or initialize empty list
    existing = []
    if doc.exists:
        existing = doc.to_dict().get("vision_corrections", [])

    # Add new correction
    existing.append(correction.to_dict())
    study_ref.update({"vision_corrections": existing})

    logger.info(
        f"Saved correction {correction.correction_id} for artifact "
        f"{correction.artifact_id} in study {study_id}"
    )

    # Optionally apply correction to the artifact
    if apply_to_artifact:
        _apply_correction_to_artifact(study_id, correction)

    return correction.correction_id


def _apply_correction_to_artifact(
    study_id: str,
    correction: Correction,
) -> bool:
    """Apply a correction to the corresponding artifact.

    Handles nested fields like "classification.material".
    """
    artifact = get_artifact_by_id(study_id, correction.artifact_id)
    if not artifact:
        logger.warning(
            f"Artifact {correction.artifact_id} not found for correction"
        )
        return False

    # Handle nested fields (e.g., "classification.material")
    if "." in correction.field:
        parts = correction.field.split(".")
        if parts[0] == "classification" and len(parts) == 2:
            classification = artifact.get("classification", {})
            classification[parts[1]] = correction.new_value
            updates = {
                "classification": classification,
                "corrected": True,
                "correction_id": correction.correction_id,
            }
        else:
            # Generic nested update - just update top level for now
            updates = {
                correction.field: correction.new_value,
                "corrected": True,
                "correction_id": correction.correction_id,
            }
    else:
        updates = {
            correction.field: correction.new_value,
            "corrected": True,
            "correction_id": correction.correction_id,
        }

    return update_artifact(study_id, correction.artifact_id, updates)


def get_corrections(study_id: str) -> List[Dict[str, Any]]:
    """Get all corrections for a study.

    Args:
        study_id: Study document ID.

    Returns:
        List of correction dictionaries.
    """
    study_ref = _get_study_ref(study_id)
    doc = study_ref.get()

    if not doc.exists:
        return []

    return doc.to_dict().get("vision_corrections", [])


def get_corrections_for_artifact(
    study_id: str,
    artifact_id: str,
) -> List[Dict[str, Any]]:
    """Get all corrections for a specific artifact.

    Args:
        study_id: Study document ID.
        artifact_id: Artifact to get corrections for.

    Returns:
        List of correction dictionaries for that artifact.
    """
    corrections = get_corrections(study_id)
    return [c for c in corrections if c.get("artifact_id") == artifact_id]


def get_corrections_by_engineer(
    study_id: str,
    engineer_id: str,
) -> List[Dict[str, Any]]:
    """Get all corrections made by a specific engineer.

    Args:
        study_id: Study document ID.
        engineer_id: Engineer ID or email.

    Returns:
        List of correction dictionaries by that engineer.
    """
    corrections = get_corrections(study_id)
    return [c for c in corrections if c.get("corrected_by") == engineer_id]


def get_correction_stats(study_id: str) -> Dict[str, Any]:
    """Get statistics about corrections for a study.

    Useful for understanding common correction patterns.

    Returns:
        Dictionary with correction statistics.
    """
    corrections = get_corrections(study_id)

    if not corrections:
        return {
            "total_corrections": 0,
            "corrections_by_field": {},
            "corrections_by_engineer": {},
            "most_corrected_artifacts": [],
        }

    # Count by field
    by_field: Dict[str, int] = {}
    for c in corrections:
        field = c.get("field", "unknown")
        by_field[field] = by_field.get(field, 0) + 1

    # Count by engineer
    by_engineer: Dict[str, int] = {}
    for c in corrections:
        engineer = c.get("corrected_by", "unknown")
        by_engineer[engineer] = by_engineer.get(engineer, 0) + 1

    # Count by artifact
    by_artifact: Dict[str, int] = {}
    for c in corrections:
        artifact = c.get("artifact_id", "unknown")
        by_artifact[artifact] = by_artifact.get(artifact, 0) + 1

    # Sort artifacts by correction count
    most_corrected = sorted(
        by_artifact.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    return {
        "total_corrections": len(corrections),
        "corrections_by_field": by_field,
        "corrections_by_engineer": by_engineer,
        "most_corrected_artifacts": [
            {"artifact_id": aid, "count": count}
            for aid, count in most_corrected
        ],
    }


def export_corrections_for_training(
    study_id: str,
    field_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Export corrections in a format suitable for fine-tuning.

    Args:
        study_id: Study document ID.
        field_filter: Optional field to filter by (e.g., "component_type").

    Returns:
        List of training examples with old/new value pairs.
    """
    corrections = get_corrections(study_id)

    if field_filter:
        corrections = [c for c in corrections if c.get("field") == field_filter]

    training_data = []
    for c in corrections:
        training_data.append({
            "artifact_id": c.get("artifact_id"),
            "field": c.get("field"),
            "original_prediction": c.get("old_value"),
            "correct_value": c.get("new_value"),
            "corrected_at": c.get("corrected_at"),
        })

    return training_data
