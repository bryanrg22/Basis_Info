"""MCP tool wrappers for vision layer integration with agentic workflow."""

from .vision_tools import (
    ALL_VISION_TOOLS,
    classify_region_tool,
    classify_scene_tool,
    detect_objects_tool,
    get_artifacts_for_image_tool,
    get_review_queue_tool,
    get_vision_artifact_tool,
    get_vision_review_stats_tool,
    process_image_tool,
    search_vision_artifacts_tool,
    segment_detections_tool,
    submit_correction_tool,
)

__all__ = [
    "ALL_VISION_TOOLS",
    "detect_objects_tool",
    "segment_detections_tool",
    "classify_region_tool",
    "process_image_tool",
    "classify_scene_tool",
    "get_vision_artifact_tool",
    "search_vision_artifacts_tool",
    "get_artifacts_for_image_tool",
    "get_review_queue_tool",
    "get_vision_review_stats_tool",
    "submit_correction_tool",
]
