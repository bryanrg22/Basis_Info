"""Firestore storage for VisionArtifacts.

Follows the same pattern as modules/room_classification/api/firebase_client.py.
Stores artifacts in the studies/{study_id} document under vision_artifacts array.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

from ..schemas.artifact import VisionArtifact

logger = logging.getLogger(__name__)

# Global Firestore client
_db = None


def _get_db():
    """Get or initialize Firestore client."""
    global _db
    if _db is None:
        # Check if Firebase is already initialized
        try:
            firebase_admin.get_app()
        except ValueError:
            # Initialize with credentials
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if cred_path:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
            else:
                # Use default credentials (for Cloud Run, etc.)
                firebase_admin.initialize_app()
        _db = firestore.client()
    return _db


def _get_study_ref(study_id: str):
    """Get reference to study document."""
    db = _get_db()
    return db.collection("studies").document(study_id)


def save_vision_artifacts(
    study_id: str,
    artifacts: List[VisionArtifact],
    merge: bool = True,
) -> None:
    """Save vision artifacts to study document.

    Args:
        study_id: Study document ID.
        artifacts: List of VisionArtifact objects to save.
        merge: If True, merge with existing artifacts. If False, replace all.
    """
    study_ref = _get_study_ref(study_id)

    # Convert artifacts to dicts
    artifact_dicts = [art.to_dict() for art in artifacts]

    if merge:
        # Get existing artifacts
        doc = study_ref.get()
        if doc.exists:
            existing = doc.to_dict().get("vision_artifacts", [])
            # Merge by artifact_id (update existing, add new)
            existing_ids = {a["artifact_id"] for a in existing}
            new_ids = {a["artifact_id"] for a in artifact_dicts}

            # Keep existing that aren't being updated
            merged = [a for a in existing if a["artifact_id"] not in new_ids]
            # Add all new/updated
            merged.extend(artifact_dicts)
            artifact_dicts = merged

    study_ref.update({"vision_artifacts": artifact_dicts})
    logger.info(f"Saved {len(artifact_dicts)} vision artifacts to study {study_id}")


def get_vision_artifacts(study_id: str) -> List[Dict[str, Any]]:
    """Get all vision artifacts for a study.

    Args:
        study_id: Study document ID.

    Returns:
        List of artifact dictionaries.
    """
    study_ref = _get_study_ref(study_id)
    doc = study_ref.get()

    if not doc.exists:
        logger.warning(f"Study {study_id} not found")
        return []

    return doc.to_dict().get("vision_artifacts", [])


def get_artifact_by_id(
    study_id: str,
    artifact_id: str,
) -> Optional[Dict[str, Any]]:
    """Get a specific artifact by ID.

    Args:
        study_id: Study document ID.
        artifact_id: Artifact ID to find.

    Returns:
        Artifact dictionary or None if not found.
    """
    artifacts = get_vision_artifacts(study_id)
    for artifact in artifacts:
        if artifact.get("artifact_id") == artifact_id:
            return artifact
    return None


def get_artifacts_by_image(
    study_id: str,
    image_id: str,
) -> List[Dict[str, Any]]:
    """Get all artifacts for a specific image.

    Args:
        study_id: Study document ID.
        image_id: Image/photo ID to filter by.

    Returns:
        List of artifact dictionaries for that image.
    """
    artifacts = get_vision_artifacts(study_id)
    return [a for a in artifacts if a.get("image_id") == image_id]


def get_artifacts_by_component(
    study_id: str,
    component_type: str,
) -> List[Dict[str, Any]]:
    """Get all artifacts of a specific component type.

    Args:
        study_id: Study document ID.
        component_type: Component type to filter by (e.g., "cabinet").

    Returns:
        List of artifact dictionaries of that type.
    """
    artifacts = get_vision_artifacts(study_id)
    return [
        a for a in artifacts
        if a.get("classification", {}).get("component_type", "").lower() == component_type.lower()
    ]


def get_artifacts_needing_review(study_id: str) -> List[Dict[str, Any]]:
    """Get all artifacts flagged for engineer review.

    Args:
        study_id: Study document ID.

    Returns:
        List of artifact dictionaries needing review.
    """
    artifacts = get_vision_artifacts(study_id)
    return [a for a in artifacts if a.get("needs_review", False)]


def update_artifact(
    study_id: str,
    artifact_id: str,
    updates: Dict[str, Any],
) -> bool:
    """Update specific fields on an artifact.

    Args:
        study_id: Study document ID.
        artifact_id: Artifact to update.
        updates: Dictionary of field updates.

    Returns:
        True if artifact was found and updated.
    """
    study_ref = _get_study_ref(study_id)
    doc = study_ref.get()

    if not doc.exists:
        return False

    artifacts = doc.to_dict().get("vision_artifacts", [])
    updated = False

    for i, artifact in enumerate(artifacts):
        if artifact.get("artifact_id") == artifact_id:
            artifacts[i].update(updates)
            updated = True
            break

    if updated:
        study_ref.update({"vision_artifacts": artifacts})
        logger.info(f"Updated artifact {artifact_id} in study {study_id}")

    return updated


def delete_artifact(study_id: str, artifact_id: str) -> bool:
    """Delete an artifact by ID.

    Args:
        study_id: Study document ID.
        artifact_id: Artifact to delete.

    Returns:
        True if artifact was found and deleted.
    """
    study_ref = _get_study_ref(study_id)
    doc = study_ref.get()

    if not doc.exists:
        return False

    artifacts = doc.to_dict().get("vision_artifacts", [])
    original_len = len(artifacts)
    artifacts = [a for a in artifacts if a.get("artifact_id") != artifact_id]

    if len(artifacts) < original_len:
        study_ref.update({"vision_artifacts": artifacts})
        logger.info(f"Deleted artifact {artifact_id} from study {study_id}")
        return True

    return False


def mark_artifact_reviewed(
    study_id: str,
    artifact_id: str,
    reviewed_by: str,
) -> bool:
    """Mark an artifact as reviewed (no longer needs_review).

    Args:
        study_id: Study document ID.
        artifact_id: Artifact to mark reviewed.
        reviewed_by: Engineer ID who reviewed.

    Returns:
        True if artifact was found and updated.
    """
    return update_artifact(
        study_id,
        artifact_id,
        {
            "needs_review": False,
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.utcnow().isoformat(),
        },
    )


def search_artifacts(
    study_id: str,
    component_type: Optional[str] = None,
    image_id: Optional[str] = None,
    min_confidence: float = 0.0,
    needs_review: Optional[bool] = None,
    grounded: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Search artifacts with multiple criteria.

    Args:
        study_id: Study document ID.
        component_type: Filter by component type.
        image_id: Filter by image ID.
        min_confidence: Minimum confidence threshold.
        needs_review: Filter by review status.
        grounded: Filter by grounding status.

    Returns:
        List of matching artifact dictionaries.
    """
    artifacts = get_vision_artifacts(study_id)

    results = []
    for artifact in artifacts:
        # Apply filters
        if component_type:
            comp = artifact.get("classification", {}).get("component_type", "")
            if comp.lower() != component_type.lower():
                continue

        if image_id and artifact.get("image_id") != image_id:
            continue

        if artifact.get("confidence", 0) < min_confidence:
            continue

        if needs_review is not None and artifact.get("needs_review") != needs_review:
            continue

        if grounded is not None and artifact.get("grounded") != grounded:
            continue

        results.append(artifact)

    return results
