"""Vision artifact schemas - the primary output of the vision pipeline."""

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .detection import BoundingBox


class Provenance(BaseModel):
    """Full provenance trace for a vision artifact."""

    image_url: str = Field(..., description="Original image URL")
    image_id: str = Field(..., description="Image identifier")
    detection_id: str = Field(..., description="Grounding DINO detection ID")
    detection_model: str = Field(
        default="grounding-dino-1.5-pro",
        description="Detection model used",
    )
    detection_confidence: float = Field(..., description="Detection confidence")
    segmentation_model: Optional[str] = Field(
        default="sam-2",
        description="Segmentation model used",
    )
    vlm_model: str = Field(
        default="gpt-4o",
        description="VLM model used for classification",
    )
    crop_bbox: BoundingBox = Field(..., description="Crop region used for VLM")
    crop_path: Optional[str] = Field(
        default=None,
        description="Path to saved crop image",
    )
    processing_timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When artifact was created",
    )
    pipeline_version: str = Field(
        default="0.1.0",
        description="Vision pipeline version",
    )


class VLMClassification(BaseModel):
    """VLM classification result for a detected region."""

    component_type: str = Field(..., description="Component type (e.g., 'cabinet')")
    material: Optional[str] = Field(
        default=None, description="Material type (e.g., 'wood', 'stainless steel')"
    )
    condition: Optional[str] = Field(
        default=None, description="Condition (e.g., 'new', 'good', 'worn')"
    )
    color: Optional[str] = Field(default=None, description="Primary color")
    brand: Optional[str] = Field(default=None, description="Brand if visible")
    model: Optional[str] = Field(default=None, description="Model if visible")
    dimensions_note: Optional[str] = Field(
        default=None, description="Estimated dimensions or size"
    )
    installation_type: Optional[str] = Field(
        default=None,
        description="Installation type (e.g., 'built-in', 'freestanding', 'mounted')",
    )
    additional_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional component-specific attributes",
    )
    raw_response: Optional[str] = Field(
        default=None,
        description="Raw VLM response for debugging",
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for Firestore."""
        return {
            "component_type": self.component_type,
            "material": self.material,
            "condition": self.condition,
            "color": self.color,
            "brand": self.brand,
            "model": self.model,
            "dimensions_note": self.dimensions_note,
            "installation_type": self.installation_type,
            "additional_attributes": self.additional_attributes,
        }


class VisionArtifact(BaseModel):
    """Primary output of the vision pipeline - analog to Chunk in PDF layer."""

    artifact_id: str = Field(
        default_factory=lambda: f"vart_{uuid4().hex[:12]}",
        description="Unique artifact identifier",
    )
    study_id: str = Field(..., description="Parent study identifier")
    image_id: str = Field(..., description="Source image identifier")
    detection_id: str = Field(..., description="Source detection identifier")

    # Classification
    classification: VLMClassification = Field(
        ..., description="VLM classification results"
    )

    # Confidence and validation
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Overall confidence score"
    )
    grounded: bool = Field(
        default=True,
        description="Whether VLM claim is grounded in detection",
    )
    consistency_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Self-consistency score from multi-pass voting",
    )
    needs_review: bool = Field(
        default=False,
        description="Flag for engineer review",
    )
    review_reason: Optional[str] = Field(
        default=None,
        description="Reason for flagging review",
    )

    # Provenance
    provenance: Provenance = Field(..., description="Full provenance trace")

    # Bounding box for spatial queries
    bbox: BoundingBox = Field(..., description="Detection bounding box")

    # Correction tracking
    corrected: bool = Field(
        default=False,
        description="Whether artifact has been corrected by engineer",
    )
    correction_id: Optional[str] = Field(
        default=None,
        description="ID of correction if applicable",
    )

    def to_citation(self) -> str:
        """Format as citation reference for agentic layer."""
        return f"{self.image_id}:{self.detection_id}:{self.artifact_id}"

    def to_dict(self) -> dict:
        """Convert to dictionary for Firestore."""
        return {
            "artifact_id": self.artifact_id,
            "study_id": self.study_id,
            "image_id": self.image_id,
            "detection_id": self.detection_id,
            "classification": self.classification.to_dict(),
            "confidence": self.confidence,
            "grounded": self.grounded,
            "consistency_score": self.consistency_score,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "bbox": {
                "x_min": self.bbox.x_min,
                "y_min": self.bbox.y_min,
                "x_max": self.bbox.x_max,
                "y_max": self.bbox.y_max,
            },
            "provenance": {
                "image_url": self.provenance.image_url,
                "image_id": self.provenance.image_id,
                "detection_id": self.provenance.detection_id,
                "detection_model": self.provenance.detection_model,
                "detection_confidence": self.provenance.detection_confidence,
                "segmentation_model": self.provenance.segmentation_model,
                "vlm_model": self.provenance.vlm_model,
                "crop_bbox": {
                    "x_min": self.provenance.crop_bbox.x_min,
                    "y_min": self.provenance.crop_bbox.y_min,
                    "x_max": self.provenance.crop_bbox.x_max,
                    "y_max": self.provenance.crop_bbox.y_max,
                },
                "crop_path": self.provenance.crop_path,
                "processing_timestamp": self.provenance.processing_timestamp.isoformat(),
                "pipeline_version": self.provenance.pipeline_version,
            },
            "corrected": self.corrected,
            "correction_id": self.correction_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VisionArtifact":
        """Create from Firestore dictionary."""
        provenance_data = data["provenance"]
        return cls(
            artifact_id=data["artifact_id"],
            study_id=data["study_id"],
            image_id=data["image_id"],
            detection_id=data["detection_id"],
            classification=VLMClassification(**data["classification"]),
            confidence=data["confidence"],
            grounded=data["grounded"],
            consistency_score=data.get("consistency_score"),
            needs_review=data["needs_review"],
            review_reason=data.get("review_reason"),
            bbox=BoundingBox(**data["bbox"]),
            provenance=Provenance(
                image_url=provenance_data["image_url"],
                image_id=provenance_data["image_id"],
                detection_id=provenance_data["detection_id"],
                detection_model=provenance_data["detection_model"],
                detection_confidence=provenance_data["detection_confidence"],
                segmentation_model=provenance_data.get("segmentation_model"),
                vlm_model=provenance_data["vlm_model"],
                crop_bbox=BoundingBox(**provenance_data["crop_bbox"]),
                crop_path=provenance_data.get("crop_path"),
                processing_timestamp=datetime.fromisoformat(
                    provenance_data["processing_timestamp"]
                ),
                pipeline_version=provenance_data["pipeline_version"],
            ),
            corrected=data.get("corrected", False),
            correction_id=data.get("correction_id"),
        )
