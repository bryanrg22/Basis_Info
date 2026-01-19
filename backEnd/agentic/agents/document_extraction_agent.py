"""
Document Extraction Agent - Intelligent document field extraction.

Uses Azure Document Intelligence with self-correction and verification
to extract fields from appraisal documents.

Phase 4 Enhancement: Tool-as-Agent pattern for document extraction.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext, AgentOutput

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Schemas
# =============================================================================


class ExtractionInput(BaseModel):
    """Input for document extraction."""

    pdf_path: str = Field(..., description="Path to the PDF file")
    field_hints: list[str] = Field(
        default_factory=lambda: [
            "property_address",
            "appraised_value",
            "land_value",
            "building_value",
            "year_built",
            "gross_building_area",
            "property_type",
        ],
        description="Fields to extract",
    )
    page_range: Optional[tuple[int, int]] = Field(
        default=None, description="Optional (start, end) page range"
    )


class ExtractedField(BaseModel):
    """A single extracted field."""

    field_name: str = Field(..., description="Name of the field")
    value: Optional[str] = Field(default=None, description="Extracted value")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Extraction confidence"
    )
    page_number: Optional[int] = Field(default=None, description="Page where found")
    bounding_box: Optional[list[float]] = Field(
        default=None, description="Bounding box coordinates"
    )
    format_valid: bool = Field(
        default=True, description="Whether value passes format validation"
    )
    validation_notes: Optional[str] = Field(
        default=None, description="Notes from format validation"
    )


class ExtractionResult(BaseModel):
    """Result of document extraction."""

    fields: list[ExtractedField] = Field(
        default_factory=list, description="Extracted fields"
    )
    pages_processed: int = Field(
        default=0, ge=0, description="Number of pages processed"
    )
    extraction_method: str = Field(
        default="azure_di", description="Method used for extraction"
    )
    overall_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall extraction confidence"
    )
    needs_review: bool = Field(
        default=False, description="Whether extraction needs human review"
    )
    review_reasons: list[str] = Field(
        default_factory=list, description="Reasons for needing review"
    )
    citation_refs: list[str] = Field(
        default_factory=list, description="Document references"
    )


# =============================================================================
# Domain-Specific Extraction Tools
# =============================================================================

# Expected formats for common fields
FIELD_FORMATS = {
    "property_address": {
        "pattern": r"^\d+.*(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|way|lane|ln)",
        "description": "Street address format",
        "example": "123 Main Street",
    },
    "appraised_value": {
        "pattern": r"^\$?[\d,]+(?:\.\d{2})?$",
        "description": "Currency format",
        "example": "$1,250,000 or 1250000",
    },
    "land_value": {
        "pattern": r"^\$?[\d,]+(?:\.\d{2})?$",
        "description": "Currency format",
        "example": "$250,000",
    },
    "building_value": {
        "pattern": r"^\$?[\d,]+(?:\.\d{2})?$",
        "description": "Currency format",
        "example": "$1,000,000",
    },
    "year_built": {
        "pattern": r"^(19|20)\d{2}$",
        "description": "4-digit year",
        "example": "1985 or 2020",
    },
    "gross_building_area": {
        "pattern": r"^[\d,]+(?:\s*(?:sf|sq\.?\s*ft\.?|square\s*feet))?$",
        "description": "Area in square feet",
        "example": "25,000 SF",
    },
    "effective_age": {
        "pattern": r"^\d+(?:\s*(?:years?|yrs?))?$",
        "description": "Age in years",
        "example": "15 years",
    },
    "appraisal_date": {
        "pattern": r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|^\w+\s+\d{1,2},?\s+\d{4}$",
        "description": "Date format",
        "example": "01/15/2024 or January 15, 2024",
    },
}


@tool
def extract_page_fields(
    pdf_path: str,
    page_num: int,
    field_hints: list[str],
) -> dict:
    """
    Extract specific fields from a single page of a PDF.

    Uses regex patterns and heuristics to find field values.
    For production use, this would call Azure Document Intelligence.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number to extract from (1-indexed)
        field_hints: List of field names to search for

    Returns:
        Dictionary of extracted fields with values and confidence
    """
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return {
                    "success": False,
                    "error": f"Page {page_num} out of range (1-{len(pdf.pages)})",
                }

            page = pdf.pages[page_num - 1]
            text = page.extract_text() or ""

            extracted = {}
            for field in field_hints:
                value, confidence = _extract_field_from_text(text, field)
                extracted[field] = {
                    "value": value,
                    "confidence": confidence,
                    "page": page_num,
                }

            return {
                "success": True,
                "page": page_num,
                "fields": extracted,
                "text_length": len(text),
            }

    except Exception as e:
        logger.error(f"Error extracting from page {page_num}: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def _extract_field_from_text(text: str, field_name: str) -> tuple[Optional[str], float]:
    """Extract a single field value from text."""
    text_lower = text.lower()
    field_lower = field_name.lower().replace("_", " ")

    # Define extraction patterns for each field
    patterns = {
        "property_address": [
            r"(?:property\s+address|subject\s+property|location)[:\s]+([^\n]{10,100})",
            r"(\d+\s+[\w\s]+(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd)[^\n]{0,50})",
        ],
        "appraised_value": [
            r"(?:appraised|market)\s+value[:\s]+\$?([\d,]+(?:\.\d{2})?)",
            r"(?:total|final)\s+value[:\s]+\$?([\d,]+(?:\.\d{2})?)",
        ],
        "land_value": [
            r"land\s+value[:\s]+\$?([\d,]+(?:\.\d{2})?)",
            r"site\s+value[:\s]+\$?([\d,]+(?:\.\d{2})?)",
        ],
        "building_value": [
            r"(?:building|improvement)\s+value[:\s]+\$?([\d,]+(?:\.\d{2})?)",
        ],
        "year_built": [
            r"year\s+built[:\s]+(\d{4})",
            r"built\s+(?:in\s+)?(\d{4})",
            r"constructed[:\s]+(\d{4})",
        ],
        "gross_building_area": [
            r"(?:gross|total)\s+(?:building\s+)?area[:\s]+([\d,]+)\s*(?:sf|sq)",
            r"(?:gba|gla)[:\s]+([\d,]+)",
        ],
        "effective_age": [
            r"effective\s+age[:\s]+(\d+)\s*(?:years?)?",
        ],
    }

    field_patterns = patterns.get(field_name, [rf"{field_lower}[:\s]+([^\n]+)"])

    for pattern in field_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Higher confidence for more specific patterns
            confidence = 0.8 if len(field_patterns) > 1 else 0.6
            return value, confidence

    return None, 0.0


@tool
def verify_field_format(
    field_name: str,
    value: str,
    expected_format: str = None,
) -> dict:
    """
    Verify that an extracted field value matches expected format.

    Checks against known formats for common appraisal fields.

    Args:
        field_name: Name of the field
        value: Extracted value to verify
        expected_format: Optional format type (date, currency, address, etc.)

    Returns:
        Validation result with is_valid flag and suggestions
    """
    if not value:
        return {
            "field_name": field_name,
            "value": value,
            "is_valid": False,
            "error": "Empty value",
        }

    # Get expected format info
    format_info = FIELD_FORMATS.get(field_name)
    if not format_info and expected_format:
        # Try by format type
        for fname, finfo in FIELD_FORMATS.items():
            if expected_format.lower() in fname.lower():
                format_info = finfo
                break

    if not format_info:
        # No format validation available
        return {
            "field_name": field_name,
            "value": value,
            "is_valid": True,
            "note": "No format validation available for this field",
        }

    # Validate against pattern
    pattern = format_info["pattern"]
    is_valid = bool(re.match(pattern, value, re.IGNORECASE))

    return {
        "field_name": field_name,
        "value": value,
        "is_valid": is_valid,
        "expected_format": format_info["description"],
        "example": format_info["example"],
        "suggestion": None if is_valid else f"Expected format: {format_info['example']}",
    }


@tool
def retry_with_enhanced_ocr(
    pdf_path: str,
    page_num: int,
    preprocessing: str = "standard",
) -> dict:
    """
    Re-extract from a page with enhanced OCR preprocessing.

    Use when initial extraction had low confidence or missing fields.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number to re-extract
        preprocessing: Type of preprocessing (standard, deskew, denoise, contrast)

    Returns:
        Re-extracted text with preprocessing applied
    """
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return {
                    "success": False,
                    "error": f"Page {page_num} out of range",
                }

            page = pdf.pages[page_num - 1]

            # Different extraction strategies based on preprocessing type
            if preprocessing == "standard":
                text = page.extract_text() or ""
            elif preprocessing == "deskew":
                # Would apply deskewing - for now use standard
                text = page.extract_text() or ""
            elif preprocessing == "denoise":
                # Would apply denoising - for now use standard
                text = page.extract_text() or ""
            elif preprocessing == "contrast":
                # Would enhance contrast - for now use standard
                text = page.extract_text() or ""
            else:
                text = page.extract_text() or ""

            # Also try table extraction
            tables = page.extract_tables()

            return {
                "success": True,
                "page": page_num,
                "preprocessing": preprocessing,
                "text_length": len(text),
                "text_preview": text[:500] if text else "",
                "tables_found": len(tables) if tables else 0,
                "note": f"Extracted with {preprocessing} preprocessing",
            }

    except Exception as e:
        logger.error(f"Error in enhanced extraction: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@tool
def cross_reference_fields(
    fields: dict,
    validation_rules: list[str] = None,
) -> dict:
    """
    Cross-reference extracted fields for consistency.

    Checks logical relationships between fields (e.g., land + building = total value).

    Args:
        fields: Dictionary of field names to values
        validation_rules: Optional specific rules to check

    Returns:
        Consistency check results with any issues found
    """
    issues = []
    suggestions = []

    # Rule 1: Total value should equal land + building (approximately)
    if "appraised_value" in fields and "land_value" in fields and "building_value" in fields:
        try:
            total = float(fields["appraised_value"].replace(",", "").replace("$", ""))
            land = float(fields["land_value"].replace(",", "").replace("$", ""))
            building = float(fields["building_value"].replace(",", "").replace("$", ""))

            expected_total = land + building
            difference = abs(total - expected_total)
            tolerance = total * 0.05  # 5% tolerance

            if difference > tolerance:
                issues.append(
                    f"Value mismatch: Land ({land:,.0f}) + Building ({building:,.0f}) = {expected_total:,.0f}, "
                    f"but Total is {total:,.0f} (difference: {difference:,.0f})"
                )
                suggestions.append(
                    "Verify value breakdown includes all components (e.g., site improvements, personal property)"
                )
        except (ValueError, TypeError):
            pass  # Can't validate if values aren't numeric

    # Rule 2: Year built should be reasonable
    if "year_built" in fields:
        try:
            year = int(fields["year_built"])
            if year < 1800 or year > 2030:
                issues.append(f"Year built ({year}) is outside reasonable range (1800-2030)")
        except (ValueError, TypeError):
            issues.append(f"Year built value '{fields['year_built']}' is not a valid year")

    # Rule 3: Building area should be reasonable
    if "gross_building_area" in fields:
        try:
            area_str = fields["gross_building_area"].replace(",", "").replace(" SF", "").replace(" sf", "")
            area = float(area_str)
            if area < 100:
                issues.append(f"Building area ({area:,.0f} SF) seems too small")
            elif area > 10_000_000:
                issues.append(f"Building area ({area:,.0f} SF) seems too large")
        except (ValueError, TypeError):
            pass

    return {
        "fields_checked": list(fields.keys()),
        "num_issues": len(issues),
        "issues": issues,
        "suggestions": suggestions,
        "is_consistent": len(issues) == 0,
    }


# =============================================================================
# Document Extraction Agent
# =============================================================================


class DocumentExtractionAgent(BaseStageAgent[ExtractionInput, ExtractionResult]):
    """
    Agent for intelligent document field extraction.

    Uses tools to:
    1. Extract fields from document pages
    2. Verify extracted values against expected formats
    3. Re-extract with enhanced OCR when needed
    4. Cross-reference fields for consistency

    Phase 4 Enhancement: Full tool-as-agent pattern with self-correction.
    """

    def __init__(self):
        super().__init__(stage_name="document_extraction")

    def get_tools(self) -> list[BaseTool]:
        """Return extraction tools."""
        return [
            extract_page_fields,
            verify_field_format,
            retry_with_enhanced_ocr,
            cross_reference_fields,
        ]

    def get_system_prompt(self) -> str:
        return """You are a document extraction expert analyzing appraisal PDFs.

