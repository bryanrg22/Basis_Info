"""Scene classification schemas."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RoomType(str, Enum):
    """Room type classifications for cost segregation."""

    # Residential
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    KITCHEN = "kitchen"
    LIVING_ROOM = "living_room"
    DINING_ROOM = "dining_room"
    LAUNDRY = "laundry"
    GARAGE = "garage"
    BASEMENT = "basement"
    ATTIC = "attic"

    # Commercial
    OFFICE = "office"
    CONFERENCE_ROOM = "conference_room"
    LOBBY = "lobby"
    RECEPTION = "reception"
    BREAK_ROOM = "break_room"
    RESTROOM = "restroom"
    STORAGE = "storage"
    SERVER_ROOM = "server_room"
    WAREHOUSE = "warehouse"

    # Common areas
    HALLWAY = "hallway"
    STAIRWELL = "stairwell"
    ELEVATOR = "elevator"
    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"

    # Outdoor
    PARKING = "parking"
    EXTERIOR = "exterior"
    ROOF = "roof"
    LANDSCAPING = "landscaping"

    # Other
    UNKNOWN = "unknown"


class SceneClassification(BaseModel):
    """Scene-level classification for an image."""

    image_id: str = Field(..., description="Source image identifier")
    room_type: RoomType = Field(..., description="Primary room type classification")
    room_type_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in room type"
    )
    secondary_type: Optional[RoomType] = Field(
        default=None, description="Secondary room type if applicable"
    )
    indoor_outdoor: str = Field(
        default="indoor",
        pattern="^(indoor|outdoor|mixed)$",
        description="Indoor/outdoor classification",
    )
    property_type: Optional[str] = Field(
        default=None,
        description="Property type (residential, commercial, industrial)",
    )
    floor_level: Optional[str] = Field(
        default=None,
        description="Floor level if determinable (ground, upper, basement)",
    )
    lighting_condition: Optional[str] = Field(
        default=None,
        description="Lighting condition (natural, artificial, mixed)",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional observations about the scene",
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for Firestore."""
        return {
            "image_id": self.image_id,
            "room_type": self.room_type.value,
            "room_type_confidence": self.room_type_confidence,
            "secondary_type": self.secondary_type.value if self.secondary_type else None,
            "indoor_outdoor": self.indoor_outdoor,
            "property_type": self.property_type,
            "floor_level": self.floor_level,
            "lighting_condition": self.lighting_condition,
            "notes": self.notes,
        }
