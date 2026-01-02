"""Tests for validation components."""

import pytest

from src.schemas.detection import BoundingBox, Detection
from src.schemas.artifact import (
    Provenance,
    VisionArtifact,
    VLMClassification,
)
from src.validation.grounding_verifier import GroundingVerifier, GroundedClaim


class TestGroundingVerifier:
    """Tests for GroundingVerifier."""

    @pytest.fixture
    def verifier(self):
        """Create a grounding verifier."""
        return GroundingVerifier(iou_threshold=0.5)

    @pytest.fixture
    def sample_detections(self):
        """Create sample detections."""
        return [
            Detection(
                detection_id="det_001",
                image_id="img_001",
                label="cabinet",
                confidence=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
            ),
            Detection(
                detection_id="det_002",
                image_id="img_001",
                label="appliance",
                confidence=0.85,
                bbox=BoundingBox(x_min=0.5, y_min=0.5, x_max=0.9, y_max=0.9),
            ),
        ]

    def test_verify_grounded_by_id(self, verifier, sample_detections):
        """Test verification when artifact matches detection by ID."""
        artifact = VisionArtifact(
            study_id="study_001",
            image_id="img_001",
            detection_id="det_001",
            classification=VLMClassification(component_type="cabinet"),
            confidence=0.9,
            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
            provenance=Provenance(
                image_url="http://example.com/img.jpg",
                image_id="img_001",
                detection_id="det_001",
                detection_confidence=0.9,
                crop_bbox=BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5),
            ),
        )

        result = verifier.verify_artifact(artifact, sample_detections)
        assert result.grounded
        assert result.detection_id == "det_001"
        assert result.iou_score == 1.0

    def test_verify_label_mismatch(self, verifier, sample_detections):
        """Test verification with mismatched labels."""
        artifact = VisionArtifact(
            study_id="study_001",
            image_id="img_001",
            detection_id="det_001",  # Detection label is "cabinet"
            classification=VLMClassification(component_type="refrigerator"),  # Mismatch
            confidence=0.9,
            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
            provenance=Provenance(
                image_url="http://example.com/img.jpg",
                image_id="img_001",
                detection_id="det_001",
                detection_confidence=0.9,
                crop_bbox=BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5),
            ),
        )

        result = verifier.verify_artifact(artifact, sample_detections)
        # With require_label_match=True (default), should not be grounded
        assert not result.grounded

    def test_verify_synonym_match(self, verifier, sample_detections):
        """Test that synonyms are recognized."""
        artifact = VisionArtifact(
            study_id="study_001",
            image_id="img_001",
            detection_id="det_001",  # Detection label is "cabinet"
            classification=VLMClassification(component_type="cupboard"),  # Synonym
            confidence=0.9,
            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
            provenance=Provenance(
                image_url="http://example.com/img.jpg",
                image_id="img_001",
                detection_id="det_001",
                detection_confidence=0.9,
                crop_bbox=BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5),
            ),
        )

        result = verifier.verify_artifact(artifact, sample_detections)
        assert result.grounded  # cupboard is synonym for cabinet

    def test_compute_grounding_score(self, verifier, sample_detections):
        """Test overall grounding score computation."""
        artifacts = [
            VisionArtifact(
                study_id="study_001",
                image_id="img_001",
                detection_id="det_001",
                classification=VLMClassification(component_type="cabinet"),
                confidence=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
                provenance=Provenance(
                    image_url="http://example.com/img.jpg",
                    image_id="img_001",
                    detection_id="det_001",
                    detection_confidence=0.9,
                    crop_bbox=BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5),
                ),
            ),
            VisionArtifact(
                study_id="study_001",
                image_id="img_001",
                detection_id="det_002",
                classification=VLMClassification(component_type="refrigerator"),  # Matches appliance synonym
                confidence=0.85,
                bbox=BoundingBox(x_min=0.5, y_min=0.5, x_max=0.9, y_max=0.9),
                provenance=Provenance(
                    image_url="http://example.com/img.jpg",
                    image_id="img_001",
                    detection_id="det_002",
                    detection_confidence=0.85,
                    crop_bbox=BoundingBox(x_min=0.4, y_min=0.4, x_max=1.0, y_max=1.0),
                ),
            ),
        ]

        score = verifier.compute_grounding_score(artifacts, sample_detections)
        assert score == 1.0  # Both should be grounded

    def test_labels_match(self, verifier):
        """Test label matching logic."""
        # Direct match
        assert verifier._labels_match("cabinet", "Cabinet")

        # Containment
        assert verifier._labels_match("wood cabinet", "cabinet")

        # Synonym
        assert verifier._labels_match("refrigerator", "appliance")
        assert verifier._labels_match("hardwood", "flooring")

        # No match
        assert not verifier._labels_match("cabinet", "toilet")
