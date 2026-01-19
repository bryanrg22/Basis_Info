"""
Background job worker for processing durable queue tasks.

Provides:
- Continuous job polling
- Timeout protection
- Automatic retry on failure
- Progress tracking

Phase 5: Workflow Reliability Implementation
"""

import asyncio
import logging
import os
import signal
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Optional

from ..firestore.job_queue import Job, JobQueue
from ..config.settings import get_settings

logger = logging.getLogger(__name__)


# =============================================================================
# Job Handlers Registry
# =============================================================================


# Registry of job type -> handler function
_JOB_HANDLERS: dict[str, Callable] = {}


def register_job_handler(job_type: str):
    """
    Decorator to register a job handler.

    Usage:
        @register_job_handler("analyze_rooms")
        async def handle_analyze_rooms(job: Job) -> dict:
            ...
    """
    def decorator(func: Callable):
        _JOB_HANDLERS[job_type] = func
        return func
    return decorator


def get_job_handler(job_type: str) -> Optional[Callable]:
    """Get handler for a job type."""
    return _JOB_HANDLERS.get(job_type)


# =============================================================================
# Built-in Job Handlers
# =============================================================================


@register_job_handler("analyze_rooms")
async def handle_analyze_rooms(job: Job, progress_callback: Callable) -> dict:
    """
    Handle analyze_rooms job.

    Runs vision analysis and room enrichment.
    """
    from ..graph.nodes import analyze_rooms_node
    from ..graph.state import WorkflowState
    from ..firestore.client import FirestoreClient

    client = FirestoreClient()
    study_id = job.study_id
    input_data = job.input_data

    # Build minimal state for the node
    state = WorkflowState(
        study_id=study_id,
        user_id=input_data.get("user_id", ""),
        property_name=input_data.get("property_name", ""),
        current_stage="analyzing_rooms",
        rooms=input_data.get("rooms", []),
        objects=input_data.get("objects", []),
        reference_doc_ids=input_data.get("reference_doc_ids", []),
        study_doc_ids=input_data.get("study_doc_ids", []),
    )

    # Update progress
    await progress_callback({"step": "starting_vision", "pct": 0})

    # Run the analyze_rooms_node
    result_state = await analyze_rooms_node(state)

    await progress_callback({"step": "vision_complete", "pct": 80})

    # Update Firestore with results
    client.update_study(study_id, {
        "rooms": result_state.get("rooms", []),
        "objects": result_state.get("objects", []),
        "roomsReady": True,
    })

    await progress_callback({"step": "saved", "pct": 100})

    return {
        "rooms_count": len(result_state.get("rooms", [])),
        "objects_count": len(result_state.get("objects", [])),
    }


