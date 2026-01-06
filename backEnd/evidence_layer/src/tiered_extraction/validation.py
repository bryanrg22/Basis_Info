"""
Validation Layer (Tier 5)

Performs cross-field validation and consistency checks on
extracted appraisal data. Flags results that need manual review.

Validation rules are based on:
- URAR form requirements
- Cost segregation analysis needs
- Data consistency checks
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .confidence import ExtractionResult, FieldResult
from .field_mappings import CRITICAL_FIELDS, CONFIDENCE_THRESHOLDS

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a single validation check."""

    def __init__(
        self,
        field_key: str,
        passed: bool,
        message: str,
        severity: str = "warning"  # "error" | "warning" | "info"
    ):
        self.field_key = field_key
        self.passed = passed
        self.message = message
        self.severity = severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field_key,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity,
        }


class AppraisalValidator:
    """
    Validates extracted appraisal data for consistency and completeness.

    Performs:
    - Critical field presence checks
    - Value range validation
    - Cross-field consistency checks
    - Date format validation
    """

    def __init__(self):
        self.validation_results: List[ValidationResult] = []

    def validate(self, result: ExtractionResult) -> ExtractionResult:
        """
        Run all validation checks on extraction result.

        Args:
            result: ExtractionResult to validate

        Returns:
            Updated ExtractionResult with needs_review flag set appropriately
        """
        self.validation_results = []

        # Run all validation checks
        self._validate_critical_fields(result)
        self._validate_year_built(result)
        self._validate_dates(result)
        self._validate_currency_fields(result)
        self._validate_area_consistency(result)
        self._validate_value_consistency(result)

        # Update needs_review based on validation
        has_errors = any(
            not vr.passed and vr.severity == "error"
            for vr in self.validation_results
        )

        has_critical_warnings = any(
            not vr.passed and vr.severity == "warning"
            and vr.field_key.split(".")[-1] in CRITICAL_FIELDS
            for vr in self.validation_results
        )

        if has_errors or has_critical_warnings:
            result.needs_review = True

        return result

    def get_validation_summary(self) -> Dict[str, Any]:
        """Get summary of validation results."""
        passed = [vr for vr in self.validation_results if vr.passed]
        failed = [vr for vr in self.validation_results if not vr.passed]

        return {
            "total_checks": len(self.validation_results),
            "passed": len(passed),
            "failed": len(failed),
            "errors": [vr.to_dict() for vr in failed if vr.severity == "error"],
            "warnings": [vr.to_dict() for vr in failed if vr.severity == "warning"],
        }

    def _validate_critical_fields(self, result: ExtractionResult) -> None:
        """Check that all critical fields are present and have sufficient confidence."""
        critical_threshold = CONFIDENCE_THRESHOLDS["critical"]

        for section_name, fields in result.sections.items():
            for field_name, field_result in fields.items():
                if field_name in CRITICAL_FIELDS:
                    field_key = f"{section_name}.{field_name}"

                    # Check presence
                    if field_result.value is None or field_result.value == "":
                        self.validation_results.append(ValidationResult(
                            field_key=field_key,
                            passed=False,
                            message=f"Critical field '{field_name}' is missing",
                            severity="error"
                        ))
                        continue

                    # Check confidence
                    if field_result.confidence < critical_threshold:
                        self.validation_results.append(ValidationResult(
                            field_key=field_key,
                            passed=False,
                            message=f"Critical field '{field_name}' has low confidence ({field_result.confidence:.2f})",
                            severity="warning"
                        ))
                    else:
                        self.validation_results.append(ValidationResult(
                            field_key=field_key,
                            passed=True,
                            message=f"Critical field '{field_name}' validated",
                            severity="info"
                        ))

    def _validate_year_built(self, result: ExtractionResult) -> None:
        """Validate year_built is a reasonable year."""
        year_built = result.get_field("improvements", "year_built")
        if not year_built or year_built.value is None:
            return

        value = year_built.value
        field_key = "improvements.year_built"

        # Convert to int if string
        if isinstance(value, str):
            match = re.search(r'\d{4}', value)
            if match:
                value = int(match.group())
            else:
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=False,
                    message=f"year_built '{value}' is not a valid year format",
                    severity="error"
                ))
                return

        current_year = datetime.now().year

        if not isinstance(value, int):
            try:
                value = int(value)
            except (ValueError, TypeError):
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=False,
                    message=f"year_built could not be parsed as integer",
                    severity="error"
                ))
                return

        # Check reasonable range
        if value < 1800:
            self.validation_results.append(ValidationResult(
                field_key=field_key,
                passed=False,
                message=f"year_built {value} is before 1800 - verify historical property",
                severity="warning"
            ))
        elif value > current_year + 2:
            self.validation_results.append(ValidationResult(
                field_key=field_key,
                passed=False,
                message=f"year_built {value} is in the future",
                severity="error"
            ))
        else:
            self.validation_results.append(ValidationResult(
                field_key=field_key,
                passed=True,
                message=f"year_built {value} is valid",
                severity="info"
            ))

    def _validate_dates(self, result: ExtractionResult) -> None:
        """Validate date fields are in correct format."""
        date_fields = [
            ("listing_and_contract", "contract_date"),
            ("reconciliation", "effective_date"),
            ("listing_and_contract", "prior_sale_date"),
        ]

        for section, field_name in date_fields:
            field_result = result.get_field(section, field_name)
            if not field_result or not field_result.value:
                continue

            field_key = f"{section}.{field_name}"
            value = field_result.value

            # Try to parse as date
            date_formats = [
                "%m/%d/%Y",
                "%Y-%m-%d",
                "%m-%d-%Y",
                "%d/%m/%Y",
            ]

            parsed = False
            for fmt in date_formats:
                try:
                    datetime.strptime(str(value), fmt)
                    parsed = True
                    break
                except ValueError:
                    continue

            if parsed:
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=True,
                    message=f"{field_name} has valid date format",
                    severity="info"
                ))
            else:
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=False,
                    message=f"{field_name} '{value}' is not a recognized date format",
                    severity="warning"
                ))

    def _validate_currency_fields(self, result: ExtractionResult) -> None:
        """Validate currency fields are reasonable positive numbers."""
        currency_fields = [
            ("listing_and_contract", "contract_price"),
            ("reconciliation", "final_opinion_of_market_value"),
            ("cost_approach", "site_value"),
            ("cost_approach", "total_cost_new"),
        ]

        for section, field_name in currency_fields:
            field_result = result.get_field(section, field_name)
            if not field_result or field_result.value is None:
                continue

            field_key = f"{section}.{field_name}"
            value = field_result.value

            # Convert to number
            if isinstance(value, str):
                cleaned = re.sub(r'[$,\s]', '', value)
                try:
                    value = float(cleaned)
                except ValueError:
                    self.validation_results.append(ValidationResult(
                        field_key=field_key,
                        passed=False,
                        message=f"{field_name} could not be parsed as currency",
                        severity="warning"
                    ))
                    continue

            # Check reasonable range
            if value < 0:
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=False,
                    message=f"{field_name} is negative: ${value:,.0f}",
                    severity="error"
                ))
            elif value > 100_000_000:  # $100M
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=False,
                    message=f"{field_name} seems unusually high: ${value:,.0f} - verify",
                    severity="warning"
                ))
            else:
                self.validation_results.append(ValidationResult(
                    field_key=field_key,
                    passed=True,
                    message=f"{field_name} is valid",
                    severity="info"
                ))

    def _validate_area_consistency(self, result: ExtractionResult) -> None:
        """Check that area-related fields are consistent."""
        gla = result.get_field("improvements", "gross_living_area")
        basement = result.get_field("improvements", "basement_area_sqft")
        finished_above = result.get_field("improvements", "finished_area_above_grade")

        if not gla or gla.value is None:
            return

        gla_value = self._to_number(gla.value)
        if gla_value is None:
            return

        # GLA should be reasonable
        if gla_value < 100:
            self.validation_results.append(ValidationResult(
                field_key="improvements.gross_living_area",
                passed=False,
                message=f"GLA {gla_value} sq ft seems too small",
                severity="warning"
            ))
        elif gla_value > 50000:
            self.validation_results.append(ValidationResult(
                field_key="improvements.gross_living_area",
                passed=False,
                message=f"GLA {gla_value} sq ft seems unusually large - verify",
                severity="warning"
            ))

        # Check basement vs GLA ratio
        if basement and basement.value:
            basement_value = self._to_number(basement.value)
            if basement_value and basement_value > gla_value * 1.5:
                self.validation_results.append(ValidationResult(
                    field_key="improvements.basement_area_sqft",
                    passed=False,
                    message=f"Basement ({basement_value} sq ft) is larger than 1.5x GLA ({gla_value} sq ft) - verify",
                    severity="warning"
                ))

    def _validate_value_consistency(self, result: ExtractionResult) -> None:
        """Check that appraised value is consistent with contract price."""
        contract = result.get_field("listing_and_contract", "contract_price")
        appraised = result.get_field("reconciliation", "final_opinion_of_market_value")

        if not contract or not appraised:
            return

        contract_value = self._to_number(contract.value)
        appraised_value = self._to_number(appraised.value)

        if contract_value is None or appraised_value is None:
            return

        if contract_value == 0 or appraised_value == 0:
            return

        # Calculate variance
        variance = abs(appraised_value - contract_value) / contract_value

        if variance > 0.20:  # More than 20% difference
            self.validation_results.append(ValidationResult(
                field_key="reconciliation.final_opinion_of_market_value",
                passed=False,
                message=f"Appraised value (${appraised_value:,.0f}) differs from contract price (${contract_value:,.0f}) by {variance:.0%}",
                severity="warning"
            ))
        else:
            self.validation_results.append(ValidationResult(
                field_key="reconciliation.final_opinion_of_market_value",
                passed=True,
                message=f"Appraised value is within 20% of contract price",
                severity="info"
            ))

    def _to_number(self, value: Any) -> Optional[float]:
        """Convert value to number, returning None if not possible."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            cleaned = re.sub(r'[$,\s]', '', value)
            try:
                return float(cleaned)
            except ValueError:
                return None

        return None


def validate_and_flag(result: ExtractionResult) -> ExtractionResult:
    """
    Convenience function to validate extraction result.

    Args:
        result: ExtractionResult to validate

    Returns:
        Validated ExtractionResult with needs_review flag updated
    """
    validator = AppraisalValidator()
    return validator.validate(result)
