"""
Cross-stage validation for workflow consistency.

Validates data consistency across workflow stages:
- Classification ↔ Takeoff alignment
- Takeoff ↔ Cost validation
- Industry standard compliance

Phase 5: Workflow Reliability Implementation
"""

import logging
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Models
# =============================================================================


class IssueSeverity(str, Enum):
    """Severity levels for validation issues."""

    INFO = "info"  # Informational, no action needed
    WARNING = "warning"  # Needs review, may be correct
    ERROR = "error"  # Likely incorrect, requires attention


class ValidationIssue(BaseModel):
    """A single validation issue."""

    severity: IssueSeverity = Field(..., description="Issue severity")
    code: str = Field(..., description="Issue code for programmatic handling")
    message: str = Field(..., description="Human-readable message")
    stage: str = Field(..., description="Stage where issue was detected")
    component_id: Optional[str] = Field(default=None, description="Related component")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    suggestion: Optional[str] = Field(default=None, description="Suggested fix")


class ValidationResult(BaseModel):
    """Result of cross-stage validation."""

    is_valid: bool = Field(default=True, description="No errors found")
    issues: list[ValidationIssue] = Field(default_factory=list, description="All issues found")

    @property
    def has_issues(self) -> bool:
        """Check if any issues were found."""
        return len(self.issues) > 0

    @property
    def has_warnings(self) -> bool:
        """Check if any warnings were found."""
        return any(i.severity == IssueSeverity.WARNING for i in self.issues)

    @property
    def has_errors(self) -> bool:
        """Check if any errors were found."""
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)

    @property
    def warning_count(self) -> int:
        """Count of warning issues."""
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING)

    @property
    def error_count(self) -> int:
        """Count of error issues."""
        return sum(1 for i in self.issues if i.severity == IssueSeverity.ERROR)

    def add_issue(self, issue: ValidationIssue) -> None:
        """Add an issue to the result."""
        self.issues.append(issue)
        if issue.severity == IssueSeverity.ERROR:
            self.is_valid = False

    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.issues.extend(other.issues)
        if not other.is_valid:
            self.is_valid = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "is_valid": self.is_valid,
            "issues": [i.model_dump() for i in self.issues],
            "warning_count": self.warning_count,
            "error_count": self.error_count,
        }


# =============================================================================
# Industry Standards Reference Data
# =============================================================================


# IRS Section 1245 typically covers personal property (5-7 year)
# IRS Section 1250 covers real property (15-39 year)
SECTION_TYPICAL_UNITS = {
    "1245": ["EA", "LF"],  # Equipment, fixtures - typically counted
    "1250": ["SF", "SY"],  # Real property - typically area-based
}

# Expected unit types by depreciation bucket
BUCKET_TYPICAL_UNITS = {
    "5_year": ["EA", "LF"],  # Personal property
    "7_year": ["EA", "LF"],  # Office furniture, fixtures
    "15_year": ["SF", "LF", "EA"],  # Land improvements
    "27_5_year": ["SF"],  # Residential rental
    "39_year": ["SF", "SY"],  # Non-residential real property
}

# Typical cost ranges by component type ($/unit)
COST_RANGES = {
    "light_fixture": {"min": 20, "max": 1000, "unit": "EA"},
    "electrical_outlet": {"min": 15, "max": 150, "unit": "EA"},
    "hvac_unit": {"min": 1000, "max": 15000, "unit": "EA"},
    "carpet": {"min": 2, "max": 30, "unit": "SF"},
    "tile": {"min": 3, "max": 50, "unit": "SF"},
    "cabinet": {"min": 80, "max": 800, "unit": "LF"},
    "countertop": {"min": 20, "max": 250, "unit": "SF"},
    "door": {"min": 150, "max": 3000, "unit": "EA"},
    "window": {"min": 200, "max": 2500, "unit": "EA"},
    "plumbing_fixture": {"min": 100, "max": 5000, "unit": "EA"},
    "paint": {"min": 1, "max": 8, "unit": "SF"},
    "fire_sprinkler": {"min": 50, "max": 300, "unit": "EA"},
}

# Components that should NOT have large SF quantities under 1245
SECTION_1245_SF_THRESHOLD = 100  # Warning if SF > 100 under section 1245


# =============================================================================
# Cross Validator
# =============================================================================


