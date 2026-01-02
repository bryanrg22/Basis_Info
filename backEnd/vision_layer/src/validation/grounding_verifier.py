"""Grounding verification - cross-reference VLM claims against detections."""

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..schemas.artifact import VLMClassification, VisionArtifact
from ..schemas.detection import BoundingBox, Detection

logger = logging.getLogger(__name__)


class GroundedClaim(BaseModel):
    """A VLM claim with grounding information."""

    claim: str = Field(..., description="The VLM claim (e.g., component type)")
    grounded: bool = Field(..., description="Whether claim is grounded in detection")
    detection_id: Optional[str] = Field(
        default=None, description="Matched detection ID if grounded"
    )
    detection_label: Optional[str] = Field(
        default=None, description="Detection label that grounds the claim"
    )
    iou_score: Optional[float] = Field(
        default=None, description="IoU overlap score if spatially matched"
    )
    confidence: float = Field(
        default=0.0, description="Grounding confidence score"
    )


class GroundingVerifier:
    """Verifies VLM claims are grounded in Grounding DINO detections.

    Grounding reduces hallucination by ensuring the VLM only makes claims
    about objects that were actually detected in the image.
    """

    def __init__(
        self,
        iou_threshold: float = 0.5,
        require_label_match: bool = True,
    ):
        """Initialize grounding verifier.

        Args:
            iou_threshold: Minimum IoU for spatial grounding.
            require_label_match: Whether to require label similarity.
        """
        self.iou_threshold = iou_threshold
        self.require_label_match = require_label_match

        # Component type synonyms for flexible matching
        self.synonyms = {
            "cabinet": ["cabinet", "cupboard", "storage", "drawer", "shelf"],
            "appliance": [
                "appliance",
                "refrigerator",
                "fridge",
                "oven",
                "stove",
                "range",
                "dishwasher",
                "microwave",
                "washer",
                "dryer",
            ],
            "lighting": [
                "light",
                "lighting",
                "lamp",
                "fixture",
                "chandelier",
                "sconce",
                "recessed",
            ],
            "flooring": [
                "floor",
                "flooring",
                "tile",
                "carpet",
                "hardwood",
                "vinyl",
                "laminate",
            ],
            "countertop": ["counter", "countertop", "surface", "worktop", "granite", "quartz"],
            "sink": ["sink", "basin", "faucet"],
            "toilet": ["toilet", "commode"],
            "tub": ["tub", "bathtub", "shower"],
            "window": ["window", "glass", "pane"],
            "door": ["door", "entry", "entrance"],
            "hvac": ["hvac", "ac", "air conditioner", "vent", "duct", "furnace"],
            "electrical": ["electrical", "outlet", "switch", "panel", "breaker"],
            "plumbing": ["plumbing", "pipe", "valve", "water heater"],
        }

    def verify_artifact(
        self,
        artifact: VisionArtifact,
        detections: list[Detection],
    ) -> GroundedClaim:
        """Verify a single artifact is grounded in detections.

        Args:
            artifact: VisionArtifact to verify.
            detections: List of detections from same image.

        Returns:
            GroundedClaim with verification results.
        """
        vlm_type = artifact.classification.component_type.lower()

        # Find matching detection by ID first
        for detection in detections:
            if detection.detection_id == artifact.detection_id:
                # Check label match
                label_match = self._labels_match(vlm_type, detection.label)
                return GroundedClaim(
                    claim=artifact.classification.component_type,
                    grounded=label_match or not self.require_label_match,
                    detection_id=detection.detection_id,
                    detection_label=detection.label,
                    iou_score=1.0,  # Same detection, perfect overlap
                    confidence=detection.confidence if label_match else detection.confidence * 0.5,
                )

        # Fallback: find best spatial match
        best_match = self._find_best_spatial_match(artifact.bbox, detections)
        if best_match:
            detection, iou = best_match
            label_match = self._labels_match(vlm_type, detection.label)
            return GroundedClaim(
                claim=artifact.classification.component_type,
                grounded=iou >= self.iou_threshold and (label_match or not self.require_label_match),
                detection_id=detection.detection_id,
                detection_label=detection.label,
                iou_score=iou,
                confidence=detection.confidence * iou if label_match else detection.confidence * iou * 0.5,
            )

        # No match found
        return GroundedClaim(
            claim=artifact.classification.component_type,
            grounded=False,
            confidence=0.0,
        )

    def verify_classification(
        self,
        classification: VLMClassification,
        bbox: BoundingBox,
        detections: list[Detection],
    ) -> GroundedClaim:
        """Verify a VLM classification is grounded.

        Args:
            classification: VLMClassification to verify.
            bbox: Bounding box of the classified region.
            detections: Available detections.

        Returns:
            GroundedClaim with verification results.
        """
        vlm_type = classification.component_type.lower()
        best_match = self._find_best_spatial_match(bbox, detections)

        if best_match:
            detection, iou = best_match
            label_match = self._labels_match(vlm_type, detection.label)
            grounded = iou >= self.iou_threshold and (label_match or not self.require_label_match)

            return GroundedClaim(
                claim=classification.component_type,
                grounded=grounded,
                detection_id=detection.detection_id,
                detection_label=detection.label,
                iou_score=iou,
                confidence=detection.confidence if grounded else 0.0,
            )

        return GroundedClaim(
            claim=classification.component_type,
            grounded=False,
            confidence=0.0,
        )

    def _labels_match(self, vlm_label: str, detection_label: str) -> bool:
        """Check if VLM and detection labels are semantically similar."""
        vlm_lower = vlm_label.lower()
        det_lower = detection_label.lower()

        # Direct containment
        if vlm_lower in det_lower or det_lower in vlm_lower:
            return True

        # Check synonyms
        for category, words in self.synonyms.items():
            vlm_in_category = any(w in vlm_lower for w in words)
            det_in_category = any(w in det_lower for w in words)
            if vlm_in_category and det_in_category:
                return True

        return False

    def _find_best_spatial_match(
        self,
        bbox: BoundingBox,
        detections: list[Detection],
    ) -> Optional[tuple[Detection, float]]:
        """Find detection with highest IoU overlap."""
        best_detection = None
        best_iou = 0.0

        for detection in detections:
            iou = bbox.iou(detection.bbox)
            if iou > best_iou:
                best_iou = iou
                best_detection = detection

        if best_detection and best_iou > 0:
            return (best_detection, best_iou)
        return None

    def compute_grounding_score(
        self,
        artifacts: list[VisionArtifact],
        detections: list[Detection],
    ) -> float:
        """Compute overall grounding score for a set of artifacts.

        Returns:
            Fraction of artifacts that are grounded (0.0 to 1.0).
        """
        if not artifacts:
            return 1.0

        grounded_count = 0
        for artifact in artifacts:
            claim = self.verify_artifact(artifact, detections)
            if claim.grounded:
                grounded_count += 1

        return grounded_count / len(artifacts)
