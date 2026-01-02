"""Detection and segmentation schemas."""

from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class BoundingBox(BaseModel):
    """Bounding box in normalized coordinates [0, 1]."""

    x_min: float = Field(..., ge=0.0, le=1.0, description="Left edge")
    y_min: float = Field(..., ge=0.0, le=1.0, description="Top edge")
    x_max: float = Field(..., ge=0.0, le=1.0, description="Right edge")
    y_max: float = Field(..., ge=0.0, le=1.0, description="Bottom edge")

    @field_validator("x_max")
    @classmethod
    def x_max_greater_than_x_min(cls, v: float, info) -> float:
        if "x_min" in info.data and v <= info.data["x_min"]:
            raise ValueError("x_max must be greater than x_min")
        return v

    @field_validator("y_max")
    @classmethod
    def y_max_greater_than_y_min(cls, v: float, info) -> float:
        if "y_min" in info.data and v <= info.data["y_min"]:
            raise ValueError("y_max must be greater than y_min")
        return v

    @property
    def width(self) -> float:
        """Width of bounding box in normalized coordinates."""
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        """Height of bounding box in normalized coordinates."""
        return self.y_max - self.y_min

    @property
    def area(self) -> float:
        """Area of bounding box in normalized coordinates."""
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        """Center point (x, y) of bounding box."""
        return (
            (self.x_min + self.x_max) / 2,
            (self.y_min + self.y_max) / 2,
        )

    def to_pixels(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        """Convert to pixel coordinates (x_min, y_min, x_max, y_max)."""
        return (
            int(self.x_min * image_width),
            int(self.y_min * image_height),
            int(self.x_max * image_width),
            int(self.y_max * image_height),
        )

    def with_padding(self, padding: float = 0.2) -> "BoundingBox":
        """Return new bbox with padding added (clamped to [0, 1])."""
        pad_w = self.width * padding
        pad_h = self.height * padding
        return BoundingBox(
            x_min=max(0.0, self.x_min - pad_w),
            y_min=max(0.0, self.y_min - pad_h),
            x_max=min(1.0, self.x_max + pad_w),
            y_max=min(1.0, self.y_max + pad_h),
        )

    def iou(self, other: "BoundingBox") -> float:
        """Compute Intersection over Union with another bbox."""
        x_min = max(self.x_min, other.x_min)
        y_min = max(self.y_min, other.y_min)
        x_max = min(self.x_max, other.x_max)
        y_max = min(self.y_max, other.y_max)

        if x_max <= x_min or y_max <= y_min:
            return 0.0

        intersection = (x_max - x_min) * (y_max - y_min)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0

    @classmethod
    def from_pixels(
        cls,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        image_width: int,
        image_height: int,
    ) -> "BoundingBox":
        """Create BoundingBox from pixel coordinates by normalizing."""
        return cls(
            x_min=x_min / image_width,
            y_min=y_min / image_height,
            x_max=x_max / image_width,
            y_max=y_max / image_height,
        )

    @classmethod
    def from_pixels_auto(
        cls,
        coords: list[float],
        image_width: int = 640,
        image_height: int = 480,
    ) -> "BoundingBox":
        """Create BoundingBox, auto-detecting if normalization is needed."""
        x_min, y_min, x_max, y_max = coords[0], coords[1], coords[2], coords[3]

        # If any coord > 1, assume pixel coordinates
        if any(c > 1.0 for c in [x_min, y_min, x_max, y_max]):
            return cls.from_pixels(x_min, y_min, x_max, y_max, image_width, image_height)

        return cls(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)


class RLEMask(BaseModel):
    """Run-length encoded segmentation mask."""

    counts: list[int] = Field(..., description="RLE counts")
    size: tuple[int, int] = Field(..., description="(height, width) of mask")


class Mask(BaseModel):
    """Segmentation mask with optional RLE encoding."""

    rle: Optional[RLEMask] = Field(default=None, description="RLE encoded mask")
    polygon: Optional[list[list[float]]] = Field(
        default=None, description="Polygon vertices [[x1,y1], [x2,y2], ...]"
    )

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, v: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if v is not None:
            if len(v) < 3:
                raise ValueError("Polygon must have at least 3 vertices")
            for point in v:
                if len(point) != 2:
                    raise ValueError("Each polygon point must have [x, y] coordinates")
        return v


class Detection(BaseModel):
    """Object detection result from Grounding DINO."""

    detection_id: str = Field(
        default_factory=lambda: f"det_{uuid4().hex[:12]}",
        description="Unique detection identifier",
    )
    image_id: str = Field(..., description="Source image identifier")
    label: str = Field(..., description="Detected object label/class")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Detection confidence score"
    )
    bbox: BoundingBox = Field(..., description="Bounding box coordinates")
    mask: Optional[Mask] = Field(
        default=None, description="Segmentation mask from SAM 2"
    )
    prompt: Optional[str] = Field(
        default=None, description="Text prompt used for detection"
    )

    def to_citation(self) -> str:
        """Format as citation reference."""
        return f"{self.image_id}:{self.detection_id}"

    def to_dict(self) -> dict:
        """Convert to dictionary for Firestore."""
        return {
            "detection_id": self.detection_id,
            "image_id": self.image_id,
            "label": self.label,
            "confidence": self.confidence,
            "bbox": {
                "x_min": self.bbox.x_min,
                "y_min": self.bbox.y_min,
                "x_max": self.bbox.x_max,
                "y_max": self.bbox.y_max,
            },
            "has_mask": self.mask is not None,
            "prompt": self.prompt,
        }
