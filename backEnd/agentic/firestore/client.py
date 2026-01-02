"""
Firestore client wrapper for Basis agentic layer.

Provides async-compatible access to Firestore with proper initialization.
"""

from functools import lru_cache
from typing import Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import AsyncClient, Client

from ..config.settings import get_settings


def _initialize_firebase() -> None:
    """Initialize Firebase Admin SDK if not already done."""
    if firebase_admin._apps:
        return

    settings = get_settings()

    if settings.google_application_credentials:
        cred = credentials.Certificate(settings.google_application_credentials)
        firebase_admin.initialize_app(cred)
    elif settings.firebase_project_id:
        # Use default credentials (for Cloud Run, etc.)
        firebase_admin.initialize_app(options={
            "projectId": settings.firebase_project_id
        })
    else:
        # Try default credentials
        firebase_admin.initialize_app()


@lru_cache()
def get_firestore_client() -> Client:
    """Get the Firestore client (sync)."""
    _initialize_firebase()
    return firestore.client()


class FirestoreClient:
    """
    High-level Firestore client for Basis operations.

    Provides study-focused operations with proper error handling.
    """

    def __init__(self):
        self._db: Optional[Client] = None

    @property
    def db(self) -> Client:
        """Lazy-load Firestore client."""
        if self._db is None:
            self._db = get_firestore_client()
        return self._db

    def get_study(self, study_id: str) -> Optional[dict[str, Any]]:
        """
        Get a study document by ID.

        Args:
            study_id: Study document ID

        Returns:
            Study document data or None if not found
        """
        doc_ref = self.db.collection("studies").document(study_id)
        doc = doc_ref.get()

        if not doc.exists:
            return None

        data = doc.to_dict()
        data["id"] = doc.id
        return data

    def update_study(
        self,
        study_id: str,
        updates: dict[str, Any],
    ) -> None:
        """
        Update a study document.

        Args:
            study_id: Study document ID
            updates: Fields to update
        """
        doc_ref = self.db.collection("studies").document(study_id)

        # Add server timestamp
        updates["updatedAt"] = firestore.SERVER_TIMESTAMP

        doc_ref.update(updates)

    def update_workflow_status(
        self,
        study_id: str,
        status: str,
    ) -> None:
        """
        Update study workflow status.

        Args:
            study_id: Study document ID
            status: New workflow status
        """
        self.update_study(study_id, {"workflowStatus": status})

    def get_study_documents(self, study_id: str) -> list[dict[str, Any]]:
        """
        Get uploaded documents for a study.

        Args:
            study_id: Study document ID

        Returns:
            List of uploaded file metadata
        """
        study = self.get_study(study_id)
        if not study:
            return []
        return study.get("uploadedFiles", [])

    def update_objects_with_classifications(
        self,
        study_id: str,
        objects: list[dict[str, Any]],
    ) -> None:
        """
        Update study objects with asset classifications.

        Args:
            study_id: Study document ID
            objects: List of objects with classifications
        """
        # Count items needing review
        needs_review_count = sum(1 for obj in objects if obj.get("needs_review"))

        self.update_study(study_id, {
            "objects": objects,
            "classification_summary": {
                "total": len(objects),
                "needs_review": needs_review_count,
                "completed_at": firestore.SERVER_TIMESTAMP,
            },
        })

    def list_studies_by_user(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        List studies for a user.

        Args:
            user_id: User ID
            limit: Maximum number of studies to return

        Returns:
            List of study documents
        """
        query = (
            self.db.collection("studies")
            .where("userId", "==", user_id)
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )

        studies = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            studies.append(data)

        return studies
