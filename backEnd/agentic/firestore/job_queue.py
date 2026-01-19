"""
Firestore-based durable job queue for background tasks.

Provides reliable task execution with:
- Persistence across restarts
- Retry logic with exponential backoff
- Timeout protection
- Progress tracking

Phase 5: Workflow Reliability Implementation
Phase 6: Added failure alerts
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from firebase_admin import firestore
from pydantic import BaseModel, Field

from .client import FirestoreClient

logger = logging.getLogger(__name__)


# =============================================================================
# Job Models
# =============================================================================


class Job(BaseModel):
    """
    Represents a background job in the queue.

    Jobs are stored in Firestore and survive server restarts.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    study_id: str = Field(..., description="Associated study ID")
    job_type: Literal[
        "analyze_rooms",
        "process_assets",
        "reclassify",
        "recalculate_costs",
        "cascade_correction",
    ] = Field(..., description="Type of job to execute")
    status: Literal[
        "pending",
        "claimed",
        "running",
        "completed",
        "failed",
        "retry",
    ] = Field(default="pending", description="Current job status")

    # Timing
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the job was created",
    )
    started_at: Optional[datetime] = Field(
        default=None, description="When execution started"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="When execution completed"
    )

    # Retry configuration
    retry_count: int = Field(default=0, description="Number of retry attempts")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    timeout_seconds: int = Field(default=300, description="Execution timeout")

    # Worker tracking
    worker_id: Optional[str] = Field(
        default=None, description="ID of worker that claimed job"
    )

    # Payload and results
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="Input data for the job"
    )
    result: Optional[dict[str, Any]] = Field(
        default=None, description="Job result (on success)"
    )
    error: Optional[str] = Field(
        default=None, description="Error message (on failure)"
    )
    progress: Optional[dict[str, Any]] = Field(
        default=None, description="Progress tracking data"
    )

    # Priority (lower = higher priority)
    priority: int = Field(default=5, ge=1, le=10, description="Job priority 1-10")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }

    def to_firestore_dict(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = self.model_dump()
        # Convert datetime to Firestore timestamp
        if data.get("created_at"):
            data["created_at"] = data["created_at"]
        return data

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "Job":
        """Create Job from Firestore document."""
        # Handle Firestore timestamps
        for field in ["created_at", "started_at", "completed_at"]:
            if field in data and data[field]:
                ts = data[field]
                if hasattr(ts, "seconds"):
                    # Firestore Timestamp
                    data[field] = datetime.fromtimestamp(ts.seconds, tz=timezone.utc)
                elif isinstance(ts, str):
                    data[field] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return cls(**data)


# =============================================================================
# Job Queue
# =============================================================================


class JobQueue:
    """
    Firestore-backed job queue for durable task execution.

    Jobs persist across server restarts and support:
    - Priority ordering
    - Retry with exponential backoff
    - Timeout protection
    - Progress tracking
    """

    COLLECTION_NAME = "job_queue"

    def __init__(self):
        self._client = FirestoreClient()

    @property
    def _collection(self):
        """Get the job queue collection reference."""
        return self._client.db.collection(self.COLLECTION_NAME)

    async def enqueue(
        self,
        job_type: str,
        study_id: str,
        input_data: dict[str, Any],
        timeout_seconds: int = 300,
        max_retries: int = 3,
        priority: int = 5,
    ) -> str:
        """
        Add a job to the queue.

        Args:
            job_type: Type of job to execute
            study_id: Associated study ID
            input_data: Input data for the job
            timeout_seconds: Execution timeout
            max_retries: Maximum retry attempts
            priority: Job priority (1-10, lower = higher priority)

        Returns:
            Job ID
        """
        job = Job(
            study_id=study_id,
            job_type=job_type,
            input_data=input_data,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            priority=priority,
        )

        # Store in Firestore
        doc_ref = self._collection.document(job.id)
        doc_ref.set(job.to_firestore_dict())

        logger.info(
            f"Enqueued job {job.id} (type={job_type}, study={study_id}, "
            f"priority={priority})"
        )

        return job.id

    async def claim_next_job(self, worker_id: str) -> Optional[Job]:
        """
        Claim the next available job for processing.

        Uses a transaction to ensure only one worker can claim a job.

        Args:
            worker_id: ID of the claiming worker

        Returns:
            Claimed job or None if no jobs available
        """
        # Query for pending or retry jobs, ordered by priority and creation time
        query = (
            self._collection
            .where("status", "in", ["pending", "retry"])
            .order_by("priority")
            .order_by("created_at")
            .limit(1)
        )

        docs = list(query.stream())
        if not docs:
            return None

        doc = docs[0]
        doc_ref = self._collection.document(doc.id)

        # Use transaction to claim
        @firestore.transactional
        def claim_in_transaction(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None

            data = snapshot.to_dict()
            status = data.get("status")

            # Only claim if still pending/retry
            if status not in ["pending", "retry"]:
                return None

            # Claim the job
            transaction.update(doc_ref, {
                "status": "claimed",
                "worker_id": worker_id,
                "started_at": datetime.now(timezone.utc),
            })

            return Job.from_firestore_dict({**data, "id": doc.id})

        transaction = self._client.db.transaction()
        job = claim_in_transaction(transaction, doc_ref)

        if job:
            logger.info(f"Worker {worker_id} claimed job {job.id}")

        return job

    async def mark_running(self, job_id: str) -> None:
        """Mark a job as actively running."""
        doc_ref = self._collection.document(job_id)
        doc_ref.update({
            "status": "running",
        })

    async def update_progress(
        self,
        job_id: str,
        progress: dict[str, Any],
    ) -> None:
        """
        Update job progress.

        Args:
            job_id: Job ID
            progress: Progress data (e.g., {"step": "vision", "pct": 50})
        """
        doc_ref = self._collection.document(job_id)
        doc_ref.update({"progress": progress})

    async def complete_job(
        self,
        job_id: str,
        result: dict[str, Any],
    ) -> None:
        """
        Mark a job as successfully completed.

        Args:
            job_id: Job ID
            result: Job result data
        """
        doc_ref = self._collection.document(job_id)
        doc_ref.update({
            "status": "completed",
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        })

        logger.info(f"Job {job_id} completed successfully")

    async def fail_job(
        self,
        job_id: str,
        error: str,
        retry: bool = False,
    ) -> None:
        """
        Mark a job as failed.

        Phase 6: Sends alert on permanent failures.

        Args:
            job_id: Job ID
            error: Error message
            retry: Whether to schedule a retry
        """
        doc_ref = self._collection.document(job_id)
        doc = doc_ref.get()

        if not doc.exists:
            logger.warning(f"Job {job_id} not found for failure marking")
            return

        data = doc.to_dict()
        retry_count = data.get("retry_count", 0)
        max_retries = data.get("max_retries", 3)
        study_id = data.get("study_id", "unknown")
        job_type = data.get("job_type", "unknown")

        if retry and retry_count < max_retries:
            # Schedule retry
            doc_ref.update({
                "status": "retry",
                "error": error,
                "retry_count": retry_count + 1,
                "worker_id": None,
                "started_at": None,
            })
            logger.info(
                f"Job {job_id} scheduled for retry "
                f"({retry_count + 1}/{max_retries}): {error}"
            )
        else:
            # Mark as permanently failed
            doc_ref.update({
                "status": "failed",
                "error": error,
                "completed_at": datetime.now(timezone.utc),
            })
            logger.error(f"Job {job_id} failed permanently: {error}")

            # Phase 6: Send alert on permanent failure
            try:
                from ..observability.alerts import send_alert
                asyncio.create_task(send_alert(
                    study_id=study_id,
                    stage=f"job:{job_type}",
                    error_type="JOB_PERMANENT_FAILURE",
                    error_message=error,
                    severity="error",
                    context={
                        "job_id": job_id,
                        "job_type": job_type,
                        "retry_count": retry_count,
                        "max_retries": max_retries,
                    },
                ))
            except Exception as alert_err:
                logger.warning(f"Failed to send job failure alert: {alert_err}")

    async def get_job(self, job_id: str) -> Optional[Job]:
        """
        Get a job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job or None if not found
        """
        doc = self._collection.document(job_id).get()

        if not doc.exists:
            return None

        return Job.from_firestore_dict({**doc.to_dict(), "id": doc.id})

    async def get_jobs_by_study(
        self,
        study_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[Job]:
        """
        Get jobs for a study.

        Args:
            study_id: Study ID
            status: Optional status filter
            limit: Maximum jobs to return

        Returns:
            List of jobs
        """
        query = self._collection.where("study_id", "==", study_id)

        if status:
            query = query.where("status", "==", status)

        query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
        query = query.limit(limit)

        jobs = []
        for doc in query.stream():
            jobs.append(Job.from_firestore_dict({**doc.to_dict(), "id": doc.id}))

        return jobs

    async def get_pending_count(self) -> int:
        """Get count of pending jobs."""
        query = self._collection.where("status", "in", ["pending", "retry"])
        return len(list(query.stream()))

    async def cleanup_stale_jobs(
        self,
        timeout_minutes: int = 30,
    ) -> int:
        """
        Reset jobs that have been claimed but not completed.

        Called periodically to recover from worker crashes.

        Args:
            timeout_minutes: Time after which claimed jobs are considered stale

        Returns:
            Number of jobs reset
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

        # Find claimed/running jobs older than cutoff
        query = (
            self._collection
            .where("status", "in", ["claimed", "running"])
            .where("started_at", "<", cutoff)
        )

        reset_count = 0
        for doc in query.stream():
            data = doc.to_dict()
            retry_count = data.get("retry_count", 0)
            max_retries = data.get("max_retries", 3)

            if retry_count < max_retries:
                doc.reference.update({
                    "status": "retry",
                    "error": "Worker timeout - job reset",
                    "retry_count": retry_count + 1,
                    "worker_id": None,
                    "started_at": None,
                })
            else:
                doc.reference.update({
                    "status": "failed",
                    "error": "Worker timeout - max retries exceeded",
                    "completed_at": datetime.now(timezone.utc),
                })
            reset_count += 1

        if reset_count > 0:
            logger.info(f"Reset {reset_count} stale jobs")

        return reset_count

    async def cancel_job(self, job_id: str, reason: str = "Cancelled") -> bool:
        """
        Cancel a pending job.

        Args:
            job_id: Job ID
            reason: Cancellation reason

        Returns:
            True if cancelled, False if not cancellable
        """
        doc_ref = self._collection.document(job_id)
        doc = doc_ref.get()

        if not doc.exists:
            return False

        data = doc.to_dict()
        status = data.get("status")

        # Can only cancel pending or retry jobs
        if status not in ["pending", "retry"]:
            return False

        doc_ref.update({
            "status": "failed",
            "error": reason,
            "completed_at": datetime.now(timezone.utc),
        })

        logger.info(f"Job {job_id} cancelled: {reason}")
        return True
