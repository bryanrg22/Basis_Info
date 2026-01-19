"""
Checkpoint history models for workflow debugging.

Phase 6: Provides history tracking for checkpoints to enable debugging
what happened between pauses and engineer approvals.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, Field


class CheckpointHistoryEntry(BaseModel):
    """
    A single checkpoint history entry.

    Captures the state at a specific point in the workflow,
    including what triggered the checkpoint and a summary of changes.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique history entry ID",
    )
    thread_id: str = Field(
        ...,
        description="Workflow thread ID this entry belongs to",
    )
    parent_id: Optional[str] = Field(
        default=None,
        description="ID of the parent checkpoint (for lineage tracking)",
    )
    v: int = Field(
        default=1,
        description="Checkpoint version number",
    )
    from_stage: Optional[str] = Field(
        default=None,
        description="Stage the workflow was at before this checkpoint",
    )
    to_stage: Optional[str] = Field(
        default=None,
        description="Stage the workflow transitioned to",
    )
    trigger: Literal[
        "workflow_start",
        "stage_complete",
        "engineer_approval",
        "correction",
        "resume",
        "error",
    ] = Field(
        default="stage_complete",
        description="What triggered this checkpoint",
    )
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of changes (e.g., rooms_added, classifications_changed)",
    )
    channel_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of workflow state channel values",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="LangGraph checkpoint metadata",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this checkpoint was created",
    )

    def to_firestore_dict(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "parent_id": self.parent_id,
            "v": self.v,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "trigger": self.trigger,
            "summary": self.summary,
            "channel_values": self.channel_values,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "CheckpointHistoryEntry":
        """Create entry from Firestore document."""
        # Handle Firestore timestamps
        created_at = data.get("created_at")
        if created_at and hasattr(created_at, "seconds"):
            created_at = datetime.fromtimestamp(created_at.seconds, tz=timezone.utc)
        elif isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(timezone.utc)

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            thread_id=data["thread_id"],
            parent_id=data.get("parent_id"),
            v=data.get("v", 1),
            from_stage=data.get("from_stage"),
            to_stage=data.get("to_stage"),
            trigger=data.get("trigger", "stage_complete"),
            summary=data.get("summary", {}),
            channel_values=data.get("channel_values", {}),
            metadata=data.get("metadata", {}),
            created_at=created_at,
        )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }


class CheckpointDiff(BaseModel):
    """
    Comparison between two checkpoints.

    Shows what changed between two points in the workflow history.
    """

    checkpoint_a_id: str = Field(
        ...,
        description="ID of the first (earlier) checkpoint",
    )
    checkpoint_b_id: str = Field(
        ...,
        description="ID of the second (later) checkpoint",
    )
    checkpoint_a_stage: Optional[str] = Field(
        default=None,
        description="Stage of checkpoint A",
    )
    checkpoint_b_stage: Optional[str] = Field(
        default=None,
        description="Stage of checkpoint B",
    )
    fields_changed: list[str] = Field(
        default_factory=list,
        description="List of field names that changed",
    )
    additions: dict[str, Any] = Field(
        default_factory=dict,
        description="Fields/values added in checkpoint B",
    )
    deletions: dict[str, Any] = Field(
        default_factory=dict,
        description="Fields/values removed in checkpoint B",
    )
    modifications: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Fields modified with old and new values",
    )
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Human-readable summary of changes",
    )

    @classmethod
    def compute(
        cls,
        entry_a: CheckpointHistoryEntry,
        entry_b: CheckpointHistoryEntry,
    ) -> "CheckpointDiff":
        """
        Compute the diff between two checkpoint entries.

        Args:
            entry_a: Earlier checkpoint
            entry_b: Later checkpoint

        Returns:
            CheckpointDiff showing what changed
        """
        values_a = entry_a.channel_values
        values_b = entry_b.channel_values

        fields_changed = []
        additions = {}
        deletions = {}
        modifications = {}

        # Find all unique keys
        all_keys = set(values_a.keys()) | set(values_b.keys())

        for key in all_keys:
            in_a = key in values_a
            in_b = key in values_b

            if in_a and not in_b:
                # Deleted
                deletions[key] = values_a[key]
                fields_changed.append(key)
            elif in_b and not in_a:
                # Added
                additions[key] = values_b[key]
                fields_changed.append(key)
            elif in_a and in_b:
                # Check if modified
                if values_a[key] != values_b[key]:
                    modifications[key] = {
                        "old": values_a[key],
                        "new": values_b[key],
                    }
                    fields_changed.append(key)

        # Build human-readable summary
        summary = {
            "total_changes": len(fields_changed),
            "additions_count": len(additions),
            "deletions_count": len(deletions),
            "modifications_count": len(modifications),
        }

        # Add specific change summaries
        if "rooms" in modifications:
            old_rooms = modifications["rooms"]["old"]
            new_rooms = modifications["rooms"]["new"]
            if isinstance(old_rooms, list) and isinstance(new_rooms, list):
                summary["rooms_change"] = f"{len(old_rooms)} → {len(new_rooms)}"

        if "objects" in modifications:
            old_objects = modifications["objects"]["old"]
            new_objects = modifications["objects"]["new"]
            if isinstance(old_objects, list) and isinstance(new_objects, list):
                summary["objects_change"] = f"{len(old_objects)} → {len(new_objects)}"

        if "current_stage" in modifications:
            summary["stage_transition"] = (
                f"{modifications['current_stage']['old']} → "
                f"{modifications['current_stage']['new']}"
            )

        return cls(
            checkpoint_a_id=entry_a.id,
            checkpoint_b_id=entry_b.id,
            checkpoint_a_stage=entry_a.to_stage,
            checkpoint_b_stage=entry_b.to_stage,
            fields_changed=sorted(fields_changed),
            additions=additions,
            deletions=deletions,
            modifications=modifications,
            summary=summary,
        )


class CheckpointHistoryList(BaseModel):
    """
    List of checkpoint history entries for a workflow.
    """

    thread_id: str = Field(
        ...,
        description="Workflow thread ID",
    )
    entries: list[CheckpointHistoryEntry] = Field(
        default_factory=list,
        description="History entries in chronological order",
    )
    total_count: int = Field(
        default=0,
        description="Total number of history entries",
    )
    latest_stage: Optional[str] = Field(
        default=None,
        description="Current workflow stage",
    )

    @property
    def latest_entry(self) -> Optional[CheckpointHistoryEntry]:
        """Get the most recent history entry."""
        return self.entries[-1] if self.entries else None

    def get_entry_by_id(self, entry_id: str) -> Optional[CheckpointHistoryEntry]:
        """Find entry by ID."""
        return next((e for e in self.entries if e.id == entry_id), None)

    def get_entries_by_trigger(
        self,
        trigger: str,
    ) -> list[CheckpointHistoryEntry]:
        """Filter entries by trigger type."""
        return [e for e in self.entries if e.trigger == trigger]

    def get_stage_transitions(self) -> list[tuple[str, str, datetime]]:
        """Get list of stage transitions as (from, to, timestamp) tuples."""
        transitions = []
        for entry in self.entries:
            if entry.from_stage or entry.to_stage:
                transitions.append((
                    entry.from_stage or "unknown",
                    entry.to_stage or "unknown",
                    entry.created_at,
                ))
        return transitions
