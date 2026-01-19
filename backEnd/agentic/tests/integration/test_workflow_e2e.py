"""
End-to-end workflow integration tests.

Phase 6: Tests the complete workflow lifecycle including:
- Happy path from start to completion
- Pause/resume at engineer review points
- Correction handling
- Failure recovery
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from ...graph.state import WorkflowState
from ...evidence.aggregator import EvidenceAggregator
from ...evidence.models import EvidenceEntry, EvidencePack


class TestEvidenceAggregation:
    """Tests for evidence aggregation functionality."""

    def test_create_aggregator(self, sample_study_id):
        """Test creating an evidence aggregator."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)
        assert aggregator.study_id == sample_study_id
        assert aggregator.get_entry_count() == 0

    def test_add_citations(self, sample_study_id):
        """Test adding citations to the aggregator."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)

        citations = [
            {
                "chunk_id": "chunk_001",
                "doc_id": "irs_pub_946",
                "page": 15,
                "excerpt": "Office furniture and fixtures...",
            },
            {
                "chunk_id": "chunk_002",
                "doc_id": "irs_pub_946",
                "page": 16,
                "excerpt": "HVAC equipment...",
            },
        ]

        count = aggregator.add_citations(
            citations=citations,
            stage="classification",
            component_id="comp_1",
            component_name="HVAC Unit",
        )

        assert count == 2
        assert aggregator.get_entry_count() == 2

    def test_deduplication(self, sample_study_id):
        """Test that duplicate citations are not added."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)

        citation = {
            "chunk_id": "chunk_001",
            "doc_id": "irs_pub_946",
            "page": 15,
            "excerpt": "Office furniture...",
        }

        # Add same citation twice for same component
        count1 = aggregator.add_citations(
            citations=[citation],
            stage="classification",
            component_id="comp_1",
            component_name="Desk",
        )
        count2 = aggregator.add_citations(
            citations=[citation],
            stage="classification",
            component_id="comp_1",
            component_name="Desk",
        )

        assert count1 == 1
        assert count2 == 0  # Duplicate should be skipped
        assert aggregator.get_entry_count() == 1

    def test_get_organized_pack(self, sample_study_id):
        """Test getting an organized evidence pack."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)

        # Add citations for multiple stages and components
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c1", "doc_id": "doc1", "page": 1, "excerpt": "text1"},
            ],
            stage="room",
            component_id="room_1",
            component_name="Office",
        )
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c2", "doc_id": "doc1", "page": 2, "excerpt": "text2"},
            ],
            stage="classification",
            component_id="obj_1",
            component_name="HVAC",
        )
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c3", "doc_id": "doc2", "page": 1, "excerpt": "text3"},
            ],
            stage="cost",
            component_id="obj_1",
            component_name="HVAC",
        )

        pack = aggregator.get_organized_pack()

        assert pack.study_id == sample_study_id
        assert pack.total_citations == 3
        assert len(pack.entries) == 3
        assert "room" in pack.by_stage
        assert "classification" in pack.by_stage
        assert "cost" in pack.by_stage
        assert "room_1" in pack.by_component
        assert "obj_1" in pack.by_component
        assert "doc1" in pack.by_document
        assert "doc2" in pack.by_document

    def test_evidence_summary(self, sample_study_id):
        """Test evidence summary generation."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)

        # Register components
        aggregator.register_component("comp_1")
        aggregator.register_component("comp_2")
        aggregator.register_component("comp_3")

        # Only add citations for 2 of 3 components
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c1", "doc_id": "doc1", "page": 1, "excerpt": "text"},
                {"chunk_id": "c2", "doc_id": "doc2", "page": 1, "excerpt": "text"},
            ],
            stage="classification",
            component_id="comp_1",
            component_name="Component 1",
        )
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c3", "doc_id": "doc1", "page": 2, "excerpt": "text"},
            ],
            stage="classification",
            component_id="comp_2",
            component_name="Component 2",
        )

        pack = aggregator.get_organized_pack()
        summary = pack.summary

        assert summary.total_citations == 3
        assert summary.unique_documents == 2
        assert "classification" in summary.stages_covered
        assert "comp_3" in summary.components_without_evidence
        assert summary.avg_citations_per_component == 1.5


