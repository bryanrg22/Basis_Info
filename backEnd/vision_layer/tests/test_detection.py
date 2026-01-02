"""Tests for detection schemas."""

import pytest

from src.schemas.detection import BoundingBox, Detection, Mask, RLEMask


class TestBoundingBox:
    """Tests for BoundingBox schema."""

    def test_valid_bbox(self):
        """Test valid bounding box creation."""
        bbox = BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.8)
        assert bbox.x_min == 0.1
        assert bbox.width == pytest.approx(0.4)
        assert bbox.height == pytest.approx(0.6)

    def test_bbox_area(self):
        """Test bounding box area calculation."""
        bbox = BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5)
        assert bbox.area == pytest.approx(0.25)

    def test_bbox_center(self):
        """Test bounding box center calculation."""
        bbox = BoundingBox(x_min=0.2, y_min=0.2, x_max=0.8, y_max=0.8)
        center = bbox.center
        assert center[0] == pytest.approx(0.5)
        assert center[1] == pytest.approx(0.5)

    def test_bbox_to_pixels(self):
        """Test conversion to pixel coordinates."""
        bbox = BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.8)
        pixels = bbox.to_pixels(1000, 800)
        assert pixels == (100, 160, 500, 640)

    def test_bbox_with_padding(self):
        """Test bounding box padding."""
        bbox = BoundingBox(x_min=0.3, y_min=0.3, x_max=0.7, y_max=0.7)
        padded = bbox.with_padding(0.25)

        # Width is 0.4, padding is 0.25 * 0.4 = 0.1 on each side
        assert padded.x_min == pytest.approx(0.2)
        assert padded.x_max == pytest.approx(0.8)

    def test_bbox_padding_clamped(self):
        """Test that padding is clamped to [0, 1]."""
        bbox = BoundingBox(x_min=0.0, y_min=0.0, x_max=0.2, y_max=0.2)
        padded = bbox.with_padding(0.5)

        assert padded.x_min >= 0.0
        assert padded.y_min >= 0.0

    def test_bbox_iou(self):
        """Test IoU calculation."""
        bbox1 = BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5)
        bbox2 = BoundingBox(x_min=0.25, y_min=0.25, x_max=0.75, y_max=0.75)

        iou = bbox1.iou(bbox2)
        # Intersection: 0.25 * 0.25 = 0.0625
        # Union: 0.25 + 0.25 - 0.0625 = 0.4375
        assert iou == pytest.approx(0.0625 / 0.4375)

    def test_bbox_iou_no_overlap(self):
        """Test IoU with no overlap."""
        bbox1 = BoundingBox(x_min=0.0, y_min=0.0, x_max=0.3, y_max=0.3)
        bbox2 = BoundingBox(x_min=0.5, y_min=0.5, x_max=0.8, y_max=0.8)

        assert bbox1.iou(bbox2) == 0.0

    def test_invalid_bbox_x(self):
        """Test that x_max must be greater than x_min."""
        with pytest.raises(ValueError):
            BoundingBox(x_min=0.5, y_min=0.0, x_max=0.3, y_max=0.5)

    def test_invalid_bbox_y(self):
        """Test that y_max must be greater than y_min."""
        with pytest.raises(ValueError):
            BoundingBox(x_min=0.0, y_min=0.5, x_max=0.5, y_max=0.3)


class TestDetection:
    """Tests for Detection schema."""

    def test_detection_creation(self):
        """Test detection creation with auto-generated ID."""
        detection = Detection(
            image_id="test_image",
            label="cabinet",
            confidence=0.85,
            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
        )
        assert detection.detection_id.startswith("det_")
        assert detection.label == "cabinet"
        assert detection.confidence == 0.85

    def test_detection_to_citation(self):
        """Test citation formatting."""
        detection = Detection(
            detection_id="det_abc123",
            image_id="img_001",
            label="appliance",
            confidence=0.9,
            bbox=BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5),
        )
        assert detection.to_citation() == "img_001:det_abc123"

    def test_detection_to_dict(self):
        """Test conversion to dictionary."""
        detection = Detection(
            detection_id="det_abc123",
            image_id="img_001",
            label="cabinet",
            confidence=0.85,
            bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.8),
            prompt="cabinet",
        )
        data = detection.to_dict()

        assert data["detection_id"] == "det_abc123"
        assert data["label"] == "cabinet"
        assert data["bbox"]["x_min"] == 0.1
        assert data["prompt"] == "cabinet"


class TestMask:
    """Tests for Mask schema."""

    def test_rle_mask(self):
        """Test RLE mask creation."""
        mask = Mask(
            rle=RLEMask(counts=[10, 20, 30], size=(100, 100))
        )
        assert mask.rle.counts == [10, 20, 30]
        assert mask.rle.size == (100, 100)

    def test_polygon_mask(self):
        """Test polygon mask creation."""
        mask = Mask(
            polygon=[[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]
        )
        assert len(mask.polygon) == 4

    def test_invalid_polygon(self):
        """Test that polygon must have at least 3 vertices."""
        with pytest.raises(ValueError):
            Mask(polygon=[[0.1, 0.1], [0.5, 0.5]])
