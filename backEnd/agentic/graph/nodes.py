"""
LangGraph node functions for workflow stages.

Each node:
1. Gets study data from Firestore
2. Runs the appropriate agent
3. Writes results back to Firestore
4. Returns updated state
"""

from typing import Any

from ..agents.base_agent import StageContext
from ..agents.asset_agent import classify_components_batch
from ..agents.room_agent import enrich_room_context
from ..agents.object_agent import enrich_objects_batch
from ..agents.takeoff_agent import calculate_takeoffs_batch
from ..agents.cost_agent import estimate_costs_batch, aggregate_costs
from ..agents.vision_agent import analyze_study_images
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
        }


async def analyze_rooms_node(state: WorkflowState) -> WorkflowState:
    """
    Analyze rooms from uploaded images.

    If no rooms exist, analyzes uploaded images using GPT-4 Vision to detect
    rooms and objects. Then enriches room classifications with IRS context.
    """
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

            if not uploaded_files:
                # No files to analyze
                writeback.advance_workflow(state["study_id"], "reviewing_rooms")
                return {
                    **state,
                    "current_stage": "reviewing_rooms",
                    "rooms": [],
                    "objects": [],
                    "needs_review": False,
                    "last_error": "No uploaded files found to analyze",
                }

            # Analyze images with GPT-4 Vision
            property_name = state.get("property_name", "")
            analyzed_rooms, analyzed_objects = await analyze_study_images(
                uploaded_files=uploaded_files,
                property_name=property_name,
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
                    "images_analyzed": len(uploaded_files),
                    "rooms_detected": len(rooms),
                    "objects_detected": len(objects),
                },
            )

        # If still no rooms after vision analysis, advance to next stage
        if not rooms:
            writeback.advance_workflow(state["study_id"], "reviewing_rooms")
            return {
                **state,
                "current_stage": "reviewing_rooms",
                "rooms": [],
                "objects": objects,
                "needs_review": False,
            }

        # Build context with available documents
        context = _build_stage_context(state)

        # Enrich each room with IRS context
        enriched_rooms = []
        evidence_pack = state.get("evidence_pack", [])
        review_items = []

        for room in rooms:
            result = await enrich_room_context(
                image_id=room.get("image_id", room.get("id", "")),
                room_type=room.get("room_type", room.get("type", "unknown")),
                context=context,
                room_confidence=room.get("confidence", 0.5),
                indoor_outdoor=room.get("indoor_outdoor", "indoor"),
                property_type=room.get("property_type"),
            )

            # Merge enrichment into room data
            enriched_room = {
                **room,
                "context": result.get("context"),
                "enrichment_confidence": result.get("confidence", 0),
            }
            enriched_rooms.append(enriched_room)

            # Collect citations
            evidence_pack.extend(result.get("citations", []))

            # Track items needing review
            if result.get("needs_review"):
                review_items.append(room.get("image_id", "unknown"))

        # Write back to Firestore
        writeback.advance_workflow(state["study_id"], "reviewing_rooms")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="analyzing_rooms",
            to_status="reviewing_rooms",
            stage_summary={
                "total_rooms": len(enriched_rooms),
                "needs_review": len(review_items),
            },
        )

        return {
            **state,
            "current_stage": "reviewing_rooms",
            "rooms": enriched_rooms,
            "objects": objects,  # Pass objects detected by vision analysis
            "evidence_pack": evidence_pack,
            "needs_review": len(review_items) > 0 or len(rooms) > 0,
            "items_needing_review": review_items,
        }


