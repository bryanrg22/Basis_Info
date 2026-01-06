"""
LangGraph Orchestrator for multi-agent appraisal extraction.

Uses a StateGraph with nodes and conditional edges:
    extractor → verifier → [all_plausible? → END : corrector → verifier (loop)]

The orchestrator:
1. Runs ExtractorAgent to extract appraisal data
2. Runs VerifierAgent to check for errors
3. If issues found and iterations < max: runs CorrectorAgent
4. Loops back to verifier until all plausible or max iterations reached
5. Returns extraction result with full audit trail
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from ..base_agent import StageContext
from .extractor_agent import run_extractor_agent
from .verifier_agent import run_verifier_agent
from .corrector_agent import run_corrector_agent
from .schemas import AgentCall, ExtractionAuditTrail, FieldAuditEntry

logger = logging.getLogger(__name__)


# =============================================================================
# State Type
# =============================================================================


class AppraisalExtractionState(TypedDict):
    """State passed between nodes in the appraisal extraction graph."""

    # Input
    study_id: str
    pdf_path: str
    mismo_xml: Optional[str]
    tables_path: Optional[str]

    # Extraction results
    sections: Dict[str, Dict[str, Any]]
    field_confidences: Dict[str, Dict[str, float]]
    field_sources: Dict[str, Dict[str, str]]
    tools_invoked: List[str]

    # Verification results
    all_plausible: bool
    suspicious_fields: List[Dict[str, Any]]

    # Correction tracking
    iterations: int
    max_iterations: int
    corrections_made: List[Dict[str, Any]]

    # Audit trail
    audit_trail: Dict[str, Any]


# =============================================================================
# Routing Functions
# =============================================================================


def route_after_verification(state: AppraisalExtractionState) -> str:
    """Route based on verification result and iteration count."""
    if state["all_plausible"]:
        logger.info("Verification passed - all fields plausible")
        return "all_good"

    if state["iterations"] >= state["max_iterations"]:
        logger.warning(
            f"Max iterations ({state['max_iterations']}) reached - "
            f"{len(state['suspicious_fields'])} fields still suspicious"
        )
        return "max_iterations"

    logger.info(
        f"Found {len(state['suspicious_fields'])} suspicious fields - "
        f"attempting correction (iteration {state['iterations'] + 1})"
    )
    return "needs_correction"


# =============================================================================
# Audit Trail Helpers
# =============================================================================


def _add_agent_call(
    audit: Dict[str, Any],
    agent_name: str,
    input_summary: str,
    output_summary: str,
    tools_used: List[str] = None,
    duration_ms: int = None,
) -> None:
    """Add an agent call record to the audit trail."""
    if "agent_calls" not in audit:
        audit["agent_calls"] = []

    audit["agent_calls"].append({
        "agent_name": agent_name,
        "timestamp": datetime.utcnow().isoformat(),
        "input_summary": input_summary,
        "output_summary": output_summary,
        "tools_used": tools_used or [],
        "duration_ms": duration_ms,
    })


def _add_field_history(
    audit: Dict[str, Any],
    field_key: str,
    action: str,
    value: Any,
    source: str,
    confidence: float,
    notes: str = None,
) -> None:
    """Add a field history entry to the audit trail."""
    if "field_history" not in audit:
        audit["field_history"] = []

    audit["field_history"].append({
        "field_key": field_key,
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "value": value,
        "source": source,
        "confidence": confidence,
        "notes": notes,
    })


# =============================================================================
# Graph Construction
# =============================================================================


def create_appraisal_extraction_graph() -> StateGraph:
    """
    Build the LangGraph for appraisal extraction.

    Flow:
        extractor → verifier → [all_plausible? → END : corrector → verifier]

    Returns:
        Compiled StateGraph
    """
    workflow = StateGraph(AppraisalExtractionState)

    # Add nodes (each calls its agent)
    workflow.add_node("extractor", run_extractor_agent)
    workflow.add_node("verifier", run_verifier_agent)
    workflow.add_node("corrector", run_corrector_agent)

    # Set entry point
    workflow.set_entry_point("extractor")

    # Extractor always goes to verifier
    workflow.add_edge("extractor", "verifier")

    # Verifier routes based on plausibility
    workflow.add_conditional_edges(
        "verifier",
        route_after_verification,
        {
            "all_good": END,           # No issues, done
            "needs_correction": "corrector",  # Issues found, correct
            "max_iterations": END,     # Hit iteration limit, done
        }
    )

    # Corrector always goes back to verifier
    workflow.add_edge("corrector", "verifier")

    return workflow.compile()


# =============================================================================
# Main Entry Point
# =============================================================================


async def run_appraisal_extraction(
    study_id: str,
    pdf_path: str,
    context: StageContext,
    mismo_xml: Optional[str] = None,
    tables_path: Optional[str] = None,
    max_iterations: int = 2,
) -> Dict[str, Any]:
    """
    Run the multi-agent appraisal extraction pipeline.

    This is the main entry point for the agentic extraction system.
    It creates a LangGraph workflow that orchestrates:
    1. ExtractorAgent - intelligent data extraction
    2. VerifierAgent - skeptical plausibility checking
    3. CorrectorAgent - error correction (up to max_iterations)

    Args:
        study_id: Study identifier for audit trail
        pdf_path: Path to appraisal PDF file
        context: Stage context (for compatibility, may be used later)
        mismo_xml: Optional MISMO XML content for high-confidence extraction
        tables_path: Optional path to .tables.jsonl for regex fallback
        max_iterations: Maximum correction iterations (default: 2)

    Returns:
        Dictionary containing:
        - extraction_result: Dict of extracted sections
        - audit_trail: Full audit trail for IRS defensibility
        - needs_review: Boolean indicating if human review needed
        - overall_confidence: Aggregate confidence score
    """
    logger.info(f"Starting agentic appraisal extraction for study {study_id}")
    start_time = datetime.utcnow()

    # Create the graph
    graph = create_appraisal_extraction_graph()

    # Initialize state
    initial_state: AppraisalExtractionState = {
        "study_id": study_id,
        "pdf_path": pdf_path,
        "mismo_xml": mismo_xml,
        "tables_path": tables_path,
        "sections": {},
        "field_confidences": {},
        "field_sources": {},
        "tools_invoked": [],
        "all_plausible": False,
        "suspicious_fields": [],
        "iterations": 0,
        "max_iterations": max_iterations,
        "corrections_made": [],
        "audit_trail": {
            "study_id": study_id,
            "started_at": start_time.isoformat(),
            "field_history": [],
            "agent_calls": [],
        },
    }

    # Run the graph
    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.error(f"Appraisal extraction failed: {e}")
        return {
            "extraction_result": {},
            "audit_trail": {
                **initial_state["audit_trail"],
                "completed_at": datetime.utcnow().isoformat(),
                "error": str(e),
            },
            "needs_review": True,
            "overall_confidence": 0.0,
        }

    # Finalize audit trail
    end_time = datetime.utcnow()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    audit_trail = final_state.get("audit_trail", {})
    audit_trail["completed_at"] = end_time.isoformat()
    audit_trail["iterations"] = final_state.get("iterations", 0)
    audit_trail["final_confidence"] = _calculate_overall_confidence(
        final_state.get("field_confidences", {})
    )
    audit_trail["needs_review"] = not final_state.get("all_plausible", False)
    audit_trail["duration_ms"] = duration_ms

    if not final_state.get("all_plausible", False):
        audit_trail["review_reasons"] = [
            f"Suspicious field: {sf.get('field_key', 'unknown')} - {sf.get('reasoning', 'no reason')}"
            for sf in final_state.get("suspicious_fields", [])
        ]

    logger.info(
        f"Appraisal extraction complete: "
        f"confidence={audit_trail['final_confidence']:.2f}, "
        f"needs_review={audit_trail['needs_review']}, "
        f"iterations={audit_trail['iterations']}, "
        f"duration={duration_ms}ms"
    )

    return {
        "extraction_result": final_state.get("sections", {}),
        "field_confidences": final_state.get("field_confidences", {}),
        "field_sources": final_state.get("field_sources", {}),
        "audit_trail": audit_trail,
        "needs_review": audit_trail["needs_review"],
        "overall_confidence": audit_trail["final_confidence"],
    }


def _calculate_overall_confidence(
    field_confidences: Dict[str, Dict[str, float]]
) -> float:
    """Calculate aggregate confidence from field confidences."""
    if not field_confidences:
        return 0.0

    all_confidences = []
    for section_confidences in field_confidences.values():
        if isinstance(section_confidences, dict):
            for conf in section_confidences.values():
                if isinstance(conf, (int, float)):
                    all_confidences.append(float(conf))

    if not all_confidences:
        return 0.0

    # Weight by criticality - lower if any critical field is low
    critical_fields = {
        "property_address", "year_built", "gross_living_area",
        "appraised_value", "final_opinion_of_market_value",
        "contract_price", "effective_date",
    }

    critical_confidences = []
    for section, fields in field_confidences.items():
        if isinstance(fields, dict):
            for field, conf in fields.items():
                if field in critical_fields and isinstance(conf, (int, float)):
                    critical_confidences.append(float(conf))

    # Overall is min of (average, lowest critical field)
    avg_confidence = sum(all_confidences) / len(all_confidences)

    if critical_confidences:
        min_critical = min(critical_confidences)
        return min(avg_confidence, min_critical)

    return avg_confidence