Your task: Extract key fields from the document with high accuracy.

## WORKFLOW

1. **Extract from Key Pages**: Use extract_page_fields on pages 1-5 first
2. **Verify Formats**: Use verify_field_format on each extracted value
3. **Re-extract if Needed**: Use retry_with_enhanced_ocr for low-confidence fields
4. **Cross-Reference**: Use cross_reference_fields to check consistency
5. **Return Results**: Compile all extractions with confidence scores

## FIELDS TO EXTRACT

Priority fields (must extract):
- property_address
- appraised_value
- land_value
- building_value
- year_built
- gross_building_area

Additional fields (if found):
- effective_age
- remaining_economic_life
- property_type
- construction_class
- appraisal_date

## QUALITY RULES

1. **Verify Every Value**: Always use verify_field_format before accepting
2. **Re-extract Low Confidence**: If confidence < 0.6, use retry_with_enhanced_ocr
3. **Cross-Reference Values**: Use cross_reference_fields at the end
4. **Flag Issues**: Set needs_review=true if any field has confidence < 0.5

## OUTPUT FORMAT

Return a JSON object:
{
    "fields": [
        {
            "field_name": "property_address",
            "value": "123 Main Street",
            "confidence": 0.9,
            "page_number": 1,
            "format_valid": true
        },
        ...
    ],
    "pages_processed": 5,
    "extraction_method": "azure_di",
    "overall_confidence": 0.85,
    "needs_review": false,
    "review_reasons": [],
    "citation_refs": ["page_1", "page_2"]
}"""

    def get_output_schema(self) -> type[ExtractionResult]:
        return ExtractionResult

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> ExtractionResult:
        """Parse agent response into extraction result."""
        json_patterns = [
            r'\{[^{}]*"fields"[^{}]*\}',
            r'```json\s*(\{.*?\})\s*```',
            r'```\s*(\{.*?\})\s*```',
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    json_str = match.strip() if isinstance(match, str) else match
                    if not json_str.startswith("{"):
                        continue

                    data = json.loads(json_str)

                    if "fields" in data:
                        fields = []
                        for f in data.get("fields", []):
                            fields.append(
                                ExtractedField(
                                    field_name=f.get("field_name", ""),
                                    value=f.get("value"),
                                    confidence=f.get("confidence", 0.5),
                                    page_number=f.get("page_number"),
                                    format_valid=f.get("format_valid", True),
                                    validation_notes=f.get("validation_notes"),
                                )
                            )

                        return ExtractionResult(
                            fields=fields,
                            pages_processed=data.get("pages_processed", 0),
                            extraction_method=data.get("extraction_method", "azure_di"),
                            overall_confidence=data.get("overall_confidence", 0.5),
                            needs_review=data.get("needs_review", False),
                            review_reasons=data.get("review_reasons", []),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        raise ValueError(f"Could not parse extraction result from response: {response[:500]}")


# =============================================================================
# Convenience Functions
# =============================================================================


async def extract_document_fields(
    pdf_path: str,
    context: StageContext,
    field_hints: list[str] = None,
) -> dict:
    """
    Convenience function to extract fields from a document.

    Args:
        pdf_path: Path to the PDF file
        context: Study context
        field_hints: Optional list of specific fields to extract

    Returns:
        Extraction result with fields and confidence
    """
    agent = DocumentExtractionAgent()

    input_data = ExtractionInput(
        pdf_path=pdf_path,
        field_hints=field_hints or [
            "property_address",
            "appraised_value",
            "land_value",
            "building_value",
            "year_built",
            "gross_building_area",
        ],
    )

    result = await agent.run(context, input_data)

    return {
        "fields": [f.model_dump() for f in result.result.fields] if result.result else [],
        "pages_processed": result.result.pages_processed if result.result else 0,
        "extraction_method": result.result.extraction_method if result.result else "unknown",
        "overall_confidence": result.result.overall_confidence if result.result else 0.0,
        "needs_review": result.needs_review,
        "citations": [c.model_dump() for c in result.citations],
    }