async def analyze_objects_node(state: WorkflowState) -> WorkflowState:
    """
    Analyze objects/components in room photos.

    Enriches object detections with IRS-relevant context using ObjectContextAgent.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("analyze_objects"):
        objects = state.get("objects", [])

        if not objects:
            writeback.advance_workflow(state["study_id"], "analyzing_takeoffs")
            return {
                **state,
                "current_stage": "analyzing_takeoffs",
                "objects": [],
            }

        # Build context with available documents
        context = _build_stage_context(state)

        # Get room context for objects if available
        rooms = state.get("rooms", [])
        default_room_type = rooms[0].get("room_type") if rooms else None

        # Enrich all objects with IRS context
        enriched_objects = await enrich_objects_batch(
            detections=objects,
            context=context,
            room_type=default_room_type,
        )

        # Extract evidence and review items
        evidence_pack = state.get("evidence_pack", [])
        review_items = []

        for obj in enriched_objects:
            evidence_pack.extend(obj.get("citations", []))
            if obj.get("needs_review"):
                review_items.append(obj.get("original_label", "unknown"))

        # Move to takeoffs analysis
        writeback.advance_workflow(state["study_id"], "analyzing_takeoffs")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="analyzing_objects",
            to_status="analyzing_takeoffs",
            stage_summary={
                "total_objects": len(enriched_objects),
                "needs_review": len(review_items),
            },
        )

        return {
            **state,
            "current_stage": "analyzing_takeoffs",
            "objects": enriched_objects,
            "evidence_pack": evidence_pack,
        }


async def analyze_takeoffs_node(state: WorkflowState) -> WorkflowState:
    """
    Analyze takeoffs (quantities/measurements).

    Calculates quantities for each component using TakeoffAgent with RSMeans units.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("analyze_takeoffs"):
        objects = state.get("objects", [])

        if not objects:
            writeback.advance_workflow(state["study_id"], "reviewing_takeoffs")
            return {
                **state,
                "current_stage": "reviewing_takeoffs",
                "takeoffs": [],
                "needs_review": False,
            }

        # Build context with available documents
        context = _build_stage_context(state)

        # Get room area if available from rooms
        rooms = state.get("rooms", [])
        room_area_sf = None
        room_type = None
        if rooms:
            room_type = rooms[0].get("room_type")
            # Try to get area from room context
            room_context = rooms[0].get("context", {})
            if room_context:
                room_area_sf = room_context.get("room_area_sf")

        # Build component list from objects for takeoff
        components = []
        for obj in objects:
            # Get component name from enriched context or original label
            obj_context = obj.get("context", {})
            component_name = obj_context.get("component_name") if obj_context else None
            if not component_name:
                component_name = obj.get("original_label", obj.get("label", "unknown"))

            components.append({
                "component_name": component_name,
                "detection_count": 1,  # Each detection is 1 count
                "room_type": room_type,
                "room_area_sf": room_area_sf,
            })

        # Calculate takeoffs for all components
        takeoffs = await calculate_takeoffs_batch(
            components=components,
            context=context,
            room_type=room_type,
            room_area_sf=room_area_sf,
        )

        # Extract evidence and review items
        evidence_pack = state.get("evidence_pack", [])
        review_items = []

        for takeoff in takeoffs:
            evidence_pack.extend(takeoff.get("citations", []))
            if takeoff.get("needs_review"):
                review_items.append(takeoff.get("component_name", "unknown"))

        writeback.advance_workflow(state["study_id"], "reviewing_takeoffs")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="analyzing_takeoffs",
            to_status="reviewing_takeoffs",
            stage_summary={
                "total_takeoffs": len(takeoffs),
                "needs_review": len(review_items),
            },
        )

        return {
            **state,
            "current_stage": "reviewing_takeoffs",
            "takeoffs": takeoffs,
            "evidence_pack": evidence_pack,
            "needs_review": len(review_items) > 0,
            "items_needing_review": review_items,
        }


