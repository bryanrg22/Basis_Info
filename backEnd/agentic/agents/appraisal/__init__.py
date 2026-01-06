"""
Appraisal Extraction Agents

Multi-agent system for intelligent appraisal data extraction:
- ExtractorAgent: Extracts data using MISMO, Azure DI, and Vision tools
- VerifierAgent: Verifies extraction plausibility and flags errors
- CorrectorAgent: Fixes flagged errors using alternative methods

The orchestrator coordinates these agents using a LangGraph StateGraph:
    extractor → verifier → [all_plausible? → END : corrector → verifier]
"""

from .schemas import (
    # Input/Output schemas
    ExtractorInput,
    ExtractorOutput,
    VerifierInput,
    VerifierOutput,
    CorrectorInput,
    CorrectorOutput,
    # Field-level schemas
    SuspiciousField,
    FieldCorrection,
    # Audit trail schemas
    FieldAuditEntry,
    AgentCall,
    ExtractionAuditTrail,
)

from .tools import (
    # Tool functions
    parse_mismo_xml,
    extract_with_azure_di,
    extract_with_vision,
    validate_extraction,
    vision_recheck_field,
    # Tool list getters
    get_extractor_tools,
    get_verifier_tools,
    get_corrector_tools,
)

from .extractor_agent import ExtractorAgent, run_extractor_agent
from .verifier_agent import VerifierAgent, run_verifier_agent
from .corrector_agent import CorrectorAgent, run_corrector_agent

from .orchestrator import (
    AppraisalExtractionState,
    create_appraisal_extraction_graph,
    run_appraisal_extraction,
)

__all__ = [
    # Main entry point
    "run_appraisal_extraction",
    # Graph
    "AppraisalExtractionState",
    "create_appraisal_extraction_graph",
    # Agents
    "ExtractorAgent",
    "VerifierAgent",
    "CorrectorAgent",
    # Agent node functions
    "run_extractor_agent",
    "run_verifier_agent",
    "run_corrector_agent",
    # Schemas
    "ExtractorInput",
    "ExtractorOutput",
    "VerifierInput",
    "VerifierOutput",
    "CorrectorInput",
    "CorrectorOutput",
    "SuspiciousField",
    "FieldCorrection",
    "FieldAuditEntry",
    "AgentCall",
    "ExtractionAuditTrail",
    # Tools
    "parse_mismo_xml",
    "extract_with_azure_di",
    "extract_with_vision",
    "validate_extraction",
    "vision_recheck_field",
    "get_extractor_tools",
    "get_verifier_tools",
    "get_corrector_tools",
]
