"""Tests for agent framework."""

import pytest

from agentic.agents.base_agent import Citation, StageContext, AgentOutput
from agentic.agents.asset_agent import (
    ComponentInput,
    AssetClassification,
    AssetClassificationAgent,
)


class TestCitation:
    """Tests for Citation model."""

    def test_citation_to_reference(self):
        """Test citation reference formatting."""
        citation = Citation(
            chunk_id="IRS_PUB946_chunk_15",
            doc_id="IRS_PUB946",
            page=45,
            excerpt="Section 1245 property includes...",
        )
        assert citation.to_reference() == "IRS_PUB946, p.45"

    def test_citation_with_table(self):
        """Test citation with table reference."""
        citation = Citation(
            chunk_id="IRS_PUB946_chunk_15",
            table_id="IRS_PUB946_p45_t0",
            doc_id="IRS_PUB946",
            page=45,
            excerpt="Table A-1",
        )
        assert "IRS_PUB946_p45_t0" in citation.to_reference()


class TestStageContext:
    """Tests for StageContext model."""

    def test_stage_context_defaults(self):
        """Test StageContext default values."""
        ctx = StageContext(study_id="STUDY_001")
        assert ctx.corpus == "reference"
        assert ctx.reference_doc_ids == []
        assert ctx.study_doc_ids == []

    def test_stage_context_with_docs(self):
        """Test StageContext with document IDs."""
        ctx = StageContext(
            study_id="STUDY_001",
            property_name="123 Main St",
            reference_doc_ids=["IRS_PUB946", "IRS_ATG"],
            study_doc_ids=["APPRAISAL_001"],
        )
        assert len(ctx.reference_doc_ids) == 2
        assert len(ctx.study_doc_ids) == 1


class TestComponentInput:
    """Tests for ComponentInput model."""

    def test_component_input_minimal(self):
        """Test ComponentInput with minimal fields."""
        inp = ComponentInput(component="carpet")
        assert inp.component == "carpet"
        assert inp.space_type is None

    def test_component_input_full(self):
        """Test ComponentInput with all fields."""
        inp = ComponentInput(
            component="kitchen_appliance",
            space_type="unit_kitchen",
            indoor_outdoor="indoor",
            attachment_type="removable",
            function_type="utility",
        )
        assert inp.function_type == "utility"


class TestAssetClassification:
    """Tests for AssetClassification model."""

    def test_asset_classification_valid(self):
        """Test valid asset classification."""
        classification = AssetClassification(
            bucket="5-year",
            life_years=5,
            section="1245",
            asset_class="57.0",
            irs_note="Per Rev. Proc. 87-56",
        )
        assert classification.macrs_system == "GDS"

    def test_asset_classification_section_validation(self):
        """Test section validation."""
        with pytest.raises(ValueError):
            AssetClassification(
                bucket="5-year",
                life_years=5,
                section="1234",  # Invalid
                irs_note="Test",
            )


class TestAssetClassificationAgent:
    """Tests for AssetClassificationAgent."""

    def test_agent_initialization(self):
        """Test agent initialization."""
        agent = AssetClassificationAgent()
        assert agent.stage_name == "asset_classification"

    def test_agent_system_prompt(self):
        """Test system prompt contains key instructions."""
        agent = AssetClassificationAgent()
        prompt = agent.get_system_prompt()

        assert "MACRS" in prompt
        assert "1245" in prompt
        assert "1250" in prompt
        assert "evidence" in prompt.lower()

    def test_agent_output_schema(self):
        """Test output schema is correct."""
        agent = AssetClassificationAgent()
        assert agent.get_output_schema() == AssetClassification

    def test_parse_output_json(self):
        """Test parsing JSON output."""
        agent = AssetClassificationAgent()

        response = '''Based on my search, I found this classification:
        {
            "bucket": "5-year",
            "life_years": 5,
            "section": "1245",
            "asset_class": "57.0",
            "irs_note": "Per IRS guidance on page 45"
        }'''

        result = agent.parse_output(response, [])
        assert result.bucket == "5-year"
        assert result.section == "1245"

    def test_parse_output_code_block(self):
        """Test parsing JSON in code block."""
        agent = AssetClassificationAgent()

        response = '''Here is the classification:
        ```json
        {
            "bucket": "15-year",
            "life_years": 15,
            "section": "1250",
            "irs_note": "Land improvement"
        }
        ```'''

        result = agent.parse_output(response, [])
        assert result.bucket == "15-year"
        assert result.section == "1250"
