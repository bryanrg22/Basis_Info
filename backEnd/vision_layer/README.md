# Vision Evidence Layer

Detection-first vision processing for Basis cost segregation studies.

## Overview

This package provides a vision processing pipeline that reduces VLM hallucinations through a detection-first architecture:

1. **Grounding DINO** - Open-vocabulary object detection
2. **SAM 2** - High-quality segmentation masks
3. **Region Cropping** - Focus VLM attention on specific objects
4. **VLM Classification** - Material, condition, and attribute extraction
5. **Grounding Verification** - Cross-reference VLM claims against detections

## Architecture

```
Property Images
       ↓
┌──────────────────────────────────────────────┐
│          Grounding DINO 1.5 Pro              │
│     (Open-vocabulary object detection)       │
└──────────────────────────────────────────────┘
       ↓ Bounding boxes + confidence
┌──────────────────────────────────────────────┐
│               SAM 2                          │
│     (Precise segmentation masks)             │
└──────────────────────────────────────────────┘
       ↓ Masks + refined boxes
┌──────────────────────────────────────────────┐
│           Region Cropper                     │
│   (Extract + pad regions for VLM)            │
└──────────────────────────────────────────────┘
       ↓ Cropped images
┌──────────────────────────────────────────────┐
│         VLM (GPT-4o Vision)                  │
│  (Classify materials, attributes per crop)   │
└──────────────────────────────────────────────┘
       ↓ Classifications
┌──────────────────────────────────────────────┐
│        Grounding Verifier                    │
│  (Cross-reference claims vs detections)      │
└──────────────────────────────────────────────┘
       ↓ Validated artifacts
       VisionArtifact (with full provenance)
```

## Quick Start

### 1. Install dependencies

```bash
cd backEnd/vision_layer
pip install -e .
```

### 2. Configure environment

```bash
# Required for detection models (Grounding DINO, SAM 2)
export REPLICATE_API_TOKEN=r8_...

# VLM Provider - Choose ONE:

# Option A: OpenAI (default)
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o  # Optional, defaults to gpt-4o

# Option B: Azure OpenAI (overrides OpenAI when all three are set)
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
export AZURE_OPENAI_API_VERSION=2024-02-15-preview  # Optional

# Optional
export GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

The VLM client automatically detects which provider to use based on environment variables. Azure OpenAI takes priority when fully configured.

### 3. Basic usage

```python
import asyncio
from src.pipeline import VisionPipeline, PipelineConfig

async def process_property_images():
    # Configure pipeline
    config = PipelineConfig(
        detection_prompts=["cabinet", "appliance", "flooring", "lighting"],
        crop_padding=0.2,
        enable_segmentation=True,
    )

    pipeline = VisionPipeline(config=config)

    # Process single image
    artifacts = await pipeline.process_image(
        image_url="https://example.com/kitchen.jpg",
        image_id="img_001",
        study_id="STUDY_001",
        room_context="kitchen",
    )

    for artifact in artifacts:
        print(f"Found: {artifact.classification.component_type}")
        print(f"  Material: {artifact.classification.material}")
        print(f"  Confidence: {artifact.confidence:.2f}")
        print(f"  Grounded: {artifact.grounded}")
        print(f"  Citation: {artifact.to_citation()}")