@register_job_handler("process_assets")
async def handle_process_assets(job: Job, progress_callback: Callable) -> dict:
    """
    Handle process_assets job.

    Runs object enrichment, takeoff, classification, and cost estimation.
    """
    from ..graph.nodes import process_assets_node
    from ..graph.state import WorkflowState
    from ..firestore.client import FirestoreClient

    client = FirestoreClient()
    study_id = job.study_id
    input_data = job.input_data

    # Get current study state
    study = client.get_study(study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")

    state = WorkflowState(
        study_id=study_id,
        user_id=study.get("userId", ""),
        property_name=study.get("propertyName", ""),
        current_stage="processing_assets",
        rooms=study.get("rooms", []),
        objects=study.get("objects", []),
        reference_doc_ids=input_data.get("reference_doc_ids", []),
        study_doc_ids=input_data.get("study_doc_ids", []),
    )

    await progress_callback({"step": "starting_assets", "pct": 0})

    result_state = await process_assets_node(state)

    await progress_callback({"step": "complete", "pct": 100})

    return {
        "objects_processed": len(result_state.get("objects", [])),
        "takeoffs_count": len(result_state.get("takeoffs", [])),
        "classifications_count": len(result_state.get("asset_classifications", [])),
        "total_cost": result_state.get("cost_summary", {}).get("total_cost", 0),
    }


@register_job_handler("reclassify")
async def handle_reclassify(job: Job, progress_callback: Callable) -> dict:
    """
    Handle reclassify job.

    Re-runs classification for specific components.
    """
    from ..agents.asset_agent import classify_components_batch
    from ..agents.base_agent import StageContext
    from ..firestore.client import FirestoreClient

    client = FirestoreClient()
    study_id = job.study_id
    input_data = job.input_data

    component_ids = input_data.get("component_ids", [])
    if not component_ids:
        return {"reclassified": 0}

    # Get study data
    study = client.get_study(study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")

    # Filter to specified components
    objects = study.get("objects", [])
    to_reclassify = [
        obj for obj in objects
        if obj.get("id") in component_ids
    ]

    if not to_reclassify:
        return {"reclassified": 0}

    await progress_callback({"step": "reclassifying", "pct": 0})

    context = StageContext(
        study_id=study_id,
        property_name=study.get("propertyName"),
        reference_doc_ids=input_data.get("reference_doc_ids", []),
        study_doc_ids=input_data.get("study_doc_ids", []),
    )

    new_classifications = await classify_components_batch(
        components=to_reclassify,
        context=context,
        max_concurrent=2,
    )

    await progress_callback({"step": "updating", "pct": 80})

    # Merge back into objects
    classifications_by_id = {
        c.get("component_id", c.get("component")): c
        for c in new_classifications
    }

    updated_objects = []
    for obj in objects:
        obj_id = obj.get("id")
        if obj_id in classifications_by_id:
            obj["asset_classification"] = classifications_by_id[obj_id]
            obj["needs_review"] = classifications_by_id[obj_id].get("needs_review", False)
        updated_objects.append(obj)

    client.update_study(study_id, {"objects": updated_objects})

    await progress_callback({"step": "complete", "pct": 100})

    return {"reclassified": len(new_classifications)}


@register_job_handler("recalculate_costs")
async def handle_recalculate_costs(job: Job, progress_callback: Callable) -> dict:
    """
    Handle recalculate_costs job.

    Re-runs cost estimation for specific components.
    """
    from ..agents.cost_agent import estimate_costs_batch, aggregate_costs
    from ..agents.base_agent import StageContext
    from ..firestore.client import FirestoreClient

    client = FirestoreClient()
    study_id = job.study_id
    input_data = job.input_data

    # Get study data
    study = client.get_study(study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")

    takeoffs = study.get("takeoffs", [])
    component_ids = input_data.get("component_ids")

    # Filter if specific components specified
    if component_ids:
        takeoffs = [t for t in takeoffs if t.get("component_id") in component_ids]

    if not takeoffs:
        return {"recalculated": 0}

    await progress_callback({"step": "estimating_costs", "pct": 0})

    context = StageContext(
        study_id=study_id,
        property_name=study.get("propertyName"),
        reference_doc_ids=input_data.get("reference_doc_ids", []),
        study_doc_ids=input_data.get("study_doc_ids", []),
    )

    takeoff_data = []
    for t in takeoffs:
        takeoff_result = t.get("takeoff", {}) or {}
        takeoff_data.append({
            "component_name": takeoff_result.get("component_name", t.get("component_name", "")),
            "quantity": takeoff_result.get("quantity", 1),
            "unit": takeoff_result.get("unit", "EA"),
        })

    cost_estimates = await estimate_costs_batch(
        takeoffs=takeoff_data,
        context=context,
        quality_tier=input_data.get("quality_tier", "standard"),
        location_factor=input_data.get("location_factor", 1.0),
        year_factor=input_data.get("year_factor", 1.0),
    )

    cost_summary = aggregate_costs(cost_estimates)

    await progress_callback({"step": "updating", "pct": 80})

    # Update study with new costs
    client.update_study(study_id, {
        "costEstimates": cost_estimates,
        "costSummary": cost_summary,
    })

    await progress_callback({"step": "complete", "pct": 100})

    return {
        "recalculated": len(cost_estimates),
        "total_cost": cost_summary.get("total_cost", 0),
    }


@register_job_handler("cascade_correction")
async def handle_cascade_correction(job: Job, progress_callback: Callable) -> dict:
    """
    Handle cascade_correction job.

    Propagates an engineer's correction to dependent stages.
    """
    from ..graph.corrections import CorrectionCascade

    input_data = job.input_data
    cascade = CorrectionCascade()

    await progress_callback({"step": "applying_correction", "pct": 0})

    result = await cascade.apply_correction(
        study_id=job.study_id,
        correction_type=input_data["correction_type"],
        component_id=input_data["component_id"],
        new_value=input_data["new_value"],
        job_queue=JobQueue(),
    )

    await progress_callback({"step": "complete", "pct": 100})

    return result


# =============================================================================
# Job Worker
# =============================================================================


class JobWorker:
    """
    Background worker that processes jobs from the queue.

    Features:
    - Continuous polling with configurable interval
    - Timeout protection per job
    - Automatic retry on failure
    - Graceful shutdown
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        poll_interval_seconds: float = 5.0,
        stale_job_cleanup_interval: int = 300,
    ):
        """
        Initialize the worker.

        Args:
            worker_id: Unique worker identifier (auto-generated if None)
            poll_interval_seconds: Seconds between queue polls
            stale_job_cleanup_interval: Seconds between stale job cleanups
        """
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.poll_interval = poll_interval_seconds
        self.stale_cleanup_interval = stale_job_cleanup_interval
        self.job_queue = JobQueue()
        self._running = False
        self._current_job: Optional[Job] = None

    async def _make_progress_callback(self, job_id: str) -> Callable:
        """Create a progress callback for a job."""
        async def callback(progress: dict):
            await self.job_queue.update_progress(job_id, progress)
        return callback

    async def execute_job(self, job: Job) -> dict:
        """
        Execute a single job.

        Args:
            job: Job to execute

        Returns:
            Job result

        Raises:
            ValueError: If no handler registered for job type
            Exception: Any exception from the handler
        """
        handler = get_job_handler(job.job_type)
        if not handler:
            raise ValueError(f"No handler registered for job type: {job.job_type}")

        progress_callback = await self._make_progress_callback(job.id)
        return await handler(job, progress_callback)

    async def process_one_job(self) -> bool:
        """
        Try to process one job from the queue.

        Returns:
            True if a job was processed, False if queue was empty
        """
        # Try to claim a job
        job = await self.job_queue.claim_next_job(self.worker_id)
        if not job:
            return False

        self._current_job = job
        logger.info(
            f"[{self.worker_id}] Processing job {job.id} "
            f"(type={job.job_type}, study={job.study_id})"
        )

        try:
            # Mark as running
            await self.job_queue.mark_running(job.id)

            # Execute with timeout
            result = await asyncio.wait_for(
                self.execute_job(job),
                timeout=job.timeout_seconds,
            )

            # Mark as completed
            await self.job_queue.complete_job(job.id, result)
            logger.info(f"[{self.worker_id}] Job {job.id} completed: {result}")

        except asyncio.TimeoutError:
            logger.error(
                f"[{self.worker_id}] Job {job.id} timed out "
                f"after {job.timeout_seconds}s"
            )
            await self.job_queue.fail_job(
                job.id,
                f"Timeout after {job.timeout_seconds}s",
                retry=True,
            )

        except Exception as e:
            logger.error(
                f"[{self.worker_id}] Job {job.id} failed: {e}",
                exc_info=True,
            )
            retry = job.retry_count < job.max_retries
            await self.job_queue.fail_job(job.id, str(e), retry=retry)

        finally:
            self._current_job = None

        return True

    async def run(self) -> None:
        """
        Run the worker loop.

        Continuously polls the queue and processes jobs until stopped.
        """
        self._running = True
        logger.info(f"[{self.worker_id}] Worker starting...")

        cleanup_counter = 0
        cleanup_threshold = int(self.stale_cleanup_interval / self.poll_interval)

        while self._running:
            try:
                # Process one job
                processed = await self.process_one_job()

                if not processed:
                    # No job available, sleep before next poll
                    await asyncio.sleep(self.poll_interval)

                # Periodic stale job cleanup
                cleanup_counter += 1
                if cleanup_counter >= cleanup_threshold:
                    await self.job_queue.cleanup_stale_jobs()
                    cleanup_counter = 0

            except Exception as e:
                logger.error(f"[{self.worker_id}] Worker error: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

        logger.info(f"[{self.worker_id}] Worker stopped")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info(f"[{self.worker_id}] Stopping worker...")
        self._running = False

        # Wait for current job to complete (up to timeout)
        if self._current_job:
            logger.info(
                f"[{self.worker_id}] Waiting for current job "
                f"{self._current_job.id} to complete..."
            )


# =============================================================================
# Worker Lifecycle
# =============================================================================


@asynccontextmanager
async def worker_context(worker_id: Optional[str] = None):
    """
    Context manager for running a worker.

    Usage:
        async with worker_context() as worker:
            await worker.run()
    """
    worker = JobWorker(worker_id=worker_id)

    # Set up signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))

    try:
        yield worker
    finally:
        await worker.stop()


async def start_worker(worker_id: Optional[str] = None) -> None:
    """
    Start a background job worker.

    This is the main entry point for running the worker.
    """
    settings = get_settings()

    worker = JobWorker(
        worker_id=worker_id,
        poll_interval_seconds=settings.job_poll_interval_seconds
        if hasattr(settings, "job_poll_interval_seconds")
        else 5.0,
    )

    # Set up signal handlers for graceful shutdown
    def shutdown_handler(sig):
        logger.info(f"Received signal {sig}, initiating shutdown...")
        asyncio.create_task(worker.stop())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: shutdown_handler(s))

    await worker.run()


# CLI entry point
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(start_worker())