async def classify_assets_node(state: WorkflowState) -> WorkflowState:
    """
    Classify objects using IRS guidance.

    This is the core agentic node - uses the AssetClassificationAgent
    to classify each object with evidence-backed citations.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("classify_assets"):
        objects = state.get("objects", [])

        if not objects:
            # No objects to classify
            writeback.advance_workflow(state["study_id"], "verifying_assets")
            return {
                **state,
                "current_stage": "verifying_assets",
                "asset_classifications": [],
            }

        # Build context with available documents
        context = _build_stage_context(state)

        # Classify all objects
        classified = await classify_components_batch(objects, context)

        # Extract evidence pack
        evidence_pack = []
        review_items = []

        for item in classified:
            evidence_pack.extend(item.get("citations", []))
            if item.get("needs_review"):
                review_items.append(item.get("component", "unknown"))

            # Log each classification
            tracer.log_classification(
                component=item.get("component", ""),
                classification=item.get("classification", {}),
                num_citations=len(item.get("citations", [])),
                confidence=item.get("confidence", 0),
                needs_review=item.get("needs_review", False),
                study_id=state["study_id"],
            )

        # Write results to Firestore
        writeback.update_objects_with_classifications(state["study_id"], classified)

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="reviewing_assets",
            to_status="estimating_costs",
            stage_summary={
                "total_classified": len(classified),
                "needs_review": len(review_items),
            },
        )

        return {
            **state,
            "current_stage": "estimating_costs",
            "asset_classifications": classified,
            "evidence_pack": evidence_pack,
            "needs_review": len(review_items) > 0,
            "items_needing_review": review_items,
        }


async def estimate_costs_node(state: WorkflowState) -> WorkflowState:
    """
    Estimate costs for classified components.

    Uses CostEstimationAgent to calculate costs with RSMeans citations.
    This runs AFTER asset classification so costs can be grouped by MACRS bucket.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("estimate_costs"):
        takeoffs = state.get("takeoffs", [])

        if not takeoffs:
            writeback.advance_workflow(state["study_id"], "verifying_assets")
            return {
                **state,
                "current_stage": "verifying_assets",
                "cost_estimates": [],
            }

        # Build context with available documents
        context = _build_stage_context(state)

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

        # Estimate costs for all takeoffs
        cost_estimates = await estimate_costs_batch(
            takeoffs=takeoff_data,
            context=context,
            quality_tier="standard",
            location_factor=1.0,
            year_factor=1.0,  # RSMeans 2020 base
        )

        # Aggregate costs
        cost_summary = aggregate_costs(cost_estimates)

        # Extract evidence
        evidence_pack = state.get("evidence_pack", [])
        review_items = []

        for estimate in cost_estimates:
            evidence_pack.extend(estimate.get("citations", []))
            if estimate.get("needs_review"):
                review_items.append(estimate.get("component_name", "unknown"))

        # Write costs to Firestore
        writeback.update_study_with_costs(state["study_id"], cost_estimates, cost_summary)
        writeback.advance_workflow(state["study_id"], "verifying_assets")

        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status="estimating_costs",
            to_status="verifying_assets",
            stage_summary={
                "total_estimates": len(cost_estimates),
                "total_cost": cost_summary.get("total_cost", 0),
                "needs_review": len(review_items),
            },
        )

        return {
            **state,
            "current_stage": "verifying_assets",
            "cost_estimates": cost_estimates,
            "evidence_pack": evidence_pack,
            "needs_review": state.get("needs_review", False) or len(review_items) > 0,
        }


async def verify_assets_node(state: WorkflowState) -> WorkflowState:
    """
    Final verification before completion.

    This node waits for engineer approval of all classifications.
    """
    tracer = get_tracer()
    writeback = FirestoreWriteback()

    with tracer.span("verify_assets"):
        if state.get("engineer_approved"):
            # Engineer has approved - complete workflow
            writeback.advance_workflow(state["study_id"], "completed")

            tracer.log_workflow_transition(
                study_id=state["study_id"],
                from_status="verifying_assets",
                to_status="completed",
            )

            return {
                **state,
                "current_stage": "completed",
            }

        # Still waiting for approval
        return state


async def complete_workflow_node(state: WorkflowState) -> WorkflowState:
    """
    Mark workflow as complete.
    """
    tracer = get_tracer()

    with tracer.span("complete_workflow"):
        tracer.log_workflow_transition(
            study_id=state["study_id"],
            from_status=state.get("current_stage", "unknown"),
            to_status="completed",
        )

        return {
            **state,
            "current_stage": "completed",
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
