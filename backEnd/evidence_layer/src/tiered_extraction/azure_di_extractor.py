"""
Azure Document Intelligence Extractor (Tier 2)

Uses Azure's Document Intelligence (formerly Form Recognizer) service
to extract structured data from appraisal PDF documents.

This provides high-accuracy extraction with native confidence scores
for form fields, tables, and key-value pairs.
"""

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

from .confidence import ExtractionResult, FieldResult, create_empty_result
from .field_mappings import AZURE_DI_KEY_MAPPINGS, URAR_SECTIONS

logger = logging.getLogger(__name__)


class AzureDocumentExtractor:
    """
    Extracts appraisal data using Azure Document Intelligence.

    Uses the "prebuilt-layout" model which excels at:
    - Table extraction with structure preservation
    - Key-value pair extraction from forms
    - Reading order detection
    - Handwriting recognition
    """

    def __init__(self):
        self.endpoint = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        self.api_key = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        self.client = None
        self._initialized = False

    def _ensure_client(self) -> bool:
        """Initialize the Azure DI client if not already done."""
        if self._initialized:
            return self.client is not None

        self._initialized = True

        if not self.endpoint or not self.api_key:
            logger.warning(
                "Azure Document Intelligence credentials not configured. "
                "Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY"
            )
            return False

        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential

            self.client = DocumentIntelligenceClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.api_key)
            )
            return True

        except ImportError:
            logger.warning(
                "azure-ai-documentintelligence package not installed. "
                "Run: pip install azure-ai-documentintelligence"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Azure DI client: {e}")
            return False

    async def extract(self, pdf_path: str) -> ExtractionResult:
        """
        Extract appraisal data from PDF using Azure Document Intelligence.

        Args:
            pdf_path: Path to the appraisal PDF file

        Returns:
            ExtractionResult with extracted fields and confidence scores
        """
        result = create_empty_result()
        result.sources_used = ["azure_di"]

        if not self._ensure_client():
            logger.warning("Azure DI client not available, returning empty result")
            result.needs_review = True
            return result

        try:
            # Run the synchronous Azure call in a thread pool
            analysis_result = await asyncio.get_event_loop().run_in_executor(
                None,
                self._analyze_document,
                pdf_path
            )

            if analysis_result is None:
                result.needs_review = True
                return result

            # Extract from key-value pairs
            self._extract_key_value_pairs(analysis_result, result)

            # Extract from tables
            self._extract_from_tables(analysis_result, result)

            # Calculate overall confidence
            from .confidence import aggregate_confidence, should_flag_for_review
            result.overall_confidence = aggregate_confidence(result)
            result.needs_review = should_flag_for_review(result)

            return result

        except Exception as e:
            logger.error(f"Azure DI extraction failed: {e}")
            result.needs_review = True
            return result

    def _analyze_document(self, pdf_path: str) -> Optional[Any]:
        """
        Perform document analysis (synchronous, runs in executor).

        Args:
            pdf_path: Path to PDF file

        Returns:
            Azure DI analysis result or None on failure
        """
        try:
            with open(pdf_path, "rb") as f:
                poller = self.client.begin_analyze_document(
                    "prebuilt-layout",
                    analyze_request=f,
                    content_type="application/pdf"
                )
            return poller.result()

        except FileNotFoundError:
            logger.error(f"PDF file not found: {pdf_path}")
            return None
        except Exception as e:
            logger.error(f"Document analysis failed: {e}")
            return None

    def _extract_key_value_pairs(
        self,
        analysis_result: Any,
        result: ExtractionResult
    ) -> None:
        """
        Extract fields from Azure DI key-value pairs.

        Args:
            analysis_result: Azure DI analysis result
            result: ExtractionResult to populate
        """
        if not hasattr(analysis_result, "key_value_pairs"):
            return

        for kv_pair in analysis_result.key_value_pairs:
            if not kv_pair.key or not kv_pair.value:
                continue

            key_text = kv_pair.key.content.strip()
            value_text = kv_pair.value.content.strip()
            confidence = kv_pair.confidence or 0.0

            # Try to map to our field structure
            field_key = self._match_key_to_field(key_text)
            if field_key:
                parts = field_key.split(".", 1)
                if len(parts) == 2:
                    section, field_name = parts

                    # Get bounding box if available
                    bbox = None
                    if hasattr(kv_pair.value, "bounding_regions") and kv_pair.value.bounding_regions:
                        region = kv_pair.value.bounding_regions[0]
                        if hasattr(region, "polygon"):
                            bbox = {"polygon": region.polygon}

                    result.set_field(
                        section,
                        field_name,
                        FieldResult(
                            value=self._normalize_value(value_text, field_name),
                            confidence=confidence,
                            source="azure_di",
                            bounding_box=bbox
                        )
                    )

    def _extract_from_tables(
        self,
        analysis_result: Any,
        result: ExtractionResult
    ) -> None:
        """
        Extract fields from Azure DI tables.

        URAR forms have structured tables for comparables, cost approach, etc.

        Args:
            analysis_result: Azure DI analysis result
            result: ExtractionResult to populate
        """
        if not hasattr(analysis_result, "tables"):
            return

        for table in analysis_result.tables:
            # Determine table type based on content
            table_type = self._identify_table_type(table)

            if table_type == "sales_comparison":
                self._extract_sales_comparison(table, result)
            elif table_type == "cost_approach":
                self._extract_cost_approach(table, result)
            elif table_type == "subject":
                self._extract_subject_table(table, result)

    def _identify_table_type(self, table: Any) -> Optional[str]:
        """
        Identify the type of URAR table based on content.

        Args:
            table: Azure DI table object

        Returns:
            Table type string or None
        """
        all_text = ""
        for cell in table.cells:
            if cell.content:
                all_text += " " + cell.content.lower()

        if "comparable" in all_text or "sale price" in all_text:
            return "sales_comparison"
        elif "cost new" in all_text or "depreciation" in all_text:
            return "cost_approach"
        elif "property address" in all_text or "borrower" in all_text:
            return "subject"

        return None

    def _extract_sales_comparison(
        self,
        table: Any,
        result: ExtractionResult
    ) -> None:
        """Extract sales comparison data from comparable sales table."""
        # Build table structure
        rows: Dict[int, Dict[int, str]] = {}
        for cell in table.cells:
            row_idx = cell.row_index
            col_idx = cell.column_index
            if row_idx not in rows:
                rows[row_idx] = {}
            rows[row_idx][col_idx] = cell.content

        # Look for Subject and Comparable columns
        # Typically: Column 0 = Field name, Column 1 = Subject, Columns 2-4 = Comparables
        comparables = []
        for col_idx in range(2, min(5, max(rows.get(0, {}).keys(), default=0) + 1)):
            comparable = {}
            for row_idx, row_data in rows.items():
                field_name = row_data.get(0, "").strip()
                value = row_data.get(col_idx, "").strip()
                if field_name and value:
                    comparable[self._normalize_field_name(field_name)] = value
            if comparable:
                comparables.append(comparable)

        # Store comparables
        for i, comp in enumerate(comparables[:3]):
            result.set_field(
                "sales_comparison",
                f"comparable_{i+1}",
                FieldResult(
                    value=comp,
                    confidence=0.85,  # Table extraction confidence
                    source="azure_di"
                )
            )

    def _extract_cost_approach(
        self,
        table: Any,
        result: ExtractionResult
    ) -> None:
        """Extract cost approach data from cost table."""
        for cell in table.cells:
            content = cell.content.lower() if cell.content else ""

            # Look for specific cost approach fields
            if "site value" in content:
                value = self._extract_adjacent_value(table, cell)
                if value:
                    result.set_field(
                        "cost_approach",
                        "site_value",
                        FieldResult(
                            value=self._parse_currency(value),
                            confidence=cell.confidence or 0.85,
                            source="azure_di"
                        )
                    )
            elif "total cost new" in content:
                value = self._extract_adjacent_value(table, cell)
                if value:
                    result.set_field(
                        "cost_approach",
                        "total_cost_new",
                        FieldResult(
                            value=self._parse_currency(value),
                            confidence=cell.confidence or 0.85,
                            source="azure_di"
                        )
                    )

    def _extract_subject_table(
        self,
        table: Any,
        result: ExtractionResult
    ) -> None:
        """Extract subject property data from subject table."""
        # URAR subject section is typically in a structured table format
        # Use regex to extract embedded field values from cell content
        all_text = " ".join(
            cell.content for cell in table.cells if cell.content
        )

        # Extract common fields using patterns
        patterns = {
            "subject.property_address": r'Property Address\s+(.+?)\s+City',
            "subject.city": r'City\s+(\w+)\s+State',
            "subject.state": r'State\s+(\w{2})\s+Zip',
            "subject.zip": r'Zip(?:\s+Code)?\s+(\d+)',
            "subject.county": r'County\s+(\w+)',
            "subject.borrower": r'Borrower\s+(.+?)\s+(?:Owner|County)',
        }

        for field_key, pattern in patterns.items():
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                parts = field_key.split(".", 1)
                if len(parts) == 2:
                    result.set_field(
                        parts[0],
                        parts[1],
                        FieldResult(
                            value=match.group(1).strip(),
                            confidence=0.85,
                            source="azure_di"
                        )
                    )

    def _extract_adjacent_value(
        self,
        table: Any,
        label_cell: Any
    ) -> Optional[str]:
        """
        Get value from cell adjacent to (right of) label cell.

        Args:
            table: Azure DI table
            label_cell: Cell containing the field label

        Returns:
            Value from adjacent cell or None
        """
        target_row = label_cell.row_index
        target_col = label_cell.column_index + 1

        for cell in table.cells:
            if cell.row_index == target_row and cell.column_index == target_col:
                return cell.content

        return None

    def _match_key_to_field(self, key_text: str) -> Optional[str]:
        """
        Match Azure DI key text to our field mapping.

        Args:
            key_text: Extracted key text from document

        Returns:
            Field key (e.g., "subject.property_address") or None
        """
        key_lower = key_text.lower().strip()

        # Direct match
        for azure_key, field_key in AZURE_DI_KEY_MAPPINGS.items():
            if azure_key.lower() == key_lower:
                return field_key

        # Fuzzy match for common variations
        fuzzy_mappings = {
            "address": "subject.property_address",
            "property addr": "subject.property_address",
            "sale price": "listing_and_contract.contract_price",
            "purchase price": "listing_and_contract.contract_price",
            "yr built": "improvements.year_built",
            "year": "improvements.year_built",
            "gla": "improvements.gross_living_area",
            "living area": "improvements.gross_living_area",
            "sq ft": "improvements.gross_living_area",
            "market value": "reconciliation.final_opinion_of_market_value",
            "appraised value": "reconciliation.final_opinion_of_market_value",
        }

        for pattern, field_key in fuzzy_mappings.items():
            if pattern in key_lower:
                return field_key

        return None

    def _normalize_field_name(self, name: str) -> str:
        """Convert field name to snake_case."""
        # Remove special characters and convert to lowercase
        name = re.sub(r'[^a-zA-Z0-9\s]', '', name)
        name = name.lower().strip()
        return re.sub(r'\s+', '_', name)

    def _normalize_value(self, value: str, field_name: str) -> Any:
        """Normalize extracted value based on field type."""
        if not value:
            return value

        value = value.strip()

        # Currency fields
        currency_fields = {
            "contract_price", "appraised_value", "site_value",
            "total_cost_new", "real_estate_taxes", "offering_price",
            "prior_sale_price", "seller_concessions",
        }

        if field_name in currency_fields:
            return self._parse_currency(value)

        # Numeric fields
        numeric_fields = {
            "gross_living_area", "basement_area_sqft", "area_sqft",
            "days_on_market", "year_built", "tax_year",
        }

        if field_name in numeric_fields:
            cleaned = re.sub(r'[^\d.]', '', value)
            try:
                return int(float(cleaned)) if cleaned else value
            except ValueError:
                return value

        return value

    def _parse_currency(self, value: str) -> int:
        """Parse currency string to integer."""
        if not value:
            return 0

        # Remove currency symbols, commas, spaces
        cleaned = re.sub(r'[$,\s]', '', value)

        try:
            return int(float(cleaned))
        except ValueError:
            return 0

    def is_available(self) -> bool:
        """Check if Azure DI is configured and available."""
        return self._ensure_client()