class TestEvidenceEntry:
    """Tests for EvidenceEntry model."""

    def test_to_reference(self):
        """Test reference string generation."""
        entry = EvidenceEntry(
            doc_id="irs_pub_946",
            page=42,
            excerpt="HVAC equipment...",
            stage="classification",
            component_id="hvac_1",
            component_name="HVAC Unit",
        )

        ref = entry.to_reference()
        assert "irs_pub_946" in ref
        assert "42" in ref

    def test_to_reference_with_table(self):
        """Test reference string with table ID."""
        entry = EvidenceEntry(
            doc_id="rsmeans_2024",
            page=100,
            table_id="table_5.2",
            excerpt="Cost data...",
            stage="cost",
            component_id="hvac_1",
            component_name="HVAC Unit",
        )

        ref = entry.to_reference()
        assert "table_5.2" in ref

    def test_signature_with_chunk_id(self):
        """Test signature generation with chunk ID."""
        entry1 = EvidenceEntry(
            chunk_id="chunk_123",
            doc_id="doc1",
            page=1,
            excerpt="text",
            stage="test",
            component_id="comp_1",
            component_name="Test",
        )
        entry2 = EvidenceEntry(
            chunk_id="chunk_123",
            doc_id="doc1",
            page=1,
            excerpt="different text",
            stage="test",
            component_id="comp_1",
            component_name="Test",
        )

        # Same chunk_id + component_id should give same signature
        assert entry1.signature() == entry2.signature()

    def test_signature_without_chunk_id(self):
        """Test signature generation without chunk ID."""
        entry1 = EvidenceEntry(
            doc_id="doc1",
            page=1,
            excerpt="same text",
            stage="test",
            component_id="comp_1",
            component_name="Test",
        )
        entry2 = EvidenceEntry(
            doc_id="doc1",
            page=1,
            excerpt="different text",
            stage="test",
            component_id="comp_1",
            component_name="Test",
        )

        # Different excerpts should give different signatures
        assert entry1.signature() != entry2.signature()


class TestEvidencePack:
    """Tests for EvidencePack model."""

    def test_firestore_serialization(self, sample_study_id):
        """Test Firestore serialization and deserialization."""
        # Create a pack with entries
        aggregator = EvidenceAggregator(study_id=sample_study_id)
        aggregator.add_citations(
            citations=[
                {"chunk_id": "c1", "doc_id": "doc1", "page": 1, "excerpt": "text"},
            ],
            stage="test",
            component_id="comp_1",
            component_name="Test",
        )
        original_pack = aggregator.get_organized_pack()

        # Serialize and deserialize
        data = original_pack.to_firestore_dict()
        restored_pack = EvidencePack.from_firestore_dict(data)

        assert restored_pack.study_id == original_pack.study_id
        assert restored_pack.total_citations == original_pack.total_citations
        assert len(restored_pack.entries) == len(original_pack.entries)

    def test_get_entries_by_stage(self, sample_study_id):
        """Test filtering entries by stage."""
        aggregator = EvidenceAggregator(study_id=sample_study_id)
        aggregator.add_citations(
            citations=[{"chunk_id": "c1", "doc_id": "d1", "page": 1, "excerpt": "t"}],
            stage="room",
            component_id="r1",
            component_name="Room",
        )
        aggregator.add_citations(
            citations=[{"chunk_id": "c2", "doc_id": "d1", "page": 2, "excerpt": "t"}],
            stage="classification",
            component_id="o1",
            component_name="Object",
        )

        pack = aggregator.get_organized_pack()
        room_entries = pack.get_entries_by_stage("room")
        class_entries = pack.get_entries_by_stage("classification")

        assert len(room_entries) == 1
        assert len(class_entries) == 1
        assert room_entries[0].stage == "room"


class TestCheckpointHistory:
    """Tests for checkpoint history tracking."""

    def test_checkpoint_history_entry_creation(self):
        """Test creating a checkpoint history entry."""
        from ...firestore.checkpoint_history import CheckpointHistoryEntry

        entry = CheckpointHistoryEntry(
            thread_id="study_123",
            from_stage="uploading_documents",
            to_stage="resource_extraction",
            trigger="stage_complete",
            summary={"rooms_added": 5},
            channel_values={"current_stage": "resource_extraction"},
        )

        assert entry.thread_id == "study_123"
        assert entry.from_stage == "uploading_documents"
        assert entry.to_stage == "resource_extraction"
        assert entry.trigger == "stage_complete"
        assert entry.summary["rooms_added"] == 5

    def test_checkpoint_diff(self):
        """Test computing diff between checkpoints."""
        from ...firestore.checkpoint_history import (
            CheckpointHistoryEntry,
            CheckpointDiff,
        )

        entry_a = CheckpointHistoryEntry(
            thread_id="study_123",
            to_stage="room_analysis",
            channel_values={
                "current_stage": "room_analysis",
                "rooms": [{"id": "r1"}],
                "objects": [],
            },
        )
        entry_b = CheckpointHistoryEntry(
            thread_id="study_123",
            to_stage="classification",
            channel_values={
                "current_stage": "classification",
                "rooms": [{"id": "r1"}, {"id": "r2"}],
                "objects": [{"id": "o1"}],
            },
        )

        diff = CheckpointDiff.compute(entry_a, entry_b)

        assert "current_stage" in diff.fields_changed
        assert "rooms" in diff.fields_changed
        assert "objects" in diff.fields_changed
        assert diff.summary["total_changes"] >= 3


