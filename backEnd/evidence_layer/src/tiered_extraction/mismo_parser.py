"""
MISMO XML Parser (Tier 1)

Parses MISMO (Mortgage Industry Standards Maintenance Organization) XML
format for appraisal data. MISMO XML is the authoritative source when
available, providing 100% confidence for all extracted fields.

Reference: https://www.mismo.org/
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional

from .confidence import ExtractionResult, FieldResult, create_empty_result
from .field_mappings import MISMO_FIELD_MAPPINGS

logger = logging.getLogger(__name__)


class MISMOParser:
    """
    Parser for MISMO XML appraisal data.

    MISMO XML provides structured, validated appraisal data.
    When available, this is the most reliable source (confidence = 1.0).
    """

    def __init__(self):
        self.namespaces = {
            "mismo": "http://www.mismo.org/residential/2009/schemas",
            "gse": "http://www.datamodelextension.gse.gov/schemas",
        }

    def parse(self, xml_content: str) -> ExtractionResult:
        """
        Parse MISMO XML content and extract appraisal fields.

        Args:
            xml_content: Raw MISMO XML string

        Returns:
            ExtractionResult with all parsed fields at confidence 1.0
        """
        result = create_empty_result()
        result.sources_used = ["mismo_xml"]

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"Failed to parse MISMO XML: {e}")
            result.needs_review = True
            return result

        # Try to detect namespace
        namespace = self._detect_namespace(root)

        # Extract fields using XPath mappings
        for xpath, field_key in MISMO_FIELD_MAPPINGS.items():
            value = self._extract_xpath(root, xpath, namespace)

            if value:
                parts = field_key.split(".", 1)
                if len(parts) == 2:
                    section, field_name = parts
                    result.set_field(
                        section,
                        field_name,
                        FieldResult(
                            value=self._normalize_value(value, field_name),
                            confidence=1.0,  # MISMO is authoritative
                            source="mismo_xml",
                        )
                    )

        # Calculate overall confidence
        from .confidence import aggregate_confidence, should_flag_for_review
        result.overall_confidence = aggregate_confidence(result)
        result.needs_review = should_flag_for_review(result)

        return result

    def _detect_namespace(self, root: ET.Element) -> Optional[str]:
        """Detect the XML namespace used in the document."""
        # Check for common MISMO namespaces
        tag = root.tag
        if "{" in tag:
            return tag.split("}")[0] + "}"
        return None

    def _extract_xpath(
        self,
        root: ET.Element,
        xpath: str,
        namespace: Optional[str]
    ) -> Optional[str]:
        """
        Extract value using XPath expression.

        Args:
            root: XML root element
            xpath: XPath expression (without namespace prefix)
            namespace: Detected namespace URI

        Returns:
            Extracted text value or None
        """
        try:
            # Try with namespace
            if namespace:
                # Convert xpath to use namespace prefix
                ns_xpath = self._add_namespace_prefix(xpath, namespace)
                elements = root.findall(ns_xpath, self.namespaces)
                if elements:
                    return elements[0].text

            # Try without namespace (simple element path)
            simple_path = xpath.replace("//", "").replace("/", "/")
            elements = root.findall(f".//{simple_path}")
            if elements:
                return elements[0].text

            # Try direct iteration for common patterns
            return self._search_by_tag_name(root, xpath)

        except Exception as e:
            logger.debug(f"XPath extraction failed for {xpath}: {e}")
            return None

    def _add_namespace_prefix(self, xpath: str, namespace: str) -> str:
        """Add namespace prefix to XPath elements."""
        # Simple implementation for common patterns
        parts = xpath.split("/")
        prefixed_parts = []
        for part in parts:
            if part and not part.startswith("@"):
                prefixed_parts.append(f"mismo:{part}")
            else:
                prefixed_parts.append(part)
        return "/".join(prefixed_parts)

    def _search_by_tag_name(
        self,
        root: ET.Element,
        xpath: str
    ) -> Optional[str]:
        """
        Fallback search by element tag name.

        Handles cases where namespace or path structure differs.
        """
        # Extract the final element name from xpath
        tag_name = xpath.split("/")[-1]
        if not tag_name or tag_name.startswith("@"):
            return None

        # Search all descendants
        for elem in root.iter():
            # Check tag name (with or without namespace)
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name == tag_name:
                if elem.text and elem.text.strip():
                    return elem.text.strip()

        return None

    def _normalize_value(self, value: str, field_name: str) -> any:
        """
        Normalize extracted value based on field type.

        Converts strings to appropriate types (int, float, etc.)
        """
        if not value:
            return value

        value = value.strip()

        # Numeric fields
        numeric_fields = {
            "contract_price", "appraised_value", "site_value",
            "total_cost_new", "depreciation_total", "real_estate_taxes",
            "gross_living_area", "basement_area_sqft", "area_sqft",
        }

        if field_name in numeric_fields:
            # Remove currency symbols and commas
            cleaned = value.replace("$", "").replace(",", "").strip()
            try:
                if "." in cleaned:
                    return float(cleaned)
                return int(cleaned)
            except ValueError:
                return value

        # Integer fields
        integer_fields = {
            "year_built", "tax_year", "days_on_market",
            "rooms_above_grade", "bedrooms_above_grade",
            "general_description_stories",
        }

        if field_name in integer_fields:
            try:
                return int(value)
            except ValueError:
                return value

        # Float fields (like bathrooms which can be 2.5)
        float_fields = {"bathrooms_above_grade"}

        if field_name in float_fields:
            try:
                return float(value)
            except ValueError:
                return value

        return value

    def can_parse(self, content: str) -> bool:
        """
        Check if content appears to be valid MISMO XML.

        Args:
            content: Raw file content

        Returns:
            True if this looks like MISMO XML
        """
        if not content:
            return False

        content_lower = content.lower()

        # Check for XML declaration and MISMO indicators
        is_xml = "<?xml" in content_lower or "<" in content
        has_mismo = "mismo" in content_lower or "appraisal" in content_lower

        return is_xml and has_mismo


def parse_mismo_file(file_path: str) -> Optional[ExtractionResult]:
    """
    Convenience function to parse a MISMO XML file.

    Args:
        file_path: Path to MISMO XML file

    Returns:
        ExtractionResult or None if parsing fails
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        parser = MISMOParser()
        if not parser.can_parse(content):
            logger.warning(f"File does not appear to be MISMO XML: {file_path}")
            return None

        return parser.parse(content)

    except FileNotFoundError:
        logger.error(f"MISMO file not found: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Failed to parse MISMO file {file_path}: {e}")
        return None
