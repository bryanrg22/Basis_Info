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
from ..evidence.aggregator import EvidenceAggregator
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
from ..observability.alerts import send_alert, send_workflow_completion_alert
from ..observability.trace_analyzer import save_trace_to_firestore
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


def _azure_di_to_sections(azure_result, fallback_fields: dict) -> dict:
    """
    Convert Azure DI ExtractionResult to frontend sections format.

    Args:
        azure_result: ExtractionResult from AzureDocumentExtractor
        fallback_fields: Regex-extracted fields for fallback

    Returns:
        Dict with section keys matching AppraisalResources TypeScript interface
    """
    sections = {
        "subject": {},
        "listing_and_contract": {},
        "neighborhood": {},
        "site": {},
        "improvements": {"general": {}, "exterior": {}, "interior_mechanical": {}},
        "sales_comparison": {"market_stats": {}, "subject": {}, "comparables": []},
        "cost_approach": {},
        "reconciliation": {},
        "photos": [],
        "sketch": {"areas": [], "basement_layout": []},
    }

    # Map Azure DI sections to our format
    if hasattr(azure_result, 'sections') and azure_result.sections:
        for section_name, fields in azure_result.sections.items():
            if section_name in sections:
                if isinstance(sections[section_name], dict):
                    for field_name, field_result in fields.items():
                        value = field_result.value if hasattr(field_result, 'value') else field_result
                        # Handle nested sections like improvements
                        if section_name == "improvements":
                            # Map to appropriate sub-section
                            if field_name in ["year_built", "effective_age", "gla_sqft", "bedrooms", "bathrooms", "stories"]:
                                sections["improvements"]["general"][field_name] = value
                            elif field_name in ["foundation", "exterior_walls", "roof"]:
                                sections["improvements"]["exterior"][field_name] = value
                            else:
                                sections["improvements"]["interior_mechanical"][field_name] = value
                        elif section_name == "sales_comparison" and field_name.startswith("comparable_"):
                            # Handle comparables list
                            sections["sales_comparison"]["comparables"].append(value)
                        else:
                            sections[section_name][field_name] = value

    # Apply fallback values where Azure DI didn't extract
    if fallback_fields:
        # Subject fallbacks
        if not sections["subject"].get("property_address") and fallback_fields.get("property_address"):
            sections["subject"]["property_address"] = fallback_fields["property_address"]
        if not sections["subject"].get("city") and fallback_fields.get("city"):
            sections["subject"]["city"] = fallback_fields["city"]
        if not sections["subject"].get("state") and fallback_fields.get("state"):
            sections["subject"]["state"] = fallback_fields["state"]
        if not sections["subject"].get("zip") and fallback_fields.get("zip_code"):
            sections["subject"]["zip"] = fallback_fields["zip_code"]
        if not sections["subject"].get("county") and fallback_fields.get("county"):
            sections["subject"]["county"] = fallback_fields["county"]

        # Improvements fallbacks
        general = sections["improvements"]["general"]
        if not general.get("year_built") and fallback_fields.get("year_built"):
            general["year_built"] = fallback_fields["year_built"]
        if not general.get("gla_sqft") and fallback_fields.get("gross_living_area"):
            general["gla_sqft"] = fallback_fields["gross_living_area"]
        if not general.get("bedrooms") and fallback_fields.get("bedroom_count"):
            general["bedrooms"] = fallback_fields["bedroom_count"]
        if not general.get("bathrooms") and fallback_fields.get("bathroom_count"):
            general["bathrooms"] = fallback_fields["bathroom_count"]

        # Cost approach fallbacks
        if not sections["cost_approach"].get("site_value") and fallback_fields.get("land_value"):
            sections["cost_approach"]["site_value"] = fallback_fields["land_value"]

        # Reconciliation fallbacks
        if not sections["reconciliation"].get("final_market_value") and fallback_fields.get("total_value"):
            sections["reconciliation"]["final_market_value"] = fallback_fields["total_value"]

    return sections


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
    Load study data from Firestore and start background vision analysis.
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

        user_id = study.get("userId", "")
        property_name = study.get("propertyName", "")
        rooms = study.get("rooms", [])
        objects = study.get("objects", [])

        # =================================================================
        # Enqueue analyze_rooms IMMEDIATELY for true parallel execution
        # This runs while resource_extraction handles the appraisal PDF
        # =================================================================
        job_id = None
        uploaded_files = study.get("uploadedFiles", [])
        image_files = [f for f in uploaded_files if f.get("type", "").startswith("image/")]

        if image_files and not rooms:  # Only if images exist and rooms not processed
            from ..firestore.job_queue import JobQueue

            job_queue = JobQueue()
            job_id = await job_queue.enqueue(
                job_type="analyze_rooms",
                study_id=state["study_id"],
                input_data={
                    "user_id": user_id,
                    "property_name": property_name,
                    "rooms": rooms,
                    "objects": objects,
                    "reference_doc_ids": state.get("reference_doc_ids", []),
                    "study_doc_ids": state.get("study_doc_ids", []),
                },
                timeout_seconds=600,
                max_retries=2,
                priority=3,
            )
            logger.info(f"Enqueued analyze_rooms job {job_id} for study {state['study_id']} ({len(image_files)} images)")

        return {
            **state,
            "user_id": user_id,
            "property_name": property_name,
            "current_stage": study.get("workflowStatus", "uploading_documents"),
            "rooms": rooms,
            "objects": objects,
            "takeoffs": study.get("takeoffs", []),
            "appraisal_resources": study.get("appraisalResources", {}),
            "pending_jobs": [job_id] if job_id else [],
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

        # PARALLEL: Enrich all rooms with IRS context (static lookup by default)
        enrich_start = time.time()
        enriched_rooms = await enrich_rooms_batch(
            rooms=rooms,
            context=context,
            max_concurrent=2,  # 2 concurrent workers (only used if use_static=False)
            use_static=True,  # Phase 1 optimization: static lookup, no LLM
        )
        enrich_elapsed = time.time() - enrich_start
        logger.info(f"[TIMING] Room enrichment (static): {enrich_elapsed:.3f}s ({len(rooms)} rooms)")

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

                        # =============================================================
                        # EXTRACTION METHOD OPTIONS:
                        # 1. Azure DI (recommended) - High quality, no LLM calls
                        # 2. Multi-agent LLM - Expensive, slow, error-prone (disabled)
                        # 3. pdfplumber fallback - Free but lower quality
                        # =============================================================
                        SKIP_AGENTIC_EXTRACTION = True  # Skip multi-agent LLM loop
                        USE_AZURE_DI = True  # Use Azure Document Intelligence directly

                        # Try Azure DI first (high quality, no LLM)
                        azure_di_succeeded = False
                        if USE_AZURE_DI:
                            try:
                                from evidence_layer.src.tiered_extraction.azure_di_extractor import AzureDocumentExtractor

                                azure_extractor = AzureDocumentExtractor()
                                if azure_extractor.is_available():
                                    logger.info(f"Using Azure DI for extraction (study {state['study_id']})")

                                    # Run Azure DI extraction
                                    azure_result = await azure_extractor.extract(str(pdf_path))

                                    if azure_result and azure_result.overall_confidence > 0.3:
                                        # Convert Azure DI result to sections format
                                        sections = _azure_di_to_sections(azure_result, fields_dict)
                                        extraction_audit = {
                                            "method": "azure_di_direct",
                                            "confidence": azure_result.overall_confidence,
                                            "needs_review": azure_result.needs_review,
                                            "sources_used": azure_result.sources_used,
                                        }
                                        azure_di_succeeded = True
                                        logger.info(
                                            f"Azure DI extraction complete: confidence={azure_result.overall_confidence:.2f}, "
                                            f"needs_review={azure_result.needs_review}"
                                        )
                                    else:
                                        logger.warning(f"Azure DI extraction low confidence, falling back to table mapping")
                                else:
                                    logger.info("Azure DI not configured, falling back to table mapping")

                            except Exception as azure_err:
                                logger.warning(f"Azure DI extraction failed: {azure_err}, falling back to table mapping")

                        # Fall back to table mapping if Azure DI didn't succeed
                        if not azure_di_succeeded:
                            logger.info(f"Using direct table extraction for study {state['study_id']}")
                            sections = map_appraisal_tables_to_sections(
                                tables_path=tables_path,
                                fallback_fields=fields_dict,
                            )
                            extraction_audit = {
                                "method": "direct_table_mapping",
                                "azure_di_attempted": USE_AZURE_DI,
                                "tables_path": str(tables_path),
                                "tables_exist": tables_path.exists(),
                            }

                        if not SKIP_AGENTIC_EXTRACTION:
                            # Try AGENTIC extraction (multi-agent with self-correction)
                            try:
                                from ..agents.appraisal import run_appraisal_extraction
                                from ..agents.appraisal.tools import set_extraction_context
                                from ..agents.base_agent import StageContext

                                logger.info(f"Starting agentic extraction for study {state['study_id']}")

                                # Set context for tool-level alerting
                                set_extraction_context(state["study_id"])

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

                                # ALERT: Agentic extraction failed - send immediately before fallback
                                # This catches missing packages, rate limits, validation errors, etc.
                                error_message = str(tier_err)
                                error_type_name = type(tier_err).__name__

                                # Categorize the error for better alerting
                                if "not installed" in error_message.lower() or "package" in error_message.lower():
                                    alert_type = "EXTRACTION_PACKAGE_MISSING"
                                    severity = "error"
                                elif "429" in error_message or "rate" in error_message.lower():
                                    alert_type = "EXTRACTION_RATE_LIMITED"
                                    severity = "warning"
                                elif "validation" in error_message.lower() or "pydantic" in error_type_name.lower():
                                    alert_type = "EXTRACTION_VALIDATION_ERROR"
                                    severity = "error"
                                else:
                                    alert_type = "EXTRACTION_AGENTIC_FAILED"
                                    severity = "error"

                                await send_alert(
                                    study_id=state["study_id"],
                                    stage="resource_extraction",
                                    error_type=alert_type,
                                    error_message=f"Agentic extraction failed, falling back to regex: {error_message[:500]}",
                                    severity=severity,
                                    context={
                                        "exception_type": error_type_name,
                                        "fallback": "regex",
                                    },
                                )

                                # Fall back to regex-only extraction
                                sections = map_appraisal_tables_to_sections(
                                    tables_path=tables_path,
                                    fallback_fields=fields_dict,
                                )
                                extraction_audit = {"error": str(tier_err), "fallback": "regex"}
                        else:
                            # =============================================================
                            # DIRECT EXTRACTION: Skip LLM, use table mapping directly
                            # This is fast (~2-3s), free, and reliable
                            # =============================================================
                            logger.info(f"Using direct table extraction for study {state['study_id']} (multi-agent skipped)")

                            sections = map_appraisal_tables_to_sections(
                                tables_path=tables_path,
                                fallback_fields=fields_dict,
                            )
                            extraction_audit = {
                                "method": "direct_table_mapping",
                                "agentic_skipped": True,
                                "tables_path": str(tables_path),
                                "tables_exist": tables_path.exists(),
                            }

                        logger.debug(f"Mapped sections: {list(sections.keys())}")

                        # Convert to dict for Firestore
                        # IMPORTANT: Ensure ALL required sections exist with defaults
                        # This prevents frontend "entryCSSFiles" and type errors
                        appraisal_resources = {
                            # Metadata
                            "doc_id": doc_id,
                            "ingested": True,
                            "num_chunks": ingest_result.num_chunks,
                            "num_tables": ingest_result.num_tables,
                            "fields": fields_dict,  # Flat extraction for backward compat
                            "_extraction_audit": extraction_audit,  # Audit trail for IRS defensibility
                            # Required sections with defaults (match AppraisalResources TypeScript interface)
                            "subject": sections.get("subject", {}),
                            "listing_and_contract": sections.get("listing_and_contract", {}),
                            "neighborhood": sections.get("neighborhood", {}),
                            "site": sections.get("site", {}),
                            "improvements": sections.get("improvements", {
                                "general": {},
                                "exterior": {},
                                "interior_mechanical": {},
                            }),
                            "sales_comparison": sections.get("sales_comparison", {
                                "market_stats": {},
                                "subject": {},
                                "comparables": [],
                            }),
                            "cost_approach": sections.get("cost_approach", {}),
                            "reconciliation": sections.get("reconciliation", {}),
                            "photos": sections.get("photos", []),
                            "sketch": sections.get("sketch", {
                                "areas": [],
                                "basement_layout": [],
                            }),
                        }

                        # Clean up temp file
                        pdf_path.unlink(missing_ok=True)

                        ingest_elapsed = time.time() - ingest_start
                        logger.info(
                            f"Appraisal ingestion: {ingest_elapsed:.1f}s "
                            f"({ingest_result.num_chunks} chunks, {ingest_result.num_tables} tables)"
                        )

                        # =============================================================
                        # ALERT: Check extraction quality and tool configuration
                        # (Only applies to agentic extraction, skip for Azure DI / table mapping)
                        # =============================================================
                        extraction_method = extraction_audit.get("method", "")
                        skip_confidence_alerts = extraction_method in ["azure_di_direct", "direct_table_mapping"]
                        if not skip_confidence_alerts:
                            overall_confidence = extraction_audit.get("final_confidence", 0)
                            review_reasons = extraction_audit.get("review_reasons", [])

                            # Alert on critically low confidence
                            if overall_confidence < 0.3:
                                # Check if it's a tool configuration issue
                                tool_config_issue = any(
                                    "not configured" in reason.lower() or
                                    "not extracted" in reason.lower()
                                    for reason in review_reasons
                                )

                                if tool_config_issue:
                                    await send_alert(
                                        study_id=state["study_id"],
                                        stage="resource_extraction",
                                        error_type="EXTRACTION_TOOL_NOT_CONFIGURED",
                                        error_message=(
                                            f"Appraisal extraction failed due to missing tool configuration. "
                                            f"Confidence: {overall_confidence:.0%}. "
                                            f"Review reasons: {'; '.join(review_reasons[:3])}"
                                        ),
                                        severity="error",
                                        context={
                                            "confidence": overall_confidence,
                                            "missing_fields": len(review_reasons),
                                            "duration_ms": extraction_audit.get("duration_ms", 0),
                                            "iterations": extraction_audit.get("iterations", 0),
                                        },
                                    )
                                else:
                                    await send_alert(
                                        study_id=state["study_id"],
                                        stage="resource_extraction",
                                        error_type="EXTRACTION_LOW_CONFIDENCE",
                                        error_message=(
                                            f"Appraisal extraction completed with critically low confidence: "
                                            f"{overall_confidence:.0%}. {len(review_reasons)} fields flagged for review."
                                        ),
                                        severity="warning",
                                        context={
                                            "confidence": overall_confidence,
                                            "flagged_fields": len(review_reasons),
                                            "duration_ms": extraction_audit.get("duration_ms", 0),
                                        },
                                    )

                    except Exception as e:
                        logger.error(f"Error ingesting appraisal: {e}")

                        # ALERT: Ingestion completely failed
                        await send_alert(
                            study_id=state["study_id"],
                            stage="resource_extraction",
                            error_type="INGESTION_FAILED",
                            error_message=f"Appraisal ingestion failed: {str(e)[:500]}",
                            severity="critical",
                            context={
                                "exception_type": type(e).__name__,
                            },
                        )

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
        # STEP 2: Advance to PAUSE #1 (resource_extraction)
        # Note: analyze_rooms job was enqueued in load_study_node for parallel execution
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

        # =================================================================
        # STEP 3: Capture trace and send alert if needed
        # =================================================================
        try:
            # Save trace to Firestore for audit trail
            await save_trace_to_firestore(state["study_id"])

            # Send alert with trace summary if extraction had issues
            overall_confidence = extraction_audit.get("final_confidence", 0) if 'extraction_audit' in locals() else 0
            if overall_confidence < 0.5 or appraisal_resources.get("error"):
                await send_workflow_completion_alert(state["study_id"])
        except Exception as trace_err:
            logger.warning(f"Failed to capture trace: {trace_err}")

        # PAUSE #1 - Engineer reviews appraisal while vision job runs in background
        return {
            **state,
            "current_stage": "resource_extraction",
            "appraisal_resources": appraisal_resources,
            "needs_review": True,
            "rooms_ready": False,  # Will be set to True when job completes
            # pending_jobs already set in load_study_node
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

        # Phase 6: Use EvidenceAggregator instead of simple list
        evidence_aggregator = EvidenceAggregator(study_id=state["study_id"])

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
                use_static=True,  # Phase 1 optimization: static lookup, no LLM
            )

            enrich_elapsed = time.time() - enrich_start
            logger.info(f"[TIMING] Object enrichment (static): {enrich_elapsed:.3f}s ({len(objects)} objects)")

            for obj in enriched_objects:
                # Phase 6: Use evidence aggregator with component context
                obj_id = obj.get("id", obj.get("original_label", "unknown"))
                obj_name = obj.get("original_label", obj.get("label", "unknown"))
                evidence_aggregator.add_citations(
                    citations=obj.get("citations", []),
                    stage="object",
                    component_id=obj_id,
                    component_name=obj_name,
                )
                # Register component for evidence tracking
                evidence_aggregator.register_component(obj_id)

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

            # Phase 2: Get property type for classification cache
            appraisal_resources = state.get("appraisal_resources", {})
            raw_property_type = (
                appraisal_resources.get("propertyType") or
                appraisal_resources.get("subject", {}).get("property_type") or
                "residential"
            ).lower()
            if "commercial" in raw_property_type or "office" in raw_property_type:
                property_type = "commercial"
            else:
                property_type = "residential"

            parallel_start = time.time()
            takeoffs, asset_classifications = await asyncio.gather(
                calculate_takeoffs_batch(
                    components=components,
                    context=context,
                    room_type=default_room_type,  # Fallback for components without room context
                    room_area_sf=default_room_area_sf,  # Fallback for components without room context
                    max_concurrent=2,  # 2 concurrent workers (only used if use_static=False)
                    use_static=True,  # Phase 1 optimization: static calculation, no LLM
                ),
                classify_components_batch(
                    components=enriched_objects,
                    context=context,
                    max_concurrent=2,  # 2 concurrent workers - classification still uses LLM
                    use_cache=True,  # Phase 2: Check verified cache first
                    property_type=property_type,  # Phase 2: For cache key
                ),
            )
            parallel_elapsed = time.time() - parallel_start

            # Phase 2: Log cache hit rate
            cache_hits = sum(1 for c in asset_classifications if c.get("from_cache"))
            cache_misses = len(asset_classifications) - cache_hits
            logger.info(
                f"[TIMING] Takeoffs (static) + Classification: {parallel_elapsed:.1f}s "
                f"({cache_hits}/{len(asset_classifications)} from cache, {cache_misses} LLM calls)"
            )

            # Phase 6: Collect citations using evidence aggregator
            for takeoff in takeoffs:
                takeoff_result = takeoff.get("takeoff", {})
                component_name = takeoff_result.get("component_name", takeoff.get("component_name", "unknown"))
                component_id = takeoff.get("component_id", component_name)
                evidence_aggregator.add_citations(
                    citations=takeoff.get("citations", []),
                    stage="takeoff",
                    component_id=component_id,
                    component_name=component_name,
                )

            for item in asset_classifications:
                component_name = item.get("component", "unknown")
                component_id = item.get("component_id", component_name)
                evidence_aggregator.add_citations(
                    citations=item.get("citations", []),
                    stage="classification",
                    component_id=component_id,
                    component_name=component_name,
                )

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

            # Get state from appraisal for regional cost adjustment
            appraisal_resources = state.get("appraisal_resources", {})
            property_state = (
                appraisal_resources.get("subject", {}).get("state") or
                appraisal_resources.get("fields", {}).get("state") or
                "CA"  # Default to CA
            )

            cost_estimates = await estimate_costs_batch(
                takeoffs=takeoff_data,
                context=context,
                quality_tier="standard",
                use_static=True,  # Phase 1 optimization: static calculation, no LLM
                state=property_state,
                year=2024,
            )

            cost_summary = aggregate_costs(cost_estimates)
            cost_elapsed = time.time() - cost_start
            logger.info(f"[TIMING] Cost estimation (static): {cost_elapsed:.3f}s ({len(takeoff_data)} items)")

            # Phase 6: Collect cost citations using evidence aggregator
            for estimate in cost_estimates:
                component_name = estimate.get("component_name", "unknown")
                component_id = estimate.get("component_id", component_name)
                evidence_aggregator.add_citations(
                    citations=estimate.get("citations", []),
                    stage="cost",
                    component_id=component_id,
                    component_name=component_name,
                )

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

        # Phase 6: Persist evidence pack
        evidence_pack = evidence_aggregator.get_organized_pack()
        writeback.persist_evidence_pack(state["study_id"], evidence_pack)
        logger.info(
            f"[EVIDENCE] Persisted evidence pack: {evidence_pack.total_citations} citations, "
            f"{len(evidence_pack.summary.stages_covered)} stages"
        )

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
            "evidence_pack": evidence_pack.to_firestore_dict(),  # Phase 6: Serialized pack
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

    Phase 6: Sends alerts on workflow errors.
    """
    tracer = get_tracer()
    from ..observability.alerts import send_alert

    error = state.get("last_error", "Unknown error")
    stage = state.get("current_stage", "unknown")

    tracer.log_error(
        Exception(error),
        context={"study_id": state["study_id"], "stage": stage},
    )

    # Phase 6: Send alert for workflow error
    try:
        await send_alert(
            study_id=state["study_id"],
            stage=stage,
            error_type="WORKFLOW_ERROR",
            error_message=error,
            severity="error",
            context={
                "user_id": state.get("user_id", "unknown"),
                "property_name": state.get("property_name", "unknown"),
            },
        )
    except Exception as alert_err:
        logger.warning(f"Failed to send workflow error alert: {alert_err}")

    return {
        **state,
        "errors": [*state.get("errors", []), {"error": error, "stage": stage}],
    }
