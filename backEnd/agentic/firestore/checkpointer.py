"""
LangGraph checkpointer using Firestore for persistence.

Stores workflow state in Firestore for resumability across restarts.
Enables workflows to pause at engineer review checkpoints and resume later.
"""

import json
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Sequence, Tuple

from firebase_admin import firestore
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from .client import get_firestore_client


class FirestoreCheckpointer(BaseCheckpointSaver):
    """
    Firestore-backed checkpointer for LangGraph workflows.

    Stores checkpoints in Firestore for persistence across restarts.
    Enables human-in-the-loop workflows with engineer review stages.

    Collection structure:
        workflow_checkpoints/
          {thread_id}/
            checkpoint: {channel_values, versions, ...}
            metadata: {step, source, ...}
            updated_at: timestamp

    Usage:
        from langgraph.graph import StateGraph
        from basis.firestore.checkpointer import FirestoreCheckpointer

        checkpointer = FirestoreCheckpointer()
        workflow = StateGraph(WorkflowState)
        # ... add nodes and edges ...
        app = workflow.compile(checkpointer=checkpointer)
    """

    def __init__(self, collection: str = "workflow_checkpoints"):
        """
        Initialize Firestore checkpointer.

        Args:
            collection: Firestore collection name for checkpoints
        """
        super().__init__()
        self.collection = collection
        self._db = None

    @property
    def db(self):
        """Lazy-load Firestore client."""
        if self._db is None:
            self._db = get_firestore_client()
        return self._db

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """
        Get checkpoint tuple (checkpoint + metadata) for a thread.

        Args:
            config: Configuration with thread_id

        Returns:
            CheckpointTuple or None if not found
        """
        thread_id = config["configurable"]["thread_id"]
        doc = self.db.collection(self.collection).document(thread_id).get()

        if not doc.exists:
            return None

        data = doc.to_dict()

        # Reconstruct checkpoint
        checkpoint = Checkpoint(
            v=data.get("v", 1),
            id=data.get("id", ""),
            ts=data.get("ts", ""),
            channel_values=self._deserialize_values(data.get("channel_values", {})),
            channel_versions=data.get("channel_versions", {}),
            versions_seen=data.get("versions_seen", {}),
        )

        # Reconstruct metadata
        metadata = CheckpointMetadata(
            source=data.get("metadata", {}).get("source", "input"),
            step=data.get("metadata", {}).get("step", 0),
            writes=data.get("metadata", {}).get("writes"),
        )

        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
        )

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Optional[dict] = None,
    ) -> dict:
        """
        Save a checkpoint to Firestore.

        Args:
            config: Configuration with thread_id
            checkpoint: Checkpoint to save
            metadata: Checkpoint metadata
            new_versions: New channel versions (optional)

        Returns:
            Updated config
        """
        thread_id = config["configurable"]["thread_id"]

        # Serialize checkpoint data
        doc_data = {
            "v": checkpoint["v"],
            "id": checkpoint.get("id", ""),
            "ts": checkpoint.get("ts", ""),
            "channel_values": self._serialize_values(checkpoint.get("channel_values", {})),
            "channel_versions": checkpoint.get("channel_versions", {}),
            "versions_seen": checkpoint.get("versions_seen", {}),
            "metadata": {
                "source": metadata.get("source", "input"),
                "step": metadata.get("step", 0),
                "writes": metadata.get("writes"),
            },
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        self.db.collection(self.collection).document(thread_id).set(doc_data)

        return config

    def list(
        self,
        config: Optional[dict] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """
        List checkpoints (returns current checkpoint only).

        For Firestore, we only store the latest checkpoint per thread.
        Full history would require subcollections.
        """
        if config is None:
            return

        tuple_result = self.get_tuple(config)
        if tuple_result:
            yield tuple_result

    def _serialize_values(self, values: dict) -> dict:
        """
        Serialize channel values for Firestore storage.

        Handles non-JSON-serializable types by converting to JSON strings.
        """
        serialized = {}
        for key, value in values.items():
            try:
                # Try to store directly (works for basic types)
                json.dumps(value)  # Test if serializable
                serialized[key] = value
            except (TypeError, ValueError):
                # Convert to JSON string for complex types
                serialized[key] = {
                    "_serialized": True,
                    "_value": str(value),
                }
        return serialized

    def _deserialize_values(self, values: dict) -> dict:
        """
        Deserialize channel values from Firestore.
        """
        deserialized = {}
        for key, value in values.items():
            if isinstance(value, dict) and value.get("_serialized"):
                # This was serialized - return as string
                # Full deserialization would require type info
                deserialized[key] = value.get("_value")
            else:
                deserialized[key] = value
        return deserialized

    def delete_checkpoint(self, thread_id: str) -> None:
        """
        Delete a checkpoint.

        Args:
            thread_id: Thread ID to delete
        """
        self.db.collection(self.collection).document(thread_id).delete()

    def get_checkpoint_age(self, thread_id: str) -> Optional[float]:
        """
        Get age of checkpoint in seconds.

        Args:
            thread_id: Thread ID to check

        Returns:
            Age in seconds or None if not found
        """
        doc = self.db.collection(self.collection).document(thread_id).get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        updated_at = data.get("updated_at")
        if updated_at:
            now = datetime.now(timezone.utc)
            return (now - updated_at).total_seconds()
        return None
