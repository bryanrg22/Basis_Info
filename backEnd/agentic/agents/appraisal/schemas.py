"""
Pydantic schemas for appraisal extraction agents.

Defines input/output models for:
- ExtractorAgent: Extract appraisal fields using tools
- VerifierAgent: Verify extraction plausibility
- CorrectorAgent: Fix flagged extraction errors
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# =============================================================================
# Extractor Agent Schemas
# =============================================================================


class ExtractorInput(BaseModel):
    """Input for ExtractorAgent."""

    pdf_path: str = Field(..., description="Path to appraisal PDF file")
    mismo_xml: Optional[str] = Field(
        default=None, description="MISMO XML content if available"
    )
    tables_path: Optional[str] = Field(
        default=None, description="Path to .tables.jsonl file"
    )


class ExtractorOutput(BaseModel):
    """Output from ExtractorAgent."""

    sections: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Extracted sections: subject, improvements, etc.",
    )
    field_confidences: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description="Confidence per field: section.field -> confidence",
    )
    field_sources: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="Source per field: section.field -> source tool",
    )
    tools_invoked: List[str] = Field(
        default_factory=list,
        description="Tools that were invoked during extraction",
    )


# =============================================================================
# Verifier Agent Schemas
# =============================================================================


class SuspiciousField(BaseModel):
    """A field flagged as suspicious by the verifier."""

    field_key: str = Field(
        ..., description="Full field key: section.field_name"
    )
    current_value: Any = Field(
        ..., description="Current extracted value"
    )
    issue_type: str = Field(
        ...,
        description="Type of issue: ocr_error, implausible, inconsistent, low_confidence",
    )
    reasoning: str = Field(
        ..., description="Why this field is suspicious"
    )
    suggested_recheck_method: str = Field(
        ..., description="Recommended tool to re-extract: azure_di, vision"
    )


class VerifierInput(BaseModel):
    """Input for VerifierAgent."""

    sections: Dict[str, Dict[str, Any]] = Field(
        ..., description="Extracted sections to verify"
    )
    field_confidences: Dict[str, Dict[str, float]] = Field(
        ..., description="Confidence scores per field"
    )
    field_sources: Dict[str, Dict[str, str]] = Field(
        ..., description="Source tool per field"
    )


class VerifierOutput(BaseModel):
    """Output from VerifierAgent."""

    all_plausible: bool = Field(
        ..., description="True if all fields pass plausibility checks"
    )
    suspicious_fields: List[SuspiciousField] = Field(
        default_factory=list,
        description="Fields that need re-extraction or review",
    )
    recommend_correction: bool = Field(
        default=False,
        description="True if automatic correction should be attempted",
    )
    verification_notes: Optional[str] = Field(
        default=None, description="Additional notes from verification"
    )


# =============================================================================
# Corrector Agent Schemas
# =============================================================================


class FieldCorrection(BaseModel):
    """A correction made to a field."""

    field_key: str = Field(
        ..., description="Full field key: section.field_name"
    )
    old_value: Any = Field(
        ..., description="Previous incorrect value"
    )
    new_value: Any = Field(
        ..., description="Corrected value"
    )
    correction_source: str = Field(
        ..., description="Tool used for correction"
    )
    correction_reasoning: str = Field(
        ..., description="Explanation of what was wrong and how fixed"
    )


class CorrectorInput(BaseModel):
    """Input for CorrectorAgent."""

    sections: Dict[str, Dict[str, Any]] = Field(
        ..., description="Current extracted sections"
    )
    suspicious_fields: List[SuspiciousField] = Field(
        ..., description="Fields flagged for correction"
    )
    field_sources: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="Original source per field: section.field -> tool name (azure_di, vision, etc.)",
    )
    pdf_path: str = Field(
        ..., description="Path to appraisal PDF for re-extraction"
    )


class CorrectorOutput(BaseModel):
    """Output from CorrectorAgent."""

    corrections_made: List[FieldCorrection] = Field(
        default_factory=list,
        description="List of corrections applied",
    )
    updated_sections: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Sections with corrections applied",
    )
    correction_summary: str = Field(
        ..., description="Summary of what was corrected"
    )


# =============================================================================
# Audit Trail Schemas
# =============================================================================


class FieldAuditEntry(BaseModel):
    """Audit entry for a single field."""

    field_key: str = Field(..., description="Full field key")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="When this entry was created",
    )
    action: str = Field(
        ..., description="Action: extracted, verified, corrected"
    )
    value: Any = Field(..., description="Value at this point")
    source: str = Field(..., description="Source tool or agent")
    confidence: float = Field(..., description="Confidence at this point")
    notes: Optional[str] = Field(default=None, description="Additional notes")


class AgentCall(BaseModel):
    """Record of an agent invocation."""

    agent_name: str = Field(..., description="Name of agent called")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    input_summary: str = Field(..., description="Summary of input")
    output_summary: str = Field(..., description="Summary of output")
    tools_used: List[str] = Field(
        default_factory=list, description="Tools invoked"
    )
    duration_ms: Optional[int] = Field(
        default=None, description="Duration in milliseconds"
    )


class ExtractionAuditTrail(BaseModel):
    """Complete audit trail for an extraction run."""

    study_id: str = Field(..., description="Study identifier")
    started_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    completed_at: Optional[str] = Field(default=None)
    iterations: int = Field(
        default=0, description="Number of correction iterations"
    )
    field_history: List[FieldAuditEntry] = Field(
        default_factory=list,
        description="Full history of field changes",
    )
    agent_calls: List[AgentCall] = Field(
        default_factory=list,
        description="Record of all agent invocations",
    )
    final_confidence: float = Field(
        default=0.0, description="Overall extraction confidence"
    )
    needs_review: bool = Field(
        default=False, description="Whether human review is needed"
    )
    review_reasons: List[str] = Field(
        default_factory=list,
        description="Reasons for flagging review",
    )