class CrossValidator:
    """
    Validates consistency across workflow stages.

    Checks:
    1. Classification ↔ Takeoff: Section vs unit type alignment
    2. Takeoff ↔ Cost: Unit cost within industry ranges
    3. Component type consistency
    """

    def validate_classification_takeoff(
        self,
        classification: dict[str, Any],
        takeoff: dict[str, Any],
    ) -> ValidationResult:
        """
        Validate alignment between classification and takeoff.

        Rules:
        1. Section 1245 with large SF quantity → warning (likely structural)
        2. 39-year bucket with EA unit → info (typically SF/LF)
        3. Section mismatch with typical component patterns
        """
        result = ValidationResult()

        # Extract data
        component_name = takeoff.get("component_name", "unknown")
        component_id = takeoff.get("component_id", classification.get("component_id"))

        # Get classification details
        clf = classification.get("classification", {})
        section = clf.get("irs_section", "")
        bucket = clf.get("depreciation_bucket", "")
        recovery_period = clf.get("recovery_period_years", 0)

        # Get takeoff details
        takeoff_result = takeoff.get("takeoff", {}) or {}
        quantity = takeoff_result.get("quantity", 0)
        unit = takeoff_result.get("unit", "EA")

        # Rule 1: Section 1245 with large SF quantity
        if "1245" in str(section) and unit == "SF":
            if quantity > SECTION_1245_SF_THRESHOLD:
                result.add_issue(ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    code="SECTION_1245_SF_LARGE",
                    message=f"Section 1245 with {quantity} SF is unusual. 1245 typically covers equipment/fixtures counted in EA, not large areas.",
                    stage="classification_takeoff",
                    component_id=component_id,
                    details={
                        "component": component_name,
                        "section": section,
                        "quantity": quantity,
                        "unit": unit,
                    },
                    suggestion="Verify if this should be Section 1250 (real property) or if quantity/unit is correct.",
                ))

        # Rule 2: 39-year bucket with EA unit
        if "39" in str(bucket) or recovery_period == 39:
            if unit == "EA":
                result.add_issue(ValidationIssue(
                    severity=IssueSeverity.INFO,
                    code="39_YEAR_EA_UNIT",
                    message=f"39-year property '{component_name}' measured in EA. Non-residential property is typically measured in SF/LF.",
                    stage="classification_takeoff",
                    component_id=component_id,
                    details={
                        "component": component_name,
                        "bucket": bucket,
                        "unit": unit,
                    },
                    suggestion="Consider if SF or LF would be more appropriate for cost estimation.",
                ))

        # Rule 3: Check typical units for bucket
        bucket_key = bucket.lower().replace("-", "_").replace(" ", "_") if bucket else ""
        for key, typical_units in BUCKET_TYPICAL_UNITS.items():
            if key in bucket_key:
                if unit not in typical_units:
                    result.add_issue(ValidationIssue(
                        severity=IssueSeverity.INFO,
                        code="UNIT_BUCKET_MISMATCH",
                        message=f"Unit '{unit}' is unusual for {key} depreciation bucket. Typical: {typical_units}",
                        stage="classification_takeoff",
                        component_id=component_id,
                        details={
                            "component": component_name,
                            "bucket": bucket,
                            "unit": unit,
                            "typical_units": typical_units,
                        },
                    ))
                break

        return result

    def validate_takeoff_cost(
        self,
        takeoff: dict[str, Any],
        cost: dict[str, Any],
    ) -> ValidationResult:
        """
        Validate alignment between takeoff and cost estimation.

        Rules:
        1. Unit cost within industry range for component type
        2. RSMeans line item found
        3. Total cost proportional to quantity
        """
        result = ValidationResult()

        # Extract data
        component_name = takeoff.get("component_name", "unknown")
        component_id = takeoff.get("component_id")

        takeoff_result = takeoff.get("takeoff", {}) or {}
        quantity = takeoff_result.get("quantity", 0)
        unit = takeoff_result.get("unit", "EA")

        estimate = cost.get("estimate", {}) or {}
        total_cost_per_unit = estimate.get("total_cost_per_unit", 0)
        final_cost = estimate.get("final_cost", 0)
        rsmeans_line_item = estimate.get("rsmeans_line_item")

        # Rule 1: RSMeans line item found
        if not rsmeans_line_item:
            result.add_issue(ValidationIssue(
                severity=IssueSeverity.WARNING,
                code="RSMEANS_NOT_FOUND",
                message=f"No RSMeans line item found for '{component_name}'. Cost estimate may be less accurate.",
                stage="takeoff_cost",
                component_id=component_id,
                details={
                    "component": component_name,
                },
                suggestion="Review component description for better RSMeans matching or verify with manual lookup.",
            ))

        # Rule 2: Unit cost within industry range
        component_lower = component_name.lower().replace(" ", "_")
        for key, ranges in COST_RANGES.items():
            if key in component_lower or component_lower in key:
                min_cost = ranges["min"]
                max_cost = ranges["max"]
                expected_unit = ranges["unit"]

                # Only check if units match
                if unit == expected_unit and total_cost_per_unit > 0:
                    if total_cost_per_unit < min_cost * 0.5:  # 50% below minimum
                        result.add_issue(ValidationIssue(
                            severity=IssueSeverity.WARNING,
                            code="COST_BELOW_RANGE",
                            message=f"Unit cost ${total_cost_per_unit:.2f}/{unit} for '{component_name}' is significantly below typical range (${min_cost}-${max_cost}).",
                            stage="takeoff_cost",
                            component_id=component_id,
                            details={
                                "component": component_name,
                                "unit_cost": total_cost_per_unit,
                                "expected_min": min_cost,
                                "expected_max": max_cost,
                            },
                            suggestion="Verify quality tier and component specifications.",
                        ))
                    elif total_cost_per_unit > max_cost * 2:  # 2x above maximum
                        result.add_issue(ValidationIssue(
                            severity=IssueSeverity.WARNING,
                            code="COST_ABOVE_RANGE",
                            message=f"Unit cost ${total_cost_per_unit:.2f}/{unit} for '{component_name}' is significantly above typical range (${min_cost}-${max_cost}).",
                            stage="takeoff_cost",
                            component_id=component_id,
                            details={
                                "component": component_name,
                                "unit_cost": total_cost_per_unit,
                                "expected_min": min_cost,
                                "expected_max": max_cost,
                            },
                            suggestion="Verify if this is a premium/luxury item or if cost data is correct.",
                        ))
                break

        # Rule 3: Total cost matches quantity × unit cost
        if quantity > 0 and total_cost_per_unit > 0:
            expected_base = quantity * total_cost_per_unit
            # Allow 20% variance for adjustments
            if abs(final_cost - expected_base) > expected_base * 0.2:
                if final_cost > 0:
                    implied_multiplier = final_cost / expected_base
                    result.add_issue(ValidationIssue(
                        severity=IssueSeverity.INFO,
                        code="COST_MULTIPLIER_DETECTED",
                        message=f"Final cost ${final_cost:.2f} includes {implied_multiplier:.2f}x adjustment from base (${expected_base:.2f}).",
                        stage="takeoff_cost",
                        component_id=component_id,
                        details={
                            "component": component_name,
                            "quantity": quantity,
                            "unit_cost": total_cost_per_unit,
                            "base_cost": expected_base,
                            "final_cost": final_cost,
                            "implied_multiplier": implied_multiplier,
                        },
                    ))

        return result

    def validate_all(
        self,
        classifications: list[dict[str, Any]],
        takeoffs: list[dict[str, Any]],
        costs: list[dict[str, Any]],
    ) -> list[ValidationResult]:
        """
        Validate all components across stages.

        Args:
            classifications: List of classification results
            takeoffs: List of takeoff results
            costs: List of cost estimation results

        Returns:
            List of validation results, one per component
        """
        results = []

        # Build lookup maps
        takeoffs_by_name = {
            t.get("component_name", t.get("takeoff", {}).get("component_name", "")): t
            for t in takeoffs
        }
        costs_by_name = {
            c.get("component_name", c.get("estimate", {}).get("component_name", "")): c
            for c in costs
        }

        for clf in classifications:
            component_name = clf.get("component", clf.get("component_name", "unknown"))
            result = ValidationResult()

            # Find matching takeoff and cost
            takeoff = takeoffs_by_name.get(component_name, {})
            cost = costs_by_name.get(component_name, {})

            # Run validations
            if takeoff:
                clf_takeoff_result = self.validate_classification_takeoff(clf, takeoff)
                result.merge(clf_takeoff_result)

            if takeoff and cost:
                takeoff_cost_result = self.validate_takeoff_cost(takeoff, cost)
                result.merge(takeoff_cost_result)

            results.append(result)

        logger.info(
            f"Cross-validation complete: {len(results)} components, "
            f"{sum(r.warning_count for r in results)} warnings, "
            f"{sum(r.error_count for r in results)} errors"
        )

        return results
