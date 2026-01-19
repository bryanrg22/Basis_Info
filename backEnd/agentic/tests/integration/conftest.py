"""
Shared fixtures for integration tests.

Phase 6: Provides mock Firestore, mock LLM, and sample data fixtures
for integration testing.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ...graph.state import WorkflowState


# =============================================================================
# Sample Data Fixtures
# =============================================================================


@pytest.fixture
def sample_study_id() -> str:
    """Generate a unique study ID for testing."""
    return f"test_study_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def sample_study(sample_study_id: str) -> dict[str, Any]:
    """Sample study document for testing."""
    return {
        "id": sample_study_id,
        "userId": "test_user_123",
        "propertyName": "Test Office Building",
        "workflowStatus": "uploading_documents",
        "rooms": [],
        "objects": [],
        "takeoffs": [],
        "costEstimates": [],
        "appraisalResources": {},
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }


@pytest.fixture
def sample_rooms() -> list[dict[str, Any]]:
    """Sample rooms for testing."""
    return [
        {
            "id": "room_1",
            "room_type": "office",
            "name": "Executive Office",
            "area_sf": 250,
            "context": {
                "room_type": "office",
                "room_area_sf": 250,
            },
            "citations": [
                {
                    "chunk_id": "chunk_001",
                    "doc_id": "irs_pub_946",
                    "page": 15,
                    "excerpt": "Office furniture and fixtures...",
                }
            ],
        },
        {
            "id": "room_2",
            "room_type": "kitchen",
            "name": "Break Room",
            "area_sf": 150,
            "context": {
                "room_type": "kitchen",
                "room_area_sf": 150,
            },
            "citations": [],
        },
    ]


@pytest.fixture
def sample_objects() -> list[dict[str, Any]]:
    """Sample objects for testing."""
    return [
        {
            "id": "obj_1",
            "room_id": "room_1",
            "label": "hvac_unit",
            "original_label": "HVAC Unit",
            "confidence": 0.92,
            "citations": [
                {
                    "chunk_id": "chunk_010",
                    "doc_id": "irs_pub_946",
                    "page": 42,
                    "excerpt": "HVAC equipment is typically 5-year property...",
                }
            ],
        },
        {
            "id": "obj_2",
            "room_id": "room_2",
            "label": "refrigerator",
            "original_label": "Commercial Refrigerator",
            "confidence": 0.88,
            "citations": [],
        },
    ]


@pytest.fixture
def sample_classifications() -> list[dict[str, Any]]:
    """Sample asset classifications for testing."""
    return [
        {
            "component": "HVAC Unit",
            "component_id": "obj_1",
            "classification": {
                "macrs_class": "5-year",
                "recovery_period": 5,
                "asset_class": "57.0",
                "description": "Industrial equipment",
            },
            "confidence": 0.85,
            "citations": [
                {
                    "chunk_id": "chunk_010",
                    "doc_id": "irs_pub_946",
                    "page": 42,
                    "excerpt": "HVAC equipment...",
                }
            ],
            "needs_review": False,
        },
        {
            "component": "Commercial Refrigerator",
            "component_id": "obj_2",
            "classification": {
                "macrs_class": "5-year",
                "recovery_period": 5,
                "asset_class": "57.0",
                "description": "Kitchen equipment",
            },
            "confidence": 0.45,
            "citations": [],
            "needs_review": True,
            "review_reason": "Low confidence classification",
        },
    ]


@pytest.fixture
def sample_workflow_state(
    sample_study_id: str,
    sample_rooms: list[dict],
    sample_objects: list[dict],
) -> WorkflowState:
    """Sample workflow state for testing."""
    return WorkflowState(
        study_id=sample_study_id,
        user_id="test_user_123",
        property_name="Test Office Building",
        current_stage="uploading_documents",
        rooms=sample_rooms,
        objects=sample_objects,
        takeoffs=[],
        asset_classifications=[],
        cost_estimates=[],
        evidence_pack=[],
        reference_doc_ids=["irs_pub_946", "rsmeans_2024"],
        study_doc_ids=[],
        needs_review=False,
        errors=[],
    )


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_firestore_client():
    """Mock Firestore client for testing without real database."""
    with patch("agentic.firestore.client.FirestoreClient") as MockClient:
        mock_instance = MagicMock()

        # Mock study operations
        mock_instance.get_study = MagicMock(return_value=None)
        mock_instance.update_study = MagicMock()
        mock_instance.update_workflow_status = MagicMock()

        # Mock database
        mock_db = MagicMock()
        mock_instance.db = mock_db

        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_firestore_with_study(mock_firestore_client, sample_study):
    """Mock Firestore client with a pre-loaded study."""
    mock_firestore_client.get_study.return_value = sample_study
    return mock_firestore_client


@pytest.fixture
def mock_llm():
    """Mock LLM for testing without API calls."""
    with patch("agentic.config.llm_providers.get_llm_for_stage") as mock_get_llm:
        mock_llm_instance = MagicMock()

        # Mock the invoke method to return a structured response
        mock_response = MagicMock()
        mock_response.content = '{"classification": {"macrs_class": "5-year"}}'
        mock_response.tool_calls = []
        mock_response.response_metadata = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": "gpt-4o-mini",
        }

        mock_llm_instance.bind_tools = MagicMock(return_value=mock_llm_instance)
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)

        mock_get_llm.return_value = mock_llm_instance
        yield mock_llm_instance


@pytest.fixture
def mock_mcp_tools():
    """Mock MCP evidence tools for testing."""
    with patch("agentic.mcp_server.server.get_all_evidence_tools") as mock_get_tools:
        # Return empty list - tests shouldn't need real search
        mock_get_tools.return_value = []
        yield mock_get_tools


@pytest.fixture
def mock_cost_tracker():
    """Mock cost tracker for testing."""
    with patch("agentic.observability.cost_tracker.get_cost_tracker") as mock_get:
        mock_tracker = MagicMock()
        mock_tracker.record_usage = AsyncMock()
        mock_tracker.get_study_summary = AsyncMock(
            return_value=MagicMock(
                study_id="test",
                total_cost_usd=0.05,
                total_input_tokens=1000,
                total_output_tokens=500,
                total_calls=5,
                calls_by_agent={},
                cost_by_agent={},
                cost_by_stage={},
                avg_latency_ms=500,
            )
        )
        mock_get.return_value = mock_tracker
        yield mock_tracker


@pytest.fixture
def mock_decision_logger():
    """Mock decision logger for testing."""
    with patch("agentic.observability.decision_log.get_decision_logger") as mock_get:
        mock_logger = MagicMock()
        mock_logger.log_decision = AsyncMock()
        mock_logger.get_all_decisions = AsyncMock(return_value=[])
        mock_logger.get_decision_summary = AsyncMock(
            return_value=MagicMock(
                study_id="test",
                total_decisions=0,
                decisions_by_agent={},
                decisions_by_type={},
                avg_confidence=0.0,
                low_confidence_count=0,
            )
        )
        mock_get.return_value = mock_logger
        yield mock_logger


@pytest.fixture
def mock_alert_manager():
    """Mock alert manager for testing."""
    with patch("agentic.observability.alerts.get_alert_manager") as mock_get:
        mock_manager = MagicMock()
        mock_manager.alert = AsyncMock(return_value=True)
        mock_manager.alert_error = AsyncMock(return_value=True)
        mock_get.return_value = mock_manager
        yield mock_manager


# =============================================================================
# Test Utilities
# =============================================================================


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run_async(coro):
    """Helper to run async functions in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)
