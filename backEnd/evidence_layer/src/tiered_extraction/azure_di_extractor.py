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
        # Load from settings (which reads .env) with fallback to os.environ
        try:
            from agentic.config.settings import get_settings
            settings = get_settings()
            self.endpoint = settings.azure_document_intelligence_endpoint or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
            self.api_key = settings.azure_document_intelligence_key or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        except ImportError:
            # Fallback if settings module not available
            self.endpoint = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
            self.api_key = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        self.client = None
        self._initialized = False
        self.DocumentAnalysisFeature = None

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
            from azure.ai.documentintelligence.models import DocumentAnalysisFeature
            from azure.core.credentials import AzureKeyCredential

            self.client = DocumentIntelligenceClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.api_key)
            )
            self.DocumentAnalysisFeature = DocumentAnalysisFeature
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

            # Track extraction statistics
            kv_count = len(analysis_result.key_value_pairs) if analysis_result.key_value_pairs else 0
            table_count = len(analysis_result.tables) if analysis_result.tables else 0

            # Extract from key-value pairs
            matched_kv = self._extract_key_value_pairs(analysis_result, result)

            # Extract from tables
            matched_tables = self._extract_from_tables(analysis_result, result)

            # Log extraction statistics
            total_fields = sum(len(section) for section in result.sections.values())
            logger.info(
                f"Azure DI extraction stats: "
                f"{kv_count} key-value pairs ({matched_kv} matched), "
                f"{table_count} tables ({matched_tables} fields extracted), "
                f"{total_fields} total fields"
            )

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
                # SDK 1.0.x uses model_id and body parameters
                # Enable KEY_VALUE_PAIRS feature to extract form fields
                poller = self.client.begin_analyze_document(
                    model_id="prebuilt-layout",
                    body=f,
                    content_type="application/pdf",
                    features=[self.DocumentAnalysisFeature.KEY_VALUE_PAIRS]
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
    ) -> int:
        """
        Extract fields from Azure DI key-value pairs.

        Args:
            analysis_result: Azure DI analysis result
            result: ExtractionResult to populate

        Returns:
            Number of key-value pairs successfully matched
        """
        matched_count = 0

        if not hasattr(analysis_result, "key_value_pairs"):
            return matched_count

        # Guard against None key_value_pairs
        if analysis_result.key_value_pairs is None:
            return matched_count

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
                    matched_count += 1

        return matched_count

    def _extract_from_tables(
        self,
        analysis_result: Any,
        result: ExtractionResult
    ) -> int:
        """
        Extract fields from Azure DI tables.

        URAR forms have structured tables for comparables, cost approach, etc.

        Args:
            analysis_result: Azure DI analysis result
            result: ExtractionResult to populate

        Returns:
            Number of fields extracted from tables
        """
        fields_extracted = 0

        if not hasattr(analysis_result, "tables"):
            return fields_extracted

        # Guard against None tables
        if analysis_result.tables is None:
            return fields_extracted

        for table in analysis_result.tables:
            # Determine table type based on content
            table_type = self._identify_table_type(table)

            if table_type == "sales_comparison":
                fields_extracted += self._extract_sales_comparison(table, result)
            elif table_type == "cost_approach":
                fields_extracted += self._extract_cost_approach(table, result)
            elif table_type == "subject":
                fields_extracted += self._extract_subject_table(table, result)

        return fields_extracted

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
    ) -> int:
        """Extract sales comparison data from comparable sales table.

        Returns:
            Number of fields extracted
        """
        fields_extracted = 0

        # Build table structure with confidence tracking
        rows: Dict[int, Dict[int, str]] = {}
        cell_confidences: Dict[tuple, float] = {}  # (row, col) -> confidence

        for cell in table.cells:
            row_idx = cell.row_index
            col_idx = cell.column_index
            if row_idx not in rows:
                rows[row_idx] = {}
            rows[row_idx][col_idx] = cell.content
            # Track cell confidence from Azure DI
            cell_confidences[(row_idx, col_idx)] = getattr(cell, 'confidence', None) or 0.95

        # Look for Subject and Comparable columns
        # Typically: Column 0 = Field name, Column 1 = Subject, Columns 2-4 = Comparables
        comparables = []
        comparable_confidences = []

        for col_idx in range(2, min(5, max(rows.get(0, {}).keys(), default=0) + 1)):
            comparable = {}
            col_conf_values = []
            for row_idx, row_data in rows.items():
                field_name = row_data.get(0, "").strip()
                value = row_data.get(col_idx, "").strip()
                if field_name and value:
                    comparable[self._normalize_field_name(field_name)] = value
                    col_conf_values.append(cell_confidences.get((row_idx, col_idx), 0.95))
            if comparable:
                comparables.append(comparable)
                # Use average confidence for this comparable
                avg_conf = sum(col_conf_values) / len(col_conf_values) if col_conf_values else 0.95
                comparable_confidences.append(avg_conf)

        # Store comparables with actual confidence
        for i, comp in enumerate(comparables[:3]):
            result.set_field(
                "sales_comparison",
                f"comparable_{i+1}",
                FieldResult(
                    value=comp,
                    confidence=comparable_confidences[i] if i < len(comparable_confidences) else 0.95,
                    source="azure_di"
                )
            )
            fields_extracted += 1

        return fields_extracted

    def _extract_cost_approach(
        self,
        table: Any,
        result: ExtractionResult
    ) -> int:
        """Extract cost approach data from cost table.

        Returns:
            Number of fields extracted
        """
        fields_extracted = 0

        for cell in table.cells:
            content = cell.content.lower() if cell.content else ""

            # Look for specific cost approach fields
            if "site value" in content:
                value, value_confidence = self._extract_adjacent_value_with_confidence(table, cell)
                if value:
                    result.set_field(
                        "cost_approach",
                        "site_value",
                        FieldResult(
                            value=self._parse_currency(value),
                            confidence=value_confidence,
                            source="azure_di"
                        )
                    )
                    fields_extracted += 1
            elif "total cost new" in content:
                value, value_confidence = self._extract_adjacent_value_with_confidence(table, cell)
                if value:
                    result.set_field(
                        "cost_approach",
                        "total_cost_new",
                        FieldResult(
                            value=self._parse_currency(value),
                            confidence=value_confidence,
                            source="azure_di"
                        )
                    )
                    fields_extracted += 1

        return fields_extracted

    def _extract_subject_table(
        self,
        table: Any,
        result: ExtractionResult
    ) -> int:
        """Extract subject property data from subject table.

        Returns:
            Number of fields extracted
        """
        fields_extracted = 0

        # URAR subject section is typically in a structured table format
        # Use regex to extract embedded field values from cell content
        all_text = " ".join(
            cell.content for cell in table.cells if cell.content
        )

        # Calculate average confidence from table cells
        cell_confidences = [
            getattr(cell, 'confidence', None) or 0.95
            for cell in table.cells
            if cell.content
        ]
        avg_table_confidence = sum(cell_confidences) / len(cell_confidences) if cell_confidences else 0.95

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
                            confidence=avg_table_confidence,
                            source="azure_di"
                        )
                    )
                    fields_extracted += 1

        return fields_extracted

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

    def _extract_adjacent_value_with_confidence(
        self,
        table: Any,
        label_cell: Any
    ) -> tuple:
        """
        Get value and confidence from cell adjacent to (right of) label cell.

        Args:
            table: Azure DI table
            label_cell: Cell containing the field label

        Returns:
            Tuple of (value, confidence) from adjacent cell, or (None, 0.95) if not found
        """
        target_row = label_cell.row_index
        target_col = label_cell.column_index + 1

        for cell in table.cells:
            if cell.row_index == target_row and cell.column_index == target_col:
                confidence = getattr(cell, 'confidence', None) or 0.95
                return cell.content, confidence

        return None, 0.95

    def _match_key_to_field(self, key_text: str) -> Optional[str]:
        """
        Match Azure DI key text to our field mapping.

        Uses direct match first, then extensive fuzzy matching for
        common URAR form label variations.

        Handles:
        - Case variations: "PROPERTY ADDRESS" → "property address"
        - Trailing punctuation: "Year Built:" → "year built"
        - Partial matching: "Subject Property Address" contains "property address"

        Args:
            key_text: Extracted key text from document

        Returns:
            Field key (e.g., "subject.property_address") or None
        """
        # Normalize: lowercase, strip whitespace and trailing punctuation
        key_lower = key_text.lower().strip().rstrip(':;.,')

        # Direct match (exact after normalization)
        for azure_key, field_key in AZURE_DI_KEY_MAPPINGS.items():
            azure_normalized = azure_key.lower().strip().rstrip(':;.,')
            if azure_normalized == key_lower:
                return field_key

        # Extensive fuzzy match for common URAR form variations
        # Ordered by specificity (more specific patterns first)
        fuzzy_mappings = [
            # Subject section - address variations
            ("property address", "subject.property_address"),
            ("subject address", "subject.property_address"),
            ("property addr", "subject.property_address"),
            ("street address", "subject.property_address"),
            ("site address", "subject.property_address"),

            # Subject section - other fields
            ("owner of record", "subject.owner_of_public_record"),
            ("public record", "subject.owner_of_public_record"),
            ("legal desc", "subject.legal_description"),
            ("parcel #", "subject.assessors_parcel"),
            ("parcel no", "subject.assessors_parcel"),
            ("apn", "subject.assessors_parcel"),
            ("r.e. tax", "subject.real_estate_taxes"),
            ("real estate tax", "subject.real_estate_taxes"),
            ("property tax", "subject.real_estate_taxes"),

            # Listing/Contract - price variations
            ("contract price", "listing_and_contract.contract_price"),
            ("sale price", "listing_and_contract.contract_price"),
            ("sales price", "listing_and_contract.contract_price"),
            ("purchase price", "listing_and_contract.contract_price"),
            ("selling price", "listing_and_contract.contract_price"),
            ("offering price", "listing_and_contract.offering_price"),
            ("list price", "listing_and_contract.offering_price"),
            ("asking price", "listing_and_contract.offering_price"),
            ("date of contract", "listing_and_contract.contract_date"),
            ("contract date", "listing_and_contract.contract_date"),
            ("sale date", "listing_and_contract.contract_date"),
            ("days on market", "listing_and_contract.days_on_market"),
            ("dom", "listing_and_contract.days_on_market"),
            ("lender/client", "listing_and_contract.lender_client"),
            ("lender", "listing_and_contract.lender_client"),
            ("client", "listing_and_contract.lender_client"),

            # Improvements - year built variations
            ("year built", "improvements.year_built"),
            ("yr built", "improvements.year_built"),
            ("year constructed", "improvements.year_built"),
            ("built in", "improvements.year_built"),
            ("construction year", "improvements.year_built"),
            ("date built", "improvements.year_built"),
            ("effective age", "improvements.effective_age"),
            ("eff age", "improvements.effective_age"),
            ("actual age", "improvements.effective_age"),

            # Improvements - GLA variations (CRITICAL)
            ("gross living area", "improvements.gross_living_area"),
            ("gross living", "improvements.gross_living_area"),
            ("living area", "improvements.gross_living_area"),
            ("gla", "improvements.gross_living_area"),
            ("total living", "improvements.gross_living_area"),
            ("finished area", "improvements.gross_living_area"),
            ("heated area", "improvements.gross_living_area"),
            ("sq ft living", "improvements.gross_living_area"),
            ("square feet", "improvements.gross_living_area"),
            ("above grade", "improvements.finished_area_above_grade"),

            # Improvements - rooms/beds/baths
            ("total rooms", "improvements.rooms_above_grade"),
            ("room count", "improvements.rooms_above_grade"),
            ("# of rooms", "improvements.rooms_above_grade"),
            ("bedrooms", "improvements.bedrooms_above_grade"),
            ("beds", "improvements.bedrooms_above_grade"),
            ("# of beds", "improvements.bedrooms_above_grade"),
            ("bathrooms", "improvements.bathrooms_above_grade"),
            ("baths", "improvements.bathrooms_above_grade"),
            ("full bath", "improvements.bathrooms_above_grade"),

            # Improvements - basement
            ("basement area", "improvements.basement_area_sqft"),
            ("basement sq", "improvements.basement_area_sqft"),
            ("basement size", "improvements.basement_area_sqft"),
            ("finished basement", "improvements.basement_finished_sqft"),

            # Improvements - structure
            ("design style", "improvements.general_description_design_style"),
            ("style", "improvements.general_description_design_style"),
            ("design", "improvements.general_description_design_style"),
            ("# stories", "improvements.general_description_stories"),
            ("stories", "improvements.general_description_stories"),
            ("story", "improvements.general_description_stories"),
            ("foundation", "improvements.foundation_type"),

            # Site section
            ("lot size", "site.area_sqft"),
            ("site area", "site.area_sqft"),
            ("lot area", "site.area_sqft"),
            ("acreage", "site.area_acres"),
            ("acres", "site.area_acres"),
            ("zoning", "site.zoning_classification"),
            ("flood zone", "site.fema_flood_zone"),
            ("fema", "site.fema_flood_zone"),

            # Reconciliation - value variations (CRITICAL)
            ("opinion of value", "reconciliation.final_opinion_of_market_value"),
            ("market value", "reconciliation.final_opinion_of_market_value"),
            ("appraised value", "reconciliation.final_opinion_of_market_value"),
            ("as is value", "reconciliation.final_opinion_of_market_value"),
            ("final value", "reconciliation.final_opinion_of_market_value"),
            ("value conclusion", "reconciliation.final_opinion_of_market_value"),
            ("estimated value", "reconciliation.final_opinion_of_market_value"),
            ("indicated value", "reconciliation.indicated_value_sales_comparison"),
            ("effective date", "reconciliation.effective_date"),
            ("date of value", "reconciliation.effective_date"),
            ("valuation date", "reconciliation.effective_date"),
            ("as of date", "reconciliation.effective_date"),
            ("appraiser", "reconciliation.appraiser_name"),

            # Cost approach
            ("site value", "cost_approach.site_value"),
            ("land value", "cost_approach.site_value"),
            ("lot value", "cost_approach.site_value"),
            ("total cost new", "cost_approach.total_cost_new"),
            ("cost new", "cost_approach.total_cost_new"),
            ("replacement cost", "cost_approach.total_cost_new"),
            ("reproduction cost", "cost_approach.total_cost_new"),
            ("depreciation", "cost_approach.depreciation_total"),
            ("physical deprec", "cost_approach.depreciation_physical"),
            ("functional deprec", "cost_approach.depreciation_functional"),
            ("external deprec", "cost_approach.depreciation_external"),

            # Generic patterns (less specific - check last)
            ("address", "subject.property_address"),
        ]

        for pattern, field_key in fuzzy_mappings:
            if pattern in key_lower:
                return field_key

        # Log unmatched keys for debugging (helps identify new patterns)
        if len(key_lower) > 2 and len(key_lower) < 50:
            logger.debug(f"Azure DI key not matched: '{key_text}'")

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
