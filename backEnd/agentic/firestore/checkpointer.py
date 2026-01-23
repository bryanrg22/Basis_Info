"""
LangGraph checkpointer using Firestore for persistence.

Stores workflow state in Firestore for resumability across restarts.
Enables workflows to pause at engineer review checkpoints and resume later.

Phase 6: Added checkpoint history tracking for debugging and audit trail.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator, Optional, Sequence, Tuple

from firebase_admin import firestore
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from .client import get_firestore_client
from .checkpoint_history import (
    CheckpointHistoryEntry,
    CheckpointHistoryList,
    CheckpointDiff,
)

logger = logging.getLogger(__name__)


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

    async def aget_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """Async version of get_tuple."""
        return self.get_tuple(config)

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
        # Filter out reserved field names (starting with __) from all dicts
        doc_data = {
            "v": checkpoint["v"],
            "id": checkpoint.get("id", ""),
            "ts": checkpoint.get("ts", ""),
            "channel_values": self._serialize_values(checkpoint.get("channel_values", {})),
            "channel_versions": self._filter_reserved_keys(checkpoint.get("channel_versions", {})),
            "versions_seen": self._filter_reserved_keys(checkpoint.get("versions_seen", {})),
            "metadata": {
                "source": metadata.get("source", "input"),
                "step": metadata.get("step", 0),
                "writes": self._filter_reserved_keys(metadata.get("writes") or {}),
            },
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        self.db.collection(self.collection).document(thread_id).set(doc_data)

        return config

    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Optional[dict] = None,
    ) -> dict:
        """Async version of put."""
        return self.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: dict,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        Store intermediate writes for a checkpoint.

        This is used by LangGraph to store partial writes before the full
        checkpoint is saved. For Firestore, we store these in a subcollection.

        Args:
            config: Configuration with thread_id
            writes: List of (channel, value) tuples to write
            task_id: Task identifier for these writes
            task_path: Path to the task in the graph (optional)
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id", "")

        # Store writes in a subcollection
        writes_ref = (
            self.db.collection(self.collection)
            .document(thread_id)
            .collection("writes")
        )

        # Serialize writes
        for idx, (channel, value) in enumerate(writes):
            # Skip reserved channel names
            if channel.startswith("__"):
                continue

            write_id = f"{checkpoint_id}_{task_id}_{idx}"
            try:
                # Try to serialize the value
                serialized_value = value
                if not isinstance(value, (str, int, float, bool, type(None), list, dict)):
                    serialized_value = str(value)

                writes_ref.document(write_id).set({
                    "channel": channel,
                    "value": serialized_value,
                    "task_id": task_id,
                    "task_path": task_path,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "created_at": firestore.SERVER_TIMESTAMP,
                })
            except Exception as e:
                logger.warning(f"Failed to save write for channel {channel}: {e}")

    async def aput_writes(
        self,
        config: dict,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Async version of put_writes."""
        return self.put_writes(config, writes, task_id, task_path)

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

    async def alist(
        self,
        config: Optional[dict] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ):
        """Async version of list."""
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    def _filter_reserved_keys(self, data: dict) -> dict:
        """
        Filter out Firestore reserved field names from a dictionary.

        Firestore doesn't allow field names starting with '__'.
        LangGraph uses '__start__', '__end__', etc. internally.
        """
        if not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items() if not k.startswith("__")}

    def _serialize_values(self, values: dict) -> dict:
        """
        Serialize channel values for Firestore storage.

        Handles non-JSON-serializable types by converting to JSON strings.
        Filters out LangGraph internal fields that Firestore doesn't allow.
        """
        serialized = {}
        for key, value in values.items():
            # Skip LangGraph internal fields - Firestore doesn't allow field names
            # starting with '__' (e.g., '__start__', '__end__')
            if key.startswith("__"):
                continue
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

    # =========================================================================
    # Phase 6: Checkpoint History Methods
    # =========================================================================

    def _get_history_collection(self, thread_id: str):
        """Get the history subcollection for a thread."""
        return (
            self.db.collection(self.collection)
            .document(thread_id)
            .collection("history")
        )

    def put_with_history(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        trigger: str = "stage_complete",
        from_stage: Optional[str] = None,
        to_stage: Optional[str] = None,
        summary: Optional[dict] = None,
        new_versions: Optional[dict] = None,
    ) -> dict:
        """
        Save a checkpoint with history tracking.

        Phase 6: Writes to both the latest checkpoint and the history subcollection.

        Args:
            config: Configuration with thread_id
            checkpoint: Checkpoint to save
            metadata: Checkpoint metadata
            trigger: What triggered this checkpoint
            from_stage: Previous workflow stage
            to_stage: New workflow stage
            summary: Summary of changes
            new_versions: New channel versions (optional)

        Returns:
            Updated config
        """
        thread_id = config["configurable"]["thread_id"]

        # Get the current checkpoint for parent_id
        current = self.get_tuple(config)
        parent_id = None
        version = 1

        if current:
            # Get the latest history entry ID as parent
            history_docs = list(
                self._get_history_collection(thread_id)
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            if history_docs:
                parent_id = history_docs[0].id
                version = history_docs[0].to_dict().get("v", 0) + 1

        # Create history entry
        history_entry = CheckpointHistoryEntry(
            thread_id=thread_id,
            parent_id=parent_id,
            v=version,
            from_stage=from_stage,
            to_stage=to_stage,
            trigger=trigger,
            summary=summary or {},
            channel_values=self._serialize_values(checkpoint.get("channel_values", {})),
            metadata={
                "source": metadata.get("source", "input"),
                "step": metadata.get("step", 0),
            },
        )

        # Write history entry
        self._get_history_collection(thread_id).document(history_entry.id).set(
            history_entry.to_firestore_dict()
        )

        logger.debug(
            f"Checkpoint history entry {history_entry.id} saved for thread {thread_id} "
            f"(trigger={trigger}, from={from_stage} to={to_stage})"
        )

        # Also save as latest checkpoint using normal put
        return self.put(config, checkpoint, metadata, new_versions)

    def get_history(
        self,
        thread_id: str,
        limit: int = 50,
    ) -> CheckpointHistoryList:
        """
        Get checkpoint history for a thread.

        Phase 6: Returns all historical checkpoints for debugging.

        Args:
            thread_id: Workflow thread ID
            limit: Maximum entries to return

        Returns:
            CheckpointHistoryList with all history entries
        """
        history_ref = self._get_history_collection(thread_id)
        query = (
            history_ref
            .order_by("created_at", direction=firestore.Query.ASCENDING)
            .limit(limit)
        )

        entries = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            entries.append(CheckpointHistoryEntry.from_firestore_dict(data))

        latest_stage = entries[-1].to_stage if entries else None

        return CheckpointHistoryList(
            thread_id=thread_id,
            entries=entries,
            total_count=len(entries),
            latest_stage=latest_stage,
        )

    def get_history_entry(
        self,
        thread_id: str,
        entry_id: str,
    ) -> Optional[CheckpointHistoryEntry]:
        """
        Get a specific history entry.

        Args:
            thread_id: Workflow thread ID
            entry_id: History entry ID

        Returns:
            CheckpointHistoryEntry or None if not found
        """
        doc = self._get_history_collection(thread_id).document(entry_id).get()

        if not doc.exists:
            return None

        data = doc.to_dict()
        data["id"] = doc.id
        return CheckpointHistoryEntry.from_firestore_dict(data)

    def diff_checkpoints(
        self,
        thread_id: str,
        entry_id_a: str,
        entry_id_b: str,
    ) -> Optional[CheckpointDiff]:
        """
        Compute the diff between two checkpoint history entries.

        Phase 6: Shows what changed between two points in workflow history.

        Args:
            thread_id: Workflow thread ID
            entry_id_a: First (earlier) entry ID
            entry_id_b: Second (later) entry ID

        Returns:
            CheckpointDiff or None if entries not found
        """
        entry_a = self.get_history_entry(thread_id, entry_id_a)
        entry_b = self.get_history_entry(thread_id, entry_id_b)

        if not entry_a or not entry_b:
            return None

        return CheckpointDiff.compute(entry_a, entry_b)