asyncio.run(process_property_images())
```

## Package Structure

```
vision_layer/
├── src/
│   ├── config/
│   │   ├── settings.py       # Pydantic settings (env vars)
│   │   └── vlm_providers.py  # OpenAI/Azure abstraction
│   ├── schemas/
│   │   ├── detection.py      # BoundingBox, Detection, Mask
│   │   ├── scene.py          # RoomType, SceneClassification
│   │   └── artifact.py       # VisionArtifact, Provenance
│   ├── api_clients/
│   │   ├── base.py           # Rate-limited base client
│   │   ├── grounding_dino.py # Grounding DINO via Replicate
│   │   ├── sam2.py           # SAM 2 via Replicate
│   │   └── vlm.py            # OpenAI/Azure Vision client
│   ├── pipeline/
│   │   ├── cropper.py        # Region extraction with padding
│   │   └── ingest.py         # Main orchestrator
│   └── validation/
│       ├── grounding_verifier.py  # Cross-reference VLM vs detections
│       └── consistency.py         # Self-consistency voting
├── mcp_tools/                # MCP tool wrappers (Phase 3)
└── tests/
```

## Key Concepts

### Detection-First Architecture

Instead of sending full images to the VLM (which causes hallucinations), we:

1. **Detect first** - Use Grounding DINO to find objects with bounding boxes
2. **Segment** - Use SAM 2 to get precise masks
3. **Crop** - Extract regions with padding around each detection
4. **Classify** - Run VLM on focused crops, not full images
5. **Verify** - Cross-reference VLM claims against detections

### VisionArtifact

The primary output, analogous to `Chunk` in the PDF evidence layer:

```python
class VisionArtifact:
    artifact_id: str           # Unique identifier
    study_id: str              # Parent study
    image_id: str              # Source image
    detection_id: str          # Source detection
    classification: VLMClassification  # Component details
    confidence: float          # Overall confidence
    grounded: bool             # VLM claim matches detection
    needs_review: bool         # Flag for engineer review
    provenance: Provenance     # Full trace
    bbox: BoundingBox          # Spatial location
```

### Grounding Verification

VLM claims are verified against Grounding DINO detections:

```python
from src.validation import GroundingVerifier

verifier = GroundingVerifier(iou_threshold=0.5)

# Verify single artifact
claim = verifier.verify_artifact(artifact, detections)
print(f"Grounded: {claim.grounded}")
print(f"Detection: {claim.detection_label}")
print(f"IoU: {claim.iou_score}")

# Compute overall grounding score
score = verifier.compute_grounding_score(artifacts, detections)
print(f"Grounding score: {score:.0%}")
```

### Self-Consistency Checking

Run multiple VLM passes for uncertain classifications:

```python
from src.validation import ConsistencyChecker

checker = ConsistencyChecker(num_passes=3)
result = await checker.check_consistency(crop_image)

print(f"Component: {result.component_type}")
print(f"Agreement: {result.agreement_score:.0%}")
print(f"All votes: {result.all_types}")
```

## Configuration

### Pipeline Config

```python
@dataclass
class PipelineConfig:
    # Detection
    detection_prompts: list[str]    # Object classes to detect
    detection_threshold: float       # Confidence threshold (0.3)

    # Cropping
    crop_padding: float              # Padding ratio (0.2 = 20%)
    save_crops: bool                 # Save crop images
    crops_dir: Path                  # Directory for crops

    # Segmentation
    enable_segmentation: bool        # Use SAM 2

    # VLM
    vlm_model: str                   # "gpt-4o"
    enable_consistency_check: bool   # Multi-pass voting
    consistency_passes: int          # Number of passes (3)

    # Review
    low_confidence_threshold: float  # Flag below this (0.5)
    require_grounding: bool          # Require detection match
```

## Integration with Agentic Workflow

The Vision Evidence Layer exposes MCP tools for the agentic layer:

```python
# In agentic/agents/room_agent.py
from vision_layer.mcp_tools import detect_objects_tool

# Agent calls vision tool
detections = await detect_objects_tool(
    image_url="https://example.com/room.jpg",
    prompts=["cabinet", "appliance", "flooring"],
)

# Use detections for IRS classification
for detection in detections:
    asset_class = await classify_asset(detection.label)
```

## Development

### Run tests

```bash
pytest tests/
```

### Type checking

```bash
mypy src/
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `REPLICATE_API_TOKEN` | Replicate API token (Grounding DINO, SAM 2) | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes* |
| `OPENAI_MODEL` | OpenAI model name (default: gpt-4o) | No |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | For Azure |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | For Azure |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Azure deployment name | For Azure |
| `AZURE_OPENAI_API_VERSION` | Azure API version (default: 2024-02-15-preview) | No |
| `GOOGLE_APPLICATION_CREDENTIALS` | Firebase credentials | For storage |

*Either OpenAI or Azure OpenAI credentials are required for VLM classification. Azure takes priority when fully configured.

## License

Proprietary - Basis Team
