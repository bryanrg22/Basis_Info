"""
LangChain tool wrappers for appraisal extraction.

Wraps existing extractors from evidence_layer/src/tiered_extraction/ as
callable tools that agents can use for extraction and verification.

Tool order by cost (agents should prefer cheaper tools first):
1. parse_mismo_xml (FREE) - if XML available
2. extract_with_azure_di (PAID) - for most fields
3. extract_with_vision (EXPENSIVE) - only for stubborn fields

NOTE: extract_with_regex is commented out for now.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# Import alert function for infrastructure failure notifications
from ...observability.alerts import send_alert

logger = logging.getLogger(__name__)

# Module-level context for tracking current study_id (set by caller before tool invocation)
_current_study_id: Optional[str] = None


def set_extraction_context(study_id: str) -> None:
    """Set the current study_id for tool alerting context."""
    global _current_study_id
    _current_study_id = study_id


def get_extraction_context() -> Optional[str]:
    """Get the current study_id for alerting."""
    return _current_study_id


async def _send_tool_alert(
    tool_name: str,
    error_type: str,
    error_message: str,
    severity: str = "error",
) -> None:
    """Send alert for tool infrastructure failures."""
    study_id = get_extraction_context()
    if not study_id:
        logger.warning(f"Cannot send alert - no study_id context set. Error: {error_message}")
        return

    await send_alert(
        study_id=study_id,
        stage="resource_extraction",
        error_type=error_type,
        error_message=f"[{tool_name}] {error_message}",
        severity=severity,
        context={"tool": tool_name},
    )


# Add evidence_layer to path for imports
evidence_layer_path = Path(__file__).parent.parent.parent.parent / "evidence_layer" / "src"
if str(evidence_layer_path) not in sys.path:
    sys.path.insert(0, str(evidence_layer_path))


# =============================================================================
# Tool Input Schemas
# =============================================================================


class MISMOInput(BaseModel):
    """Input for MISMO XML parsing."""

    xml_content: str = Field(..., description="MISMO XML content to parse")


class AzureDIInput(BaseModel):
    """Input for Azure Document Intelligence extraction."""

    pdf_path: str = Field(..., description="Path to PDF file")


class VisionInput(BaseModel):
    """Input for GPT-4o Vision extraction."""

    pdf_path: str = Field(..., description="Path to PDF file")
    missing_fields: List[str] = Field(
        ...,
        description="List of field keys to extract, e.g. ['subject.property_address', 'improvements.year_built']",
    )


class ValidationInput(BaseModel):
    """Input for extraction validation."""

    sections: Dict[str, Dict[str, Any]] = Field(
        ..., description="Extracted sections to validate"
    )


class VisionRecheckInput(BaseModel):
    """Input for targeted field re-extraction with vision."""

    pdf_path: str = Field(..., description="Path to PDF file")
    field_key: str = Field(
        ..., description="Single field key to re-extract, e.g. 'subject.property_address'"
    )
    context: Optional[str] = Field(
        default=None,
        description="Additional context about what was wrong with previous extraction",
    )


# =============================================================================
# Tool Implementations
# =============================================================================


@tool(args_schema=MISMOInput)
def parse_mismo_xml(xml_content: str) -> Dict[str, Any]:
    """
    Parse MISMO XML for appraisal data. FREE - use first if XML available.

    MISMO (Mortgage Industry Standards Maintenance Organization) XML is the
    authoritative format for appraisal data. When available, it provides
    100% confidence extraction.

    Returns:
        Extracted sections with confidence scores.
    """
    try:
        from tiered_extraction.mismo_parser import MISMOParser

        parser = MISMOParser()
        result = parser.parse(xml_content)

        return {
            "success": True,
            "sections": result.to_dict().get("sections", {}),
            "overall_confidence": result.overall_confidence,
            "source": "mismo_xml",
        }
    except ImportError as e:
        logger.warning(f"MISMO parser not available: {e}")
        return {
            "success": False,
            "error": "MISMO parser not installed",
            "source": "mismo_xml",
        }
    except Exception as e:
        logger.error(f"MISMO parsing failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "source": "mismo_xml",
        }


# =============================================================================
# COMMENTED OUT: Regex extraction
# =============================================================================
#
# @tool(args_schema=RegexInput)
# def extract_with_regex(tables_path: str, fallback_fields: Optional[Dict] = None) -> Dict[str, Any]:
#     """
#     Extract using regex patterns from table text. FREE but lower confidence.
#
#     Uses pattern matching on extracted table text. Good for structured forms
#     but may miss handwritten or non-standard content.
#
#     Returns:
#         Extracted sections with confidence scores.
#     """
#     try:
#         from tiered_extraction.extractor import RegexExtractor
#
#         extractor = RegexExtractor()
#         if not extractor.is_available():
#             return {
#                 "success": False,
#                 "error": "Regex extractor not available",
#                 "source": "regex",
#             }
#
#         results = extractor.extract(tables_path, fallback_fields or {})
#
#         # Convert FieldResult objects to dicts
#         sections = {}
#         for field_key, field_result in results.items():
#             section, field_name = field_key.split(".", 1)
#             if section not in sections:
#                 sections[section] = {}
#             sections[section][field_name] = {
#                 "value": field_result.value,
#                 "confidence": field_result.confidence,
#             }
#
#         return {
#             "success": True,
#             "sections": sections,
#             "num_fields": len(results),
#             "source": "regex",
#         }
#     except Exception as e:
#         logger.error(f"Regex extraction failed: {e}")
#         return {
#             "success": False,
#             "error": str(e),
#             "source": "regex",
#         }