class TestAlerts:
    """Tests for alert functionality."""

    def test_alert_payload_creation(self):
        """Test creating an alert payload."""
        from ...observability.alerts import AlertPayload

        payload = AlertPayload(
            study_id="study_123",
            workflow_stage="classification",
            error_type="LLM_TIMEOUT",
            error_message="LLM call timed out after 60s",
            severity="error",
            context={"component_id": "hvac_1"},
        )

        assert payload.study_id == "study_123"
        assert payload.severity == "error"
        assert "component_id" in payload.context

    def test_alert_throttle_key(self):
        """Test alert throttle key generation."""
        from ...observability.alerts import AlertPayload

        payload1 = AlertPayload(
            study_id="study_123",
            workflow_stage="classification",
            error_type="LLM_TIMEOUT",
            error_message="Error 1",
        )
        payload2 = AlertPayload(
            study_id="study_123",
            workflow_stage="classification",
            error_type="LLM_TIMEOUT",
            error_message="Error 2",  # Different message
        )
        payload3 = AlertPayload(
            study_id="study_456",  # Different study
            workflow_stage="classification",
            error_type="LLM_TIMEOUT",
            error_message="Error 1",
        )

        # Same study/stage/type should have same throttle key
        assert payload1.throttle_key() == payload2.throttle_key()
        # Different study should have different key
        assert payload1.throttle_key() != payload3.throttle_key()

    def test_slack_message_format(self):
        """Test Slack message formatting."""
        from ...observability.alerts import AlertPayload

        payload = AlertPayload(
            study_id="study_123",
            workflow_stage="classification",
            error_type="PARSING_ERROR",
            error_message="Failed to parse JSON response",
            severity="warning",
        )

        slack_msg = payload.to_slack_message()

        assert "attachments" in slack_msg
        assert len(slack_msg["attachments"]) > 0
        assert "blocks" in slack_msg["attachments"][0]


class TestCostTracking:
    """Tests for cost tracking functionality."""

    def test_model_costs_lookup(self):
        """Test model cost lookup."""
        from ...observability.cost_tracker import get_model_costs

        gpt4o_costs = get_model_costs("gpt-4o")
        assert "input" in gpt4o_costs
        assert "output" in gpt4o_costs
        assert gpt4o_costs["input"] > 0

    def test_unknown_model_fallback(self):
        """Test fallback for unknown models."""
        from ...observability.cost_tracker import get_model_costs

        costs = get_model_costs("unknown-model-xyz")
        # Should return default costs
        assert "input" in costs
        assert "output" in costs

    def test_llm_usage_record(self):
        """Test LLM usage record creation."""
        from ...observability.cost_tracker import LLMUsageRecord

        record = LLMUsageRecord(
            study_id="study_123",
            agent="room_agent",
            model="gpt-4o-mini",
            input_tokens=1000,
            output_tokens=500,
            estimated_cost_usd=0.0006,
            latency_ms=1200,
        )

        assert record.study_id == "study_123"
        assert record.input_tokens == 1000
        assert record.estimated_cost_usd == 0.0006


class TestDecisionLogging:
    """Tests for decision logging functionality."""

    def test_decision_point_creation(self):
        """Test creating a decision point."""
        from ...observability.decision_log import DecisionPoint

        decision = DecisionPoint(
            study_id="study_123",
            agent="asset_classifier",
            decision_type="classification",
            decision={"macrs_class": "5-year"},
            reasoning="HVAC is 5-year property",
            evidence_used=["chunk_001", "chunk_002"],
            confidence=0.85,
            component_id="hvac_1",
            component_name="HVAC Unit",
        )

        assert decision.study_id == "study_123"
        assert decision.confidence == 0.85
        assert len(decision.evidence_used) == 2

    def test_firestore_serialization(self):
        """Test decision point Firestore serialization."""
        from ...observability.decision_log import DecisionPoint

        decision = DecisionPoint(
            study_id="study_123",
            agent="test_agent",
            decision_type="test",
            decision={"test": "value"},
        )

        data = decision.to_firestore_dict()
        restored = DecisionPoint.from_firestore_dict(data)

        assert restored.study_id == decision.study_id
        assert restored.agent == decision.agent
