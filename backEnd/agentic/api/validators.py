"""
Input validation utilities.

Provides validation functions and annotated types for secure input handling.
"""

import re
from typing import Annotated

from pydantic import AfterValidator


# Valid stage names for workflow operations
VALID_STAGES = {
    "analyze_rooms",
    "analyze_objects",
    "analyze_takeoffs",
    "classify_assets",
    "verify_assets",
}

# Pattern for valid study IDs: alphanumeric, hyphens, underscores
STUDY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_study_id(value: str) -> str:
    """
    Validate study ID format.

    Allows alphanumeric characters, hyphens, and underscores only.
    Prevents injection attacks via study_id parameter.
    """
    if not value:
        raise ValueError("Study ID cannot be empty")

    if len(value) > 128:
        raise ValueError("Study ID too long (max 128 characters)")

    if not STUDY_ID_PATTERN.match(value):
        raise ValueError(
            "Study ID must contain only alphanumeric characters, hyphens, and underscores"
        )

    return value


def validate_stage_name(value: str) -> str:
    """
    Validate stage name is in the allowed set.
    """
    if value not in VALID_STAGES:
        raise ValueError(
            f"Invalid stage: {value}. Must be one of: {', '.join(sorted(VALID_STAGES))}"
        )
    return value


# Annotated types for use in Pydantic models
ValidStudyId = Annotated[str, AfterValidator(validate_study_id)]
ValidStageName = Annotated[str, AfterValidator(validate_stage_name)]
