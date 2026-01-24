"""
Correction cascade system for propagating changes.

When an engineer makes a correction, dependent downstream data
must be recalculated to maintain consistency.

Phase 5: Workflow Reliability Implementation
Phase 2 Enhancement: Save approved classifications to verified cache
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..firestore.classification_cache import save_to_cache

logger = logging.getLogger(__name__)


# =============================================================================
# Dependency Graph
# =============================================================================


# Defines which stages depend on which other stages
# Key = stage that was corrected
# Value = list of stages that need recalculation
DEPENDENCY_GRAPH: dict[str, list[str]] = {
    "classification": ["takeoff", "cost"],  # Re-classify → recalc takeoff → recalc cost
    "takeoff": ["cost"],  # Change takeoff → recalc cost only
    "room": ["objects", "takeoff", "cost"],  # Change room → affects objects in it
    "object": ["takeoff", "cost"],  # Change object → affects its takeoff/cost
}


# =============================================================================
# Correction Models
# =============================================================================


class CorrectionType(str):
    """Types of corrections that can be made."""

    CLASSIFICATION_SECTION = "classification_section"  # Changed IRS section
    CLASSIFICATION_BUCKET = "classification_bucket"  # Changed depreciation bucket
    CLASSIFICATION_PERIOD = "classification_period"  # Changed recovery period
    TAKEOFF_QUANTITY = "takeoff_quantity"  # Changed quantity
    TAKEOFF_UNIT = "takeoff_unit"  # Changed unit
    ROOM_TYPE = "room_type"  # Changed room type
    OBJECT_NAME = "object_name"  # Renamed/relabeled object
    COST_OVERRIDE = "cost_override"  # Manual cost override


class Correction(BaseModel):
    """Record of a correction made by an engineer."""

    correction_type: str = Field(..., description="Type of correction")
    component_id: str = Field(..., description="ID of the corrected component")
    study_id: str = Field(..., description="Study ID")
    field: str = Field(..., description="Field that was corrected")
    old_value: Any = Field(default=None, description="Previous value")
    new_value: Any = Field(..., description="New value")
    reason: Optional[str] = Field(default=None, description="Reason for correction")
    user_id: Optional[str] = Field(default=None, description="User who made correction")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When correction was made",
    )


# =============================================================================
# Correction Cascade
# =============================================================================


class CorrectionCascade:
    """
    Handles cascading corrections to dependent stages.

    When a correction is made at one stage, this class:
    1. Applies the direct correction
    2. Marks dependent data as stale
    3. Enqueues recalculation jobs for dependent stages
    """

    DEPENDENCY_GRAPH = DEPENDENCY_GRAPH

    def __init__(self):
        from ..firestore.client import FirestoreClient
        self._client = FirestoreClient()

    async def apply_correction(
        self,
        study_id: str,
        correction_type: str,
        component_id: str,
        new_value: Any,
        job_queue: Optional[Any] = None,
        user_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Apply a correction and cascade to dependent stages.

        Args:
            study_id: Study ID
            correction_type: Type of correction being made
            component_id: ID of the component being corrected
            new_value: New value to apply
            job_queue: Optional JobQueue instance for enqueuing recalc jobs
            user_id: ID of user making correction
            reason: Reason for the correction

        Returns:
            Result including jobs enqueued and stages affected
        """
        logger.info(
            f"Applying correction: {correction_type} for component {component_id} "
            f"in study {study_id}"
        )

        result = {
            "correction_type": correction_type,
            "component_id": component_id,
            "stages_affected": [],
            "jobs_enqueued": [],
            "stale_data_marked": [],
        }

        # 1. Apply the direct correction
        old_value = await self._apply_direct_correction(
            study_id=study_id,
            correction_type=correction_type,
            component_id=component_id,
            new_value=new_value,
            user_id=user_id,
        )

        # 2. Record the correction for audit trail
        correction = Correction(
            correction_type=correction_type,
            component_id=component_id,
            study_id=study_id,
            field=self._get_field_for_correction_type(correction_type),
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            user_id=user_id,
        )
        await self._record_correction(correction)

        # 3. Determine which stages are affected
        source_stage = self._get_stage_for_correction_type(correction_type)
        dependent_stages = self.DEPENDENCY_GRAPH.get(source_stage, [])
        result["stages_affected"] = dependent_stages

        # 4. Mark dependent data as stale
        for stage in dependent_stages:
            await self._mark_data_stale(study_id, component_id, stage)
            result["stale_data_marked"].append(stage)

        # 5. Enqueue recalculation jobs (if job_queue provided)
        if job_queue and dependent_stages:
            jobs = await self._enqueue_recalculation_jobs(
                job_queue=job_queue,
                study_id=study_id,
                component_id=component_id,
                stages=dependent_stages,
                correction_type=correction_type,
            )
            result["jobs_enqueued"] = jobs

        logger.info(
            f"Correction cascade complete: {len(dependent_stages)} stages affected, "
            f"{len(result['jobs_enqueued'])} jobs enqueued"
        )

        return result

    async def _apply_direct_correction(
        self,
        study_id: str,
        correction_type: str,
        component_id: str,
        new_value: Any,
        user_id: Optional[str] = None,
    ) -> Any:
        """
        Apply the correction directly to the data.

        Phase 2: Also saves approved classifications to verified cache.

        Returns the old value for audit trail.
        """
        study = self._client.get_study(study_id)
        if not study:
            raise ValueError(f"Study not found: {study_id}")

        old_value = None
        objects = study.get("objects", [])
        rooms = study.get("rooms", [])

        # Classification corrections
        if correction_type in (
            CorrectionType.CLASSIFICATION_SECTION,
            CorrectionType.CLASSIFICATION_BUCKET,
            CorrectionType.CLASSIFICATION_PERIOD,
        ):
            corrected_component = None
            for obj in objects:
                if obj.get("id") == component_id:
                    clf = obj.get("asset_classification", {})
                    classification = clf.get("classification", {})

                    if correction_type == CorrectionType.CLASSIFICATION_SECTION:
                        old_value = classification.get("irs_section")
                        classification["irs_section"] = new_value
                        classification["engineer_corrected"] = True
                    elif correction_type == CorrectionType.CLASSIFICATION_BUCKET:
                        old_value = classification.get("depreciation_bucket")
                        classification["depreciation_bucket"] = new_value
                        classification["engineer_corrected"] = True
                    elif correction_type == CorrectionType.CLASSIFICATION_PERIOD:
                        old_value = classification.get("recovery_period_years")
                        classification["recovery_period_years"] = new_value
                        classification["engineer_corrected"] = True

                    clf["classification"] = classification
                    obj["asset_classification"] = clf
                    corrected_component = obj
                    break

            self._client.update_study(study_id, {"objects": objects})

            # Phase 2: Save approved classification to verified cache
            # Only cache if we have citations for IRS defensibility
            if corrected_component:
                clf = corrected_component.get("asset_classification", {})
                classification = clf.get("classification", {})
                citations = clf.get("citations", [])
                component_name = corrected_component.get("label") or corrected_component.get("original_label", "")

                # Get property type from study
                property_type = study.get("propertyType", "residential").lower()
                if "commercial" in property_type or "office" in property_type:
                    property_type = "commercial"
                else:
                    property_type = "residential"

                if citations and component_name:
                    save_to_cache(
                        db=self._client.db,
                        component_name=component_name,
                        classification=classification,
                        citations=citations,
                        property_type=property_type,
                        approved_by=user_id,
                        study_id=study_id,
                    )
                    logger.info(
                        f"Cached approved classification for '{component_name}' "
                        f"({property_type})"
                    )

        # Takeoff corrections
        elif correction_type in (
            CorrectionType.TAKEOFF_QUANTITY,
            CorrectionType.TAKEOFF_UNIT,
        ):
            takeoffs = study.get("takeoffs", [])
            for takeoff in takeoffs:
                takeoff_result = takeoff.get("takeoff", {})
                if takeoff.get("component_id") == component_id or takeoff_result.get("component_name") == component_id:
                    if correction_type == CorrectionType.TAKEOFF_QUANTITY:
                        old_value = takeoff_result.get("quantity")
                        takeoff_result["quantity"] = new_value
                        takeoff_result["engineer_corrected"] = True
                    elif correction_type == CorrectionType.TAKEOFF_UNIT:
                        old_value = takeoff_result.get("unit")
                        takeoff_result["unit"] = new_value
                        takeoff_result["engineer_corrected"] = True

                    takeoff["takeoff"] = takeoff_result
                    break

            self._client.update_study(study_id, {"takeoffs": takeoffs})

        # Room corrections
        elif correction_type == CorrectionType.ROOM_TYPE:
            for room in rooms:
                if room.get("id") == component_id:
                    old_value = room.get("room_type")
                    room["room_type"] = new_value
                    room["engineer_corrected"] = True
                    break

            self._client.update_study(study_id, {"rooms": rooms})

        # Object corrections
        elif correction_type == CorrectionType.OBJECT_NAME:
            for obj in objects:
                if obj.get("id") == component_id:
                    old_value = obj.get("label") or obj.get("original_label")
                    obj["label"] = new_value
                    obj["engineer_corrected"] = True
                    break

            self._client.update_study(study_id, {"objects": objects})

        # Cost override
        elif correction_type == CorrectionType.COST_OVERRIDE:
            cost_estimates = study.get("costEstimates", [])
            for estimate in cost_estimates:
                est = estimate.get("estimate", {})
                if est.get("component_name") == component_id or estimate.get("component_id") == component_id:
                    old_value = est.get("final_cost")
                    est["final_cost"] = new_value
                    est["engineer_override"] = True
                    est["original_calculated_cost"] = old_value
                    estimate["estimate"] = est
                    break

            self._client.update_study(study_id, {"costEstimates": cost_estimates})

        return old_value

    def _get_field_for_correction_type(self, correction_type: str) -> str:
        """Map correction type to field name."""
        mapping = {
            CorrectionType.CLASSIFICATION_SECTION: "irs_section",
            CorrectionType.CLASSIFICATION_BUCKET: "depreciation_bucket",
            CorrectionType.CLASSIFICATION_PERIOD: "recovery_period_years",
            CorrectionType.TAKEOFF_QUANTITY: "quantity",
            CorrectionType.TAKEOFF_UNIT: "unit",
            CorrectionType.ROOM_TYPE: "room_type",
            CorrectionType.OBJECT_NAME: "label",
            CorrectionType.COST_OVERRIDE: "final_cost",
        }
        return mapping.get(correction_type, correction_type)

    def _get_stage_for_correction_type(self, correction_type: str) -> str:
        """Map correction type to source stage."""
        if correction_type.startswith("classification"):
            return "classification"
        elif correction_type.startswith("takeoff"):
            return "takeoff"
        elif correction_type == CorrectionType.ROOM_TYPE:
            return "room"
        elif correction_type == CorrectionType.OBJECT_NAME:
            return "object"
        elif correction_type == CorrectionType.COST_OVERRIDE:
            return "cost"
        return "unknown"

    async def _record_correction(self, correction: Correction) -> None:
        """Store correction in Firestore for audit trail."""
        doc_ref = (
            self._client.db.collection("studies")
            .document(correction.study_id)
            .collection("correction_history")
            .document()
        )

        doc_ref.set({
            "correction_type": correction.correction_type,
            "component_id": correction.component_id,
            "field": correction.field,
            "old_value": correction.old_value,
            "new_value": correction.new_value,
            "reason": correction.reason,
            "user_id": correction.user_id,
            "created_at": correction.created_at,
        })

    async def _mark_data_stale(
        self,
        study_id: str,
        component_id: str,
        stage: str,
    ) -> None:
        """
        Mark data as stale in the workflow state.

        This allows the frontend to show which data needs recalculation.
        """
        study = self._client.get_study(study_id)
        if not study:
            return

        stale_data = study.get("stale_data", {})
        if stage not in stale_data:
            stale_data[stage] = {}
        stale_data[stage][component_id] = True

        self._client.update_study(study_id, {"stale_data": stale_data})

    async def _enqueue_recalculation_jobs(
        self,
        job_queue: Any,
        study_id: str,
        component_id: str,
        stages: list[str],
        correction_type: str,
    ) -> list[str]:
        """
        Enqueue jobs to recalculate affected stages.

        Returns list of job IDs.
        """
        jobs = []

        # Map stages to job types
        stage_to_job = {
            "classification": "reclassify",
            "takeoff": None,  # No direct recalc, part of reclassify
            "cost": "recalculate_costs",
        }

        for stage in stages:
            job_type = stage_to_job.get(stage)
            if job_type:
                job_id = await job_queue.enqueue(
                    job_type=job_type,
                    study_id=study_id,
                    input_data={
                        "component_ids": [component_id],
                        "triggered_by": correction_type,
                    },
                    timeout_seconds=180,
                    max_retries=2,
                    priority=4,  # Higher priority for corrections
                )
                jobs.append(job_id)
                logger.info(f"Enqueued {job_type} job {job_id} for {component_id}")

        return jobs


# =============================================================================
# Convenience Functions
# =============================================================================


async def apply_engineer_correction(
    study_id: str,
    correction_type: str,
    component_id: str,
    new_value: Any,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """
    Apply an engineer correction with cascade.

    Convenience function that sets up the cascade processor and job queue.

    Args:
        study_id: Study ID
        correction_type: Type of correction
        component_id: Component being corrected
        new_value: New value
        user_id: User making the correction
        reason: Reason for correction

    Returns:
        Result of the correction cascade
    """
    from ..firestore.job_queue import JobQueue

    cascade = CorrectionCascade()
    job_queue = JobQueue()

    return await cascade.apply_correction(
        study_id=study_id,
        correction_type=correction_type,
        component_id=component_id,
        new_value=new_value,
        job_queue=job_queue,
        user_id=user_id,
        reason=reason,
    )
