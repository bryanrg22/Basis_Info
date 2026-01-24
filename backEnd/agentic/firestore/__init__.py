"""Firestore integration for Basis agentic layer."""

from .client import get_firestore_client, FirestoreClient
from .checkpointer import FirestoreCheckpointer
from .schemas import Study, WorkflowStatus
from .writeback import FirestoreWriteback, EvidenceBackedUpdate
from .classification_cache import (
    get_cached_classification,
    save_to_cache,
    get_cache_stats,
    invalidate_cache_entry,
)

__all__ = [
    "get_firestore_client",
    "FirestoreClient",
    "FirestoreCheckpointer",
    "Study",
    "WorkflowStatus",
    "FirestoreWriteback",
    "EvidenceBackedUpdate",
    # Phase 2: Classification cache
    "get_cached_classification",
    "save_to_cache",
    "get_cache_stats",
    "invalidate_cache_entry",
]
