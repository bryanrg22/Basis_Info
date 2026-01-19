"""
LangGraph node functions for workflow stages.

SIMPLIFIED STAGE-GATED WORKFLOW:
1. load_study - Load study data from Firestore
2. analyze_rooms - Vision analysis to detect rooms
3. resource_extraction - Extract appraisal data (PAUSE #1)
4. reviewing_rooms - Room review checkpoint (PAUSE #2)
5. process_assets - Combined: objects + takeoffs + classification + costs
6. engineering_takeoff - Asset review checkpoint (PAUSE #3)
7. complete - Mark workflow complete

Each node:
1. Gets study data from Firestore
2. Runs the appropriate agent(s)
3. Writes results back to Firestore
4. Returns updated state
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from ..agents.base_agent import StageContext
from ..agents.asset_agent import classify_components_batch
from ..agents.room_agent import enrich_rooms_batch
from ..agents.object_agent import enrich_objects_batch
from ..agents.takeoff_agent import calculate_takeoffs_batch
from ..agents.cost_agent import estimate_costs_batch, aggregate_costs
from ..agents.vision_agent import analyze_study_images
from ..agents.classification_verifier import verify_classifications_batch
from ..agents.document_extraction_agent import extract_document_fields
from ..firestore.client import FirestoreClient
from ..firestore.writeback import FirestoreWriteback
from ..observability.tracing import get_tracer
from .state import WorkflowState


# =============================================================================
# Helper Functions
# =============================================================================


def _build_stage_context(state: WorkflowState) -> StageContext:
    """Build StageContext from workflow state."""
    return StageContext(
        study_id=state["study_id"],
        property_name=state.get("property_name"),
        reference_doc_ids=state.get("reference_doc_ids", []),
        study_doc_ids=state.get("study_doc_ids", []),
    )


def _get_room_for_object(obj: dict, rooms: list[dict]) -> dict | None:
    """
    Get room context for a specific object.

    Matches objects to rooms by room_id. Falls back to the first room
    if no match is found.

    Args:
        obj: Object dict with optional room_id field
        rooms: List of room dicts with id field

    Returns:
        Matching room dict, or first room as fallback, or None if no rooms
    """
    room_id = obj.get("room_id")
    if room_id and rooms:
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if room:
            return room
    # Fallback to first room
    return rooms[0] if rooms else None


# =============================================================================
# Stage Nodes
# =============================================================================


async def load_study_node(state: WorkflowState) -> WorkflowState:
    """
    Load study data from Firestore.

    This is the entry point - loads current study state.
    """
    tracer = get_tracer()
    client = FirestoreClient()

    with tracer.span("load_study"):
        study = client.get_study(state["study_id"])

        if not study:
            return {
                **state,
                "last_error": f"Study not found: {state['study_id']}",
            }

        return {
            **state,
            "user_id": study.get("userId", ""),
            "property_name": study.get("propertyName", ""),
            "current_stage": study.get("workflowStatus", "uploading_documents"),
            "rooms": study.get("rooms", []),
            "objects": study.get("objects", []),
            "takeoffs": study.get("takeoffs", []),
            "appraisal_resources": study.get("appraisalResources", {}),
        }


async def analyze_rooms_node(state: WorkflowState) -> WorkflowState:
    """
    Analyze rooms from uploaded images using Vision Agent.

    RUNS AS BACKGROUND TASK while engineer reviews appraisal at PAUSE #1.
    Uses 2 concurrent Azure OpenAI workers for ~50% faster processing.

    When complete:
    - Saves rooms/objects to Firestore
    - Sets roomsReady=True flag
    - When engineer approves appraisal AND roomsReady → PAUSE #2

    Timing logs:
    - [TIMING] Vision analysis: Xs (N images, 2 workers, avg Xs/image)
    - [TIMING] Room enrichment: Xs (N rooms)
    - [TIMING] Total analyze_rooms: Xs
    """
    stage_start = time.time()

    tracer = get_tracer()
    writeback = FirestoreWriteback()
    client = FirestoreClient()

    with tracer.span("analyze_rooms"):
        rooms = state.get("rooms", [])
        objects = state.get("objects", [])

        # If no rooms exist, analyze images to create them
        if not rooms:
            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="analyzing_rooms",
                to_status="analyzing_rooms",
                stage_summary={"action": "starting_vision_analysis"},
            )

            # Get uploaded files from Firestore
            study = client.get_study(state["study_id"])
            uploaded_files = study.get("uploadedFiles", []) if study else []

            if uploaded_files:
                # Filter to image files only
                image_files = [
                    f for f in uploaded_files
                    if f.get("type", "").startswith("image/")
                ]

                vision_start = time.time()

                # Analyze images with GPT-4 Vision (2 CONCURRENT WORKERS)
                property_name = state.get("property_name", "")
                analyzed_rooms, analyzed_objects = await analyze_study_images(
                    uploaded_files=image_files,
                    property_name=property_name,
                    max_concurrent=2,  # 2 parallel Azure OpenAI calls (3 hits rate limits)
                )

                vision_elapsed = time.time() - vision_start
                avg_per_image = vision_elapsed / len(image_files) if image_files else 0
                logger.info(
                    f"[TIMING] Vision analysis: {vision_elapsed:.1f}s "
                    f"({len(image_files)} images, 2 workers, avg {avg_per_image:.1f}s/image)"
                )

                # Save the analyzed rooms and objects to Firestore
                if analyzed_rooms or analyzed_objects:
                    client.update_study(state["study_id"], {
                        "rooms": analyzed_rooms,
                        "objects": analyzed_objects,
                    })

                rooms = analyzed_rooms
                objects = analyzed_objects

                tracer.log_workflow_transition(
                    study_id=state["study_id"],
                    from_status="vision_analysis",
                    to_status="enriching_rooms",
                    stage_summary={
                        "images_analyzed": len(image_files),
                        "rooms_detected": len(rooms),
                        "objects_detected": len(objects),
                        "vision_elapsed_s": vision_elapsed,
                    },
                )

        # Build context with available documents
        context = _build_stage_context(state)
        evidence_pack = state.get("evidence_pack", [])

        # PARALLEL: Enrich all rooms with IRS context concurrently
        enrich_start = time.time()
        enriched_rooms = await enrich_rooms_batch(
            rooms=rooms,
            context=context,
            max_concurrent=2,  # 2 concurrent workers (3 hits rate limits)
        )
        enrich_elapsed = time.time() - enrich_start
        logger.info(f"[TIMING] Room enrichment: {enrich_elapsed:.1f}s ({len(rooms)} rooms)")

        # Collect citations from all enriched rooms
        for room in enriched_rooms:
            evidence_pack.extend(room.get("citations", []))

        # NOTE: This node runs as background task, so we don't advance workflow here
        # The resource_extraction_node will set roomsReady=True when this completes

        stage_elapsed = time.time() - stage_start
        logger.info(f"[TIMING] Total analyze_rooms: {stage_elapsed:.1f}s")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="analyzing_rooms",
            to_status="rooms_ready",
            stage_summary={
                "total_rooms": len(enriched_rooms),
                "total_objects": len(objects),
                "stage_elapsed_s": stage_elapsed,
            },
        )

        return {
            **state,
            "current_stage": "analyzing_rooms",
            "rooms": enriched_rooms,
            "objects": objects,
            "evidence_pack": evidence_pack,
            "rooms_ready": True,  # Signal that analysis is complete
        }


async def resource_extraction_node(state: WorkflowState) -> WorkflowState:
    """
    Extract and structure appraisal/resource data using the ingestion pipeline.

    PARALLEL WORKFLOW:
    1. Ingest appraisal PDF (fast, ~30s) using same pipeline as IRS/RSMeans
    2. Extract structured fields from appraisal
    3. Start analyze_rooms as BACKGROUND TASK
    4. PAUSE #1 - engineer reviews appraisal while vision runs in background

    After approval, checks if rooms_ready:
    - If yes: transitions to reviewing_rooms (PAUSE #2)
    - If no: waits for analyze_rooms to complete
    """
    import time
    import tempfile
    import urllib.request
    import ssl
    from pathlib import Path

    tracer = get_tracer()
    writeback = FirestoreWriteback()
    client = FirestoreClient()

    stage_start = time.time()

    with tracer.span("resource_extraction"):
        # Get study data
        study = client.get_study(state["study_id"])
        uploaded_files = study.get("uploadedFiles", []) if study else []
        appraisal_resources = study.get("appraisalResources", {}) if study else {}

        # =================================================================
        # STEP 1: Ingest appraisal PDF (if not already done)
        # =================================================================
        if not appraisal_resources:
            ingest_start = time.time()

            # Find appraisal PDF from uploaded files
            appraisal_files = [
                f for f in uploaded_files
                if f.get("type", "").lower() == "application/pdf"
                or f.get("name", "").lower().endswith(".pdf")
            ]

            if appraisal_files:
                appraisal_file = appraisal_files[0]  # Use first PDF as appraisal
                download_url = appraisal_file.get("downloadURL")

                if download_url:
                    try:
                        # Download PDF to temp file
                        ctx = ssl.create_default_context()
                        req = urllib.request.Request(
                            download_url,
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        with urllib.request.urlopen(req, context=ctx, timeout=60) as response:
                            pdf_data = response.read()

                        # Write to temp file
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                            tmp.write(pdf_data)
                            pdf_path = Path(tmp.name)

                        # Import ingestion pipeline
                        from evidence_layer.src.ingest import ingest_document
                        from evidence_layer.src.manifest import Corpus, DocType
                        from evidence_layer.src.extract_fields import extract_appraisal_fields
                        from evidence_layer.src.map_appraisal_sections import map_appraisal_tables_to_sections

                        # Run ingestion pipeline (study-scoped)
                        doc_id = f"{state['study_id']}_appraisal"
                        ingest_result = ingest_document(
                            pdf_path=pdf_path,
                            corpus=Corpus.STUDY,
                            doc_type=DocType.APPRAISAL,
                            study_id=state["study_id"],
                            verbose=True,
                        )

                        # Extract structured fields (regex-based, used as fallback)
                        appraisal_fields = extract_appraisal_fields(pdf_path, doc_id)
                        fields_dict = appraisal_fields.to_dict()

                        # Get tables path for tiered extraction
                        tables_path = ingest_result.data_dir / "structured" / f"{ingest_result.doc_id}.tables.jsonl"
                        logger.debug(f"Looking for tables at: {tables_path}")
                        logger.debug(f"Tables file exists: {tables_path.exists()}")

                        # Try AGENTIC extraction (multi-agent with self-correction)
                        try:
                            from ..agents.appraisal import run_appraisal_extraction
                            from ..agents.base_agent import StageContext

                            extraction_context = StageContext(
                                study_id=state["study_id"],
                                property_name=state.get("property_name"),
                                reference_doc_ids=state.get("reference_doc_ids", []),
                                study_doc_ids=state.get("study_doc_ids", []),
                            )

                            extraction_output = await run_appraisal_extraction(
                                study_id=state["study_id"],
                                pdf_path=str(pdf_path),
                                context=extraction_context,
                                mismo_xml=None,  # TODO: Support MISMO XML upload
                                tables_path=str(tables_path) if tables_path.exists() else None,
                                max_iterations=2,
                            )

                            sections = extraction_output["extraction_result"]
                            extraction_audit = extraction_output["audit_trail"]
                            logger.debug(
                                f"Agentic extraction: confidence={extraction_output['overall_confidence']:.2f}, "
                                f"needs_review={extraction_output['needs_review']}, "
                                f"iterations={extraction_audit.get('iterations', 0)}"
                            )

                            # Phase 4: Supplement with DocumentExtractionAgent for additional fields
                            try:
                                doc_extraction_result = await extract_document_fields(
                                    pdf_path=str(pdf_path),
                                    context=extraction_context,
                                    field_hints=[
                                        "property_address",
                                        "appraised_value",
                                        "land_value",
                                        "building_value",
                                        "year_built",
                                        "gross_building_area",
                                        "effective_age",
                                    ],
                                )

                                # Merge high-confidence extractions into existing fields
                                if doc_extraction_result.get("overall_confidence", 0) > 0.7:
                                    for field in doc_extraction_result.get("fields", []):
                                        if field.get("confidence", 0) > 0.8 and field.get("value"):
                                            field_name = field.get("field_name")
                                            # Add to fields_dict if not already present or higher confidence
                                            if field_name not in fields_dict or fields_dict.get(field_name) is None:
                                                fields_dict[field_name] = field.get("value")

                                    extraction_audit["document_extraction_agent"] = {
                                        "used": True,
                                        "confidence": doc_extraction_result.get("overall_confidence", 0),
                                        "fields_added": len(doc_extraction_result.get("fields", [])),
                                    }
                                    logger.debug(
                                        f"DocumentExtractionAgent supplemented extraction: "
                                        f"confidence={doc_extraction_result.get('overall_confidence', 0):.2f}"
                                    )

                            except Exception as doc_err:
                                logger.debug(f"DocumentExtractionAgent skipped: {doc_err}")
                                extraction_audit["document_extraction_agent"] = {
                                    "used": False,
                                    "error": str(doc_err),
                                }

                        except Exception as tier_err:
                            logger.warning(f"Agentic extraction failed, falling back to regex: {tier_err}")
                            # Fall back to regex-only extraction
                            sections = map_appraisal_tables_to_sections(
                                tables_path=tables_path,
                                fallback_fields=fields_dict,
                            )
                            extraction_audit = {"error": str(tier_err), "fallback": "regex"}

                        logger.debug(f"Mapped sections: {list(sections.keys())}")

                        # Convert to dict for Firestore
                        # Include both flat fields (backward compat) AND rich sections (for UI)
                        appraisal_resources = {
                            "doc_id": doc_id,
                            "ingested": True,
                            "num_chunks": ingest_result.num_chunks,
                            "num_tables": ingest_result.num_tables,
                            "fields": fields_dict,  # Flat extraction for backward compat
                            "_extraction_audit": extraction_audit,  # Audit trail for IRS defensibility
                            **sections,  # Rich sections: subject, listing_and_contract, etc.
                        }

                        # Clean up temp file
                        pdf_path.unlink(missing_ok=True)

                        ingest_elapsed = time.time() - ingest_start
                        logger.info(
                            f"Appraisal ingestion: {ingest_elapsed:.1f}s "
                            f"({ingest_result.num_chunks} chunks, {ingest_result.num_tables} tables)"
                        )

                    except Exception as e:
                        logger.error(f"Error ingesting appraisal: {e}")
                        # Fall back to empty structure
                        appraisal_resources = {
                            "error": str(e),
                            "ingested": False,
                        }

            # If still no resources, create empty structure
            if not appraisal_resources:
                appraisal_resources = {
                    "ingested": False,
                    "note": "No appraisal PDF found in uploaded files",
                }

            # Save to Firestore
            client.update_study(state["study_id"], {
                "appraisalResources": appraisal_resources,
            })

        # =================================================================
        # STEP 2: Enqueue analyze_rooms as DURABLE BACKGROUND JOB
        # =================================================================
        # Phase 5: Use durable job queue instead of fire-and-forget asyncio.create_task
        # Jobs survive server restarts, support retry logic, and provide progress tracking
        from ..firestore.job_queue import JobQueue

        job_queue = JobQueue()
        job_id = await job_queue.enqueue(
            job_type="analyze_rooms",
            study_id=state["study_id"],
            input_data={
                "user_id": state.get("user_id", ""),
                "property_name": state.get("property_name", ""),
                "rooms": state.get("rooms", []),
                "objects": state.get("objects", []),
                "reference_doc_ids": state.get("reference_doc_ids", []),
                "study_doc_ids": state.get("study_doc_ids", []),
            },
            timeout_seconds=600,  # 10 minute timeout for vision analysis
            max_retries=2,
            priority=3,  # High priority
        )
        logger.info(f"Enqueued analyze_rooms job {job_id} for study {state['study_id']}")

        # =================================================================
        # STEP 3: Advance to PAUSE #1 (resource_extraction)
        # =================================================================
        writeback.advance_workflow(state["study_id"], "resource_extraction")

        stage_elapsed = time.time() - stage_start
        logger.info(f"Total resource_extraction: {stage_elapsed:.1f}s")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="uploading_documents",
            to_status="resource_extraction",
            stage_summary={
                "appraisal_ingested": appraisal_resources.get("ingested", False),
                "background_task": "analyze_rooms_started",
                "elapsed_seconds": stage_elapsed,
            },
        )

        # PAUSE #1 - Engineer reviews appraisal while vision job runs in background
        return {
            **state,
            "current_stage": "resource_extraction",
            "appraisal_resources": appraisal_resources,
            "needs_review": True,
            "rooms_ready": False,  # Will be set to True when job completes
            "pending_jobs": [job_id],  # Phase 5: Track pending jobs for status checks
        }


async def reviewing_rooms_node(state: WorkflowState) -> WorkflowState:
    """
    Room review checkpoint (PAUSE #2).

    Engineer reviews and approves room classifications.
    After approval, continues to process_assets.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("reviewing_rooms"):
        # This node just sets up the pause state
        # The actual room data is already in state from analyze_rooms_node
        writeback.advance_workflow(state["study_id"], "reviewing_rooms")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="resource_extraction",
            to_status="reviewing_rooms",
        )

        return {
            **state,
            "current_stage": "reviewing_rooms",
            "needs_review": True,  # PAUSE for engineer to approve rooms
        }


async def process_assets_node(state: WorkflowState) -> WorkflowState:
    """
    Combined asset processing node.

    Runs ALL of these together (no pause between):
    1. Object detection + enrichment
    2. Takeoff calculation + IRS classification (PARALLEL)
    3. Cost estimation with RSMeans

    Timing logs:
    - [TIMING] Object enrichment: Xs (N objects)
    - [TIMING] Takeoffs + Classification (parallel): Xs
    - [TIMING] Cost estimation: Xs
    - [TIMING] Total process_assets: Xs

    After processing, transitions to engineering_takeoff (PAUSE #3).
    """
    stage_start = time.time()

    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("process_assets"):
        context = _build_stage_context(state)
        objects = state.get("objects", [])
        rooms = state.get("rooms", [])
        evidence_pack = state.get("evidence_pack", [])

        # Build a room lookup map for quick access
        rooms_by_id = {r.get("id"): r for r in rooms if r.get("id")}

        # Get default room context (fallback for objects without room_id)
        default_room = rooms[0] if rooms else None
        default_room_type = default_room.get("room_type") if default_room else None

        # =====================================================================
        # STEP 1: Enrich objects with IRS context (per-object room context)
        # =====================================================================
        enriched_objects = []
        if objects:
            enrich_start = time.time()
            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="processing_assets",
                to_status="processing_assets",
                stage_summary={"step": "enriching_objects", "count": len(objects)},
            )

            # Add per-object room_type for enrichment
            objects_with_room_context = []
            for obj in objects:
                room = _get_room_for_object(obj, rooms)
                obj_room_type = room.get("room_type") if room else default_room_type
                # Pass room_type with each object for context
                obj_copy = {**obj, "room_type": obj_room_type}
                objects_with_room_context.append(obj_copy)

            enriched_objects = await enrich_objects_batch(
                detections=objects_with_room_context,
                context=context,
                room_type=default_room_type,  # Still pass default for backward compat
            )

            enrich_elapsed = time.time() - enrich_start
            logger.info(f"[TIMING] Object enrichment: {enrich_elapsed:.1f}s ({len(objects)} objects)")

            for obj in enriched_objects:
                evidence_pack.extend(obj.get("citations", []))

        # =====================================================================
        # STEP 2 & 3: Calculate takeoffs AND IRS classification IN PARALLEL!
        # These two operations are independent - they both depend on enriched
        # objects but not on each other, so we run them concurrently.
        # =====================================================================
        takeoffs = []
        asset_classifications = []

        if enriched_objects:
            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="processing_assets",
                to_status="processing_assets",
                stage_summary={
                    "step": "takeoffs_and_classification_parallel",
                    "count": len(enriched_objects),
                },
            )

            # Build component list from objects for takeoffs (per-object room context)
            components = []
            for obj in enriched_objects:
                obj_context = obj.get("context", {})
                component_name = obj_context.get("component_name") if obj_context else None
                if not component_name:
                    component_name = obj.get("original_label", obj.get("label", "unknown"))

                # Get per-object room context
                room = _get_room_for_object(obj, rooms)
                obj_room_type = room.get("room_type") if room else default_room_type
                obj_room_context = room.get("context", {}) if room else {}
                obj_room_area_sf = obj_room_context.get("room_area_sf") if obj_room_context else None

                components.append({
                    "component_name": component_name,
                    "detection_count": 1,
                    "room_type": obj_room_type,
                    "room_area_sf": obj_room_area_sf,
                })

            # PARALLEL: Run takeoffs and classification at the same time!
            # Note: room_type and room_area_sf are now per-component in the components list
            default_room_context = default_room.get("context", {}) if default_room else {}
            default_room_area_sf = default_room_context.get("room_area_sf") if default_room_context else None

            parallel_start = time.time()
            takeoffs, asset_classifications = await asyncio.gather(
                calculate_takeoffs_batch(
                    components=components,
                    context=context,
                    room_type=default_room_type,  # Fallback for components without room context
                    room_area_sf=default_room_area_sf,  # Fallback for components without room context
                    max_concurrent=2,  # 2 concurrent workers
                ),
                classify_components_batch(
                    components=enriched_objects,
                    context=context,
                    max_concurrent=2,  # 2 concurrent workers
                ),
            )
            parallel_elapsed = time.time() - parallel_start
            logger.info(f"[TIMING] Takeoffs + Classification (parallel): {parallel_elapsed:.1f}s")

            # Collect citations from both
            for takeoff in takeoffs:
                evidence_pack.extend(takeoff.get("citations", []))

            for item in asset_classifications:
                evidence_pack.extend(item.get("citations", []))

                tracer.log_classification(
                    component=item.get("component", ""),
                    classification=item.get("classification", {}),
                    num_citations=len(item.get("citations", [])),
                    confidence=item.get("confidence", 0),
                    needs_review=item.get("needs_review", False),
                    study_id=state["study_id"],
                )

            # =====================================================================
            # STEP 3.5: Verify classifications for IRS defensibility
            # =====================================================================
            if asset_classifications:
                verify_start = time.time()
                tracer.log_workflow_transition(
                    study_id=state["study_id"],
                    from_status="processing_assets",
                    to_status="processing_assets",
                    stage_summary={
                        "step": "verifying_classifications",
                        "count": len(asset_classifications),
                    },
                )

                verification_results = await verify_classifications_batch(
                    classifications=asset_classifications,
                    components=enriched_objects,
                    context=context,
                    max_concurrent=5,
                )

                # Merge verification results into classifications
                for i, verification in enumerate(verification_results):
                    if i < len(asset_classifications):
                        # Update needs_review flag if verification found issues
                        if verification.get("needs_review"):
                            asset_classifications[i]["needs_review"] = True
                            existing_reason = asset_classifications[i].get("review_reason", "")
                            new_reason = verification.get("review_reason", "")
                            if new_reason and new_reason not in (existing_reason or ""):
                                asset_classifications[i]["review_reason"] = (
                                    f"{existing_reason}; {new_reason}" if existing_reason else new_reason
                                )

                        # Update confidence if verification adjusted it
                        if verification.get("adjusted_confidence") is not None:
                            original_conf = asset_classifications[i].get("confidence", 0.5)
                            verified_conf = verification.get("adjusted_confidence", original_conf)
                            # Use the lower of the two confidences
                            asset_classifications[i]["confidence"] = min(original_conf, verified_conf)

                        # Add verification metadata
                        asset_classifications[i]["verification"] = {
                            "is_valid": verification.get("is_valid", True),
                            "issues": verification.get("issues", []),
                            "suggestions": verification.get("suggestions", []),
                        }

                verify_elapsed = time.time() - verify_start
                flagged_count = sum(1 for v in verification_results if v.get("needs_review"))
                logger.info(
                    f"[TIMING] Classification verification: {verify_elapsed:.1f}s "
                    f"({len(asset_classifications)} verified, {flagged_count} flagged)"
                )

        # =====================================================================
        # STEP 4: Cost estimation
        # =====================================================================
        cost_estimates = []
        cost_summary = {}
        if takeoffs:
            cost_start = time.time()
            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="processing_assets",
                to_status="processing_assets",
                stage_summary={"step": "estimating_costs", "count": len(takeoffs)},
            )

            # Build takeoff data for cost estimation
            takeoff_data = []
            for takeoff in takeoffs:
                takeoff_result = takeoff.get("takeoff", {})
                if takeoff_result:
                    takeoff_data.append({
                        "component_name": takeoff_result.get("component_name", takeoff.get("component_name", "")),
                        "quantity": takeoff_result.get("quantity", 1),
                        "unit": takeoff_result.get("unit", "EA"),
                    })

            cost_estimates = await estimate_costs_batch(
                takeoffs=takeoff_data,
                context=context,
                quality_tier="standard",
                location_factor=1.0,
                year_factor=1.0,
            )

            cost_summary = aggregate_costs(cost_estimates)
            cost_elapsed = time.time() - cost_start
            logger.info(f"[TIMING] Cost estimation: {cost_elapsed:.1f}s ({len(takeoff_data)} items)")

            for estimate in cost_estimates:
                evidence_pack.extend(estimate.get("citations", []))

        # =====================================================================
        # STEP 5: Cross-validation across stages (Phase 5)
        # =====================================================================
        # Validates consistency between classification, takeoff, and cost data
        from ..validation.cross_validator import CrossValidator
        from ..config.settings import get_settings

        settings = get_settings()
        cross_validation_enabled = getattr(settings, "cross_validation_enabled", True)

        if cross_validation_enabled and asset_classifications and takeoffs and cost_estimates:
            validation_start = time.time()
            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="processing_assets",
                to_status="processing_assets",
                stage_summary={"step": "cross_validation", "count": len(asset_classifications)},
            )

            validator = CrossValidator()
            validation_results = validator.validate_all(
                classifications=asset_classifications,
                takeoffs=takeoffs,
                costs=cost_estimates,
            )

            # Merge validation results into classifications
            for i, validation in enumerate(validation_results):
                if i < len(asset_classifications):
                    # Add cross-validation results
                    asset_classifications[i]["cross_validation"] = validation.to_dict()

                    # Flag for review if warnings found
                    if validation.has_warnings:
                        asset_classifications[i]["needs_review"] = True
                        existing_reason = asset_classifications[i].get("review_reason", "")
                        warning_reasons = [
                            issue.message
                            for issue in validation.issues
                            if issue.severity.value == "warning"
                        ]
                        if warning_reasons:
                            new_reason = "; ".join(warning_reasons[:2])  # Limit to first 2
                            if new_reason not in (existing_reason or ""):
                                asset_classifications[i]["review_reason"] = (
                                    f"{existing_reason}; {new_reason}" if existing_reason else new_reason
                                )

            validation_elapsed = time.time() - validation_start
            total_warnings = sum(v.warning_count for v in validation_results)
            total_errors = sum(v.error_count for v in validation_results)
            logger.info(
                f"[TIMING] Cross-validation: {validation_elapsed:.1f}s "
                f"({len(validation_results)} components, {total_warnings} warnings, {total_errors} errors)"
            )

        # =====================================================================
        # Write all results to Firestore
        # =====================================================================
        writeback.update_objects_with_classifications(state["study_id"], asset_classifications)
        writeback.update_study_with_costs(state["study_id"], cost_estimates, cost_summary)
        writeback.advance_workflow(state["study_id"], "engineering_takeoff")

        stage_elapsed = time.time() - stage_start
        logger.info(f"[TIMING] Total process_assets: {stage_elapsed:.1f}s")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="processing_assets",
            to_status="engineering_takeoff",
            stage_summary={
                "objects_enriched": len(enriched_objects),
                "takeoffs_calculated": len(takeoffs),
                "assets_classified": len(asset_classifications),
                "costs_estimated": len(cost_estimates),
                "total_cost": cost_summary.get("total_cost", 0),
                "stage_elapsed_s": stage_elapsed,
            },
        )

        # Transition to engineering_takeoff (PAUSE #3)
        return {
            **state,
            "current_stage": "engineering_takeoff",
            "objects": enriched_objects,
            "takeoffs": takeoffs,
            "asset_classifications": asset_classifications,
            "cost_estimates": cost_estimates,
            "evidence_pack": evidence_pack,
            "needs_review": True,  # PAUSE for engineer to review all asset data
        }


async def complete_workflow_node(state: WorkflowState) -> WorkflowState:
    """
    Mark workflow as complete.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("complete_workflow"):
        writeback.advance_workflow(state["study_id"], "completed")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status=state.get("current_stage", "unknown"),
            to_status="completed",
        )

        return {
            **state,
            "current_stage": "completed",
            "needs_review": False,
        }


async def error_handler_node(state: WorkflowState) -> WorkflowState:
    """
    Handle errors during workflow execution.
    """
    tracer = get_tracer()

    error = state.get("last_error", "Unknown error")
    tracer.log_error(
        Exception(error),
        context={"study_id": state["study_id"], "stage": state.get("current_stage")},
    )

    return {
        **state,
        "errors": [*state.get("errors", []), {"error": error, "stage": state.get("current_stage")}],
    }
