"""
Pydantic schemas for Firestore documents.

These schemas match the frontend TypeScript types for consistency.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    """Workflow status matching frontend/src/types/workflow.types.ts."""

    UPLOADING_DOCUMENTS = "uploading_documents"
    ANALYZING_ROOMS = "analyzing_rooms"
    REVIEWING_ROOMS = "reviewing_rooms"
    ANALYZING_TAKEOFFS = "analyzing_takeoffs"
    REVIEWING_TAKEOFFS = "reviewing_takeoffs"
    VIEWING_REPORT = "viewing_report"
    REVIEWING_ASSETS = "reviewing_assets"
    VERIFYING_ASSETS = "verifying_assets"
    COMPLETED = "completed"


class UploadedFile(BaseModel):
    """Uploaded file metadata."""

    id: str
    name: str
    type: str  # MIME type
    size: int
    url: str
    uploaded_at: Optional[datetime] = None


class Room(BaseModel):
    """Room classification result."""

    id: str
    name: str
    type: str  # Room type classification
    photo_ids: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    needs_review: bool = False


class AssetClassification(BaseModel):
    """Asset classification from IRS guidance."""

    bucket: str = Field(..., description="MACRS bucket: 5-year, 7-year, etc.")
    life_years: int = Field(..., ge=1, le=40)
    section: str = Field(..., pattern="^(1245|1250)$")
    asset_class: Optional[str] = Field(None, description="IRS asset class (e.g., 57.0)")
    macrs_system: str = Field(default="GDS")
    irs_note: str = Field(..., description="Explanation with IRS citations")


class Asset(BaseModel):
    """Asset/object with classification."""

    id: str
    name: str
    component: str  # Component type
    space_type: Optional[str] = None
    indoor_outdoor: Optional[str] = None
    attachment_type: Optional[str] = None
    function_type: Optional[str] = None

    # Classification results
    asset_classification: Optional[AssetClassification] = None
    category: Optional[str] = None  # Legacy field: "5-year", "15-year", etc.
    estimated_value: Optional[float] = None
    verified: bool = False

    # Evidence
    citations: list[dict[str, Any]] = Field(default_factory=list)
    confidence: Optional[float] = None
    needs_review: bool = False
    review_reason: Optional[str] = None


class Takeoff(BaseModel):
    """Takeoff/quantity measurement."""

    id: str
    component: str
    quantity: float
    unit: str
    room_id: Optional[str] = None
    photo_id: Optional[str] = None


class Study(BaseModel):
    """Full study document matching Firestore schema."""

    id: str
    user_id: str = Field(alias="userId")
    property_name: str = Field(alias="propertyName")
    status: str = Field(default="in_progress")
    workflow_status: WorkflowStatus = Field(
        default=WorkflowStatus.UPLOADING_DOCUMENTS,
        alias="workflowStatus",
    )

    # Collections
    uploaded_files: list[UploadedFile] = Field(
        default_factory=list, alias="uploadedFiles"
    )
    rooms: list[Room] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    takeoffs: list[Takeoff] = Field(default_factory=list)

    # Evidence tracking
    evidence_pack: list[dict[str, Any]] = Field(
        default_factory=list, alias="evidencePack"
    )

    # Classification summary
    classification_summary: Optional[dict[str, Any]] = Field(
        default=None, alias="classificationSummary"
    )

    # Timestamps
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")
    completed_at: Optional[datetime] = Field(default=None, alias="completedAt")

    class Config:
        populate_by_name = True
