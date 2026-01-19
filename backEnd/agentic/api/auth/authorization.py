"""
Authorization utilities for study ownership verification.
"""

from typing import Any, Dict

from fastapi import HTTPException, status

from .dependencies import CurrentUser
from ...firestore.client import FirestoreClient


def verify_study_ownership(
    study_id: str,
    user: CurrentUser,
    client: FirestoreClient | None = None,
) -> Dict[str, Any]:
    """
    Verify that the authenticated user owns the study.

    Args:
        study_id: The study document ID
        user: The authenticated user
        client: Optional FirestoreClient instance (created if not provided)

    Returns:
        The study data if authorized

    Raises:
        HTTPException 404: If study not found
        HTTPException 403: If user doesn't own the study
    """
    if client is None:
        client = FirestoreClient()

    study = client.get_study(study_id)

    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study not found",
        )

    # Check ownership - study.userId should match user.uid
    study_owner = study.get("userId")
    if study_owner and study_owner != user.uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return study