@tool(args_schema=AzureDIInput)
async def extract_with_azure_di(pdf_path: str) -> Dict[str, Any]:
    """
    Extract using Azure Document Intelligence. PAID ($0.10-0.50 per doc).

    Uses Azure's Document Intelligence (Form Recognizer) for high-accuracy
    extraction with native confidence scores. Excellent for structured forms,
    tables, and typed text.

    Returns:
        Extracted sections with confidence scores.
    """
    try:
        from tiered_extraction.azure_di_extractor import AzureDocumentExtractor

        extractor = AzureDocumentExtractor()
        if not extractor.is_available():
            error_msg = "Azure DI not configured. Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY"
            await _send_tool_alert(
                tool_name="extract_with_azure_di",
                error_type="AZURE_DI_NOT_CONFIGURED",
                error_message=error_msg,
                severity="error",
            )
            return {
                "success": False,
                "error": error_msg,
                "source": "azure_di",
            }

        result = await extractor.extract(pdf_path)

        # Alert if extraction returned no data (infrastructure issue)
        if result.overall_confidence == 0:
            await _send_tool_alert(
                tool_name="extract_with_azure_di",
                error_type="AZURE_DI_NO_DATA",
                error_message="Azure DI returned 0% confidence - possible API or document parsing issue",
                severity="error",
            )

        # Get the dict and extract sections (everything except _metadata)
        result_dict = result.to_dict()
        sections = {k: v for k, v in result_dict.items() if k != "_metadata"}

        return {
            "success": True,
            "sections": sections,
            "overall_confidence": result.overall_confidence,
            "needs_review": result.needs_review,
            "source": "azure_di",
        }
    except ImportError as e:
        error_msg = "azure-ai-documentintelligence package not installed"
        logger.warning(f"Azure DI extractor not available: {e}")
        await _send_tool_alert(
            tool_name="extract_with_azure_di",
            error_type="AZURE_DI_PACKAGE_MISSING",
            error_message=error_msg,
            severity="error",
        )
        return {
            "success": False,
            "error": error_msg,
            "source": "azure_di",
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Azure DI extraction failed: {e}")
        # Categorize the error for better alerting
        if "429" in error_msg or "rate" in error_msg.lower():
            error_type = "AZURE_DI_RATE_LIMITED"
            severity = "warning"
        elif "401" in error_msg or "403" in error_msg or "auth" in error_msg.lower():
            error_type = "AZURE_DI_AUTH_ERROR"
            severity = "error"
        elif "body" in error_msg.lower() or "argument" in error_msg.lower():
            error_type = "AZURE_DI_API_ERROR"
            severity = "error"
        else:
            error_type = "AZURE_DI_EXTRACTION_FAILED"
            severity = "error"
        await _send_tool_alert(
            tool_name="extract_with_azure_di",
            error_type=error_type,
            error_message=error_msg[:500],
            severity=severity,
        )
        return {
            "success": False,
            "error": error_msg,
            "source": "azure_di",
        }


@tool(args_schema=VisionInput)
async def extract_with_vision(pdf_path: str, missing_fields: List[str]) -> Dict[str, Any]:
    """
    Extract using GPT-4o Vision. EXPENSIVE ($0.10-0.20 per call).

    Uses multimodal AI to visually analyze PDF pages. Best for:
    - Handwritten fields
    - Faded or scanned documents
    - Non-standard form layouts
    - Fields that other methods missed

    Only use for stubborn fields that Azure DI couldn't extract.

    Returns:
        Extracted fields with confidence scores.
    """
    try:
        from tiered_extraction.vision_fallback import VisionFallbackExtractor

        extractor = VisionFallbackExtractor()
        if not extractor.is_available():
            error_msg = "Vision extractor not configured. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY"
            await _send_tool_alert(
                tool_name="extract_with_vision",
                error_type="VISION_NOT_CONFIGURED",
                error_message=error_msg,
                severity="error",
            )
            return {
                "success": False,
                "error": error_msg,
                "source": "vision",
            }

        # Convert field keys to tuples: "section.field" -> ("section", "field")
        field_tuples = []
        for field_key in missing_fields:
            if "." in field_key:
                section, field_name = field_key.split(".", 1)
                field_tuples.append((section, field_name))

        if not field_tuples:
            return {
                "success": False,
                "error": "No valid field keys provided",
                "source": "vision",
            }

        results = await extractor.extract_fields(pdf_path, field_tuples)

        # Alert if vision returned no results (possible PDF conversion or API issue)
        if not results:
            await _send_tool_alert(
                tool_name="extract_with_vision",
                error_type="VISION_NO_RESULTS",
                error_message=f"Vision extraction returned no results for {len(field_tuples)} fields - check PDF conversion and OpenAI API",
                severity="error",
            )

        # Convert FieldResult objects to dicts
        sections = {}
        for field_key, field_result in results.items():
            section, field_name = field_key.split(".", 1) if "." in field_key else ("unknown", field_key)
            if section not in sections:
                sections[section] = {}
            sections[section][field_name] = {
                "value": field_result.value,
                "confidence": field_result.confidence,
            }

        return {
            "success": True,
            "sections": sections,
            "num_fields": len(results),
            "source": "vision",
        }
    except ImportError as e:
        error_msg = "openai package not installed"
        logger.warning(f"Vision extractor not available: {e}")
        await _send_tool_alert(
            tool_name="extract_with_vision",
            error_type="VISION_PACKAGE_MISSING",
            error_message=error_msg,
            severity="error",
        )
        return {
            "success": False,
            "error": error_msg,
            "source": "vision",
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Vision extraction failed: {e}")
        # Categorize the error
        if "429" in error_msg or "rate" in error_msg.lower():
            error_type = "VISION_RATE_LIMITED"
            severity = "warning"
        elif "poppler" in error_msg.lower() or "pdf" in error_msg.lower():
            error_type = "VISION_PDF_CONVERSION_FAILED"
            severity = "error"
        elif "401" in error_msg or "403" in error_msg or "auth" in error_msg.lower():
            error_type = "VISION_AUTH_ERROR"
            severity = "error"
        else:
            error_type = "VISION_EXTRACTION_FAILED"
            severity = "error"
        await _send_tool_alert(
            tool_name="extract_with_vision",
            error_type=error_type,
            error_message=error_msg[:500],
            severity=severity,
        )
        return {
            "success": False,
            "error": error_msg,
            "source": "vision",
        }


@tool(args_schema=ValidationInput)
def validate_extraction(sections: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Validate extracted appraisal data. FREE.

    Runs plausibility checks including:
    - Critical field presence
    - Value range validation (year_built, GLA, currency)
    - Cross-field consistency (contract vs appraised value)
    - Date format validation

    Returns:
        Validation results with any errors/warnings.
    """
    try:
        from tiered_extraction.confidence import ExtractionResult, FieldResult
        from tiered_extraction.validation import AppraisalValidator

        # Build ExtractionResult from sections dict
        result = ExtractionResult(
            sections={},
            overall_confidence=0.0,
            needs_review=False,
            extraction_time_ms=0,
            sources_used=[],
        )

        # Populate sections
        for section_name, fields in sections.items():
            if section_name not in result.sections:
                result.sections[section_name] = {}
            for field_name, field_data in fields.items():
                if isinstance(field_data, dict):
                    result.sections[section_name][field_name] = FieldResult(
                        value=field_data.get("value"),
                        confidence=field_data.get("confidence", 0.7),
                        source=field_data.get("source", "unknown"),
                    )
                else:
                    result.sections[section_name][field_name] = FieldResult(
                        value=field_data,
                        confidence=0.7,
                        source="unknown",
                    )

        # Run validation
        validator = AppraisalValidator()
        validated_result = validator.validate(result)
        summary = validator.get_validation_summary()

        return {
            "success": True,
            "needs_review": validated_result.needs_review,
            "total_checks": summary["total_checks"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "errors": summary["errors"],
            "warnings": summary["warnings"],
        }
    except ImportError as e:
        logger.warning(f"Validator not available: {e}")
        return {
            "success": False,
            "error": "Validation module not available",
        }
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@tool(args_schema=VisionRecheckInput)
async def vision_recheck_field(
    pdf_path: str,
    field_key: str,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-extract a single field using vision. MODERATELY EXPENSIVE ($0.05-0.10).

    Targeted re-extraction of a specific field that was flagged as suspicious.
    Provides additional context to GPT-4o about what was wrong.

    Use this when:
    - A field has an OCR error (0 vs O, 1 vs I)
    - A value is implausible and needs visual verification
    - Previous extraction had low confidence

    Returns:
        Single field with confidence score.
    """
    try:
        from tiered_extraction.vision_fallback import VisionFallbackExtractor

        extractor = VisionFallbackExtractor()
        if not extractor.is_available():
            return {
                "success": False,
                "error": "Vision extractor not configured",
                "source": "vision_recheck",
            }

        # Parse field key
        if "." not in field_key:
            return {
                "success": False,
                "error": f"Invalid field key format: {field_key}. Expected 'section.field_name'",
                "source": "vision_recheck",
            }

        section, field_name = field_key.split(".", 1)

        # Extract the single field
        results = await extractor.extract_fields(pdf_path, [(section, field_name)])

        if field_key in results:
            field_result = results[field_key]
            return {
                "success": True,
                "field_key": field_key,
                "value": field_result.value,
                "confidence": field_result.confidence,
                "source": "vision_recheck",
            }
        else:
            return {
                "success": False,
                "error": f"Could not extract field: {field_key}",
                "source": "vision_recheck",
            }
    except Exception as e:
        logger.error(f"Vision recheck failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "source": "vision_recheck",
        }


# =============================================================================
# Tool Lists for Agents
# =============================================================================


def get_extractor_tools():
    """Get tools available to ExtractorAgent."""
    return [
        parse_mismo_xml,
        extract_with_azure_di,
        extract_with_vision,
        # extract_with_regex,  # COMMENTED OUT
    ]


def get_verifier_tools():
    """Get tools available to VerifierAgent."""
    return [
        validate_extraction,
        vision_recheck_field,
    ]


def get_corrector_tools():
    """Get tools available to CorrectorAgent."""
    return [
        extract_with_azure_di,
        extract_with_vision,
        vision_recheck_field,
    ]
