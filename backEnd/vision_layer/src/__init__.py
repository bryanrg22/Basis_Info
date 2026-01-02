"""Vision Evidence Layer for Basis cost segregation.

Detection-first architecture using Grounding DINO + SAM 2 + VLM
to reduce hallucinations and provide evidence-backed visual analysis.
"""

__version__ = "0.1.0"

# Core pipeline
from .pipeline.ingest import PipelineConfig, VisionPipeline

# Schemas
from .schemas.artifact import Provenance, VisionArtifact, VLMClassification
from .schemas.detection import BoundingBox, Detection, Mask
from .schemas.scene import RoomType, SceneClassification

# API clients
from .api_clients.grounding_dino import GroundingDINOClient
from .api_clients.sam2 import SAM2Client
from .api_clients.vlm import VLMClient

# Evidence storage
from .evidence import (
    Correction,
    ReviewItem,
    ReviewThresholds,
    get_artifact_by_id,
    get_review_queue,
    get_vision_artifacts,
    save_correction,
    save_vision_artifacts,
    search_artifacts,
)

# Config
from .config import Settings, get_settings, get_vlm_client

__all__ = [
    # Version
    "__version__",
    # Pipeline
    "VisionPipeline",
    "PipelineConfig",
    # Schemas
    "VisionArtifact",
    "VLMClassification",
    "Provenance",
    "Detection",
    "BoundingBox",
    "Mask",
    "SceneClassification",
    "RoomType",
    # Clients
    "GroundingDINOClient",
    "SAM2Client",
    "VLMClient",
    # Evidence
    "save_vision_artifacts",
    "get_vision_artifacts",
    "get_artifact_by_id",
    "search_artifacts",
    "Correction",
    "save_correction",
    "ReviewThresholds",
    "ReviewItem",
    "get_review_queue",
    # Config
    "Settings",
    "get_settings",
    "get_vlm_client",
]
