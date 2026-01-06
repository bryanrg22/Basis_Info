"""
Extractor Agent for appraisal data extraction.

Role: "Extract appraisal data intelligently using available tools."

Strategy:
1. parse_mismo_xml (FREE) - if XML available
2. extract_with_azure_di (PAID) - for most fields
3. extract_with_vision (EXPENSIVE) - only for stubborn fields

The agent reasons about which tools to use based on:
- Availability of MISMO XML
- Which fields are still missing
- Confidence thresholds for critical fields
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from ...config.llm_providers import get_llm_for_stage
from .schemas import ExtractorInput, ExtractorOutput
from .tools import get_extractor_tools

logger = logging.getLogger(__name__)


# Critical fields that require >= 0.90 confidence
CRITICAL_FIELDS = {
    "property_address",
    "year_built",
    "gross_living_area",
    "appraised_value",
    "final_opinion_of_market_value",
    "contract_price",
    "effective_date",
}


class ExtractorAgent:
    """
    Agent that extracts appraisal data using intelligent tool selection.

    Uses a ReAct loop to:
    1. Assess what data sources are available
    2. Choose appropriate extraction tools
    3. Merge results from multiple tools
    4. Ensure critical fields meet confidence thresholds
    """

    def __init__(self, model_override: Optional[str] = None):
        self.stage_name = "appraisal_extraction"
        self._model_override = model_override
        self._llm: Optional[BaseChatModel] = None

    @property
    def llm(self) -> BaseChatModel:
        """Get the LLM for this agent (lazy loaded)."""
        if self._llm is None:
            self._llm = get_llm_for_stage(self.stage_name)
        return self._llm

    def get_system_prompt(self) -> str:
        """Return the system prompt for extraction."""
        return """You are an expert appraisal data extractor. Your role is to extract
structured data from URAR (Uniform Residential Appraisal Report) documents.

## Available Tools

You have access to these extraction tools (in order of preference):

1. **parse_mismo_xml** (FREE, 100% confidence)
   - Use FIRST if MISMO XML is available
   - Provides authoritative, structured data

2. **extract_with_azure_di** (PAID $0.10-0.50, 70-95% confidence)
   - Azure Document Intelligence for PDF extraction
   - Excellent for structured forms and tables

3. **extract_with_vision** (EXPENSIVE $0.10-0.20, 60-90% confidence)
   - GPT-4o Vision for visual analysis
   - Use for handwritten/faded content or fields Azure DI missed

## Critical Fields (require >= 90% confidence)

- subject.property_address
- improvements.year_built
- improvements.gross_living_area
- reconciliation.final_opinion_of_market_value
- listing_and_contract.contract_price
- reconciliation.effective_date

## Strategy

1. Check if MISMO XML is provided - if yes, use parse_mismo_xml first
2. Use extract_with_azure_di for the main extraction
3. For any critical fields with low confidence, use extract_with_vision

## Output Format

After extraction, provide a summary in this JSON format:
```json
{
    "sections": {
        "subject": {"property_address": "123 Main St", ...},
        "improvements": {"year_built": 1995, ...},
        ...
    },
    "field_confidences": {
        "subject": {"property_address": 0.95, ...},
        ...
    },
    "field_sources": {
        "subject": {"property_address": "azure_di", ...},
        ...
    },
    "tools_invoked": ["extract_with_azure_di", "extract_with_vision"]
}
```

Be thorough - extract all available fields, not just critical ones.
Prefer higher-confidence values when merging results from multiple tools.
"""

    def get_tools(self) -> List[BaseTool]:
        """Return extraction tools."""
        return get_extractor_tools()

    async def run(
        self,
        input_data: ExtractorInput,
    ) -> ExtractorOutput:
        """
        Execute extraction using intelligent tool selection.

        Args:
            input_data: ExtractorInput with pdf_path and optional XML

        Returns:
            ExtractorOutput with extracted data and metadata
        """
        tools = self.get_tools()
        system_prompt = self.get_system_prompt()

        # Bind tools to the LLM
        llm_with_tools = self.llm.bind_tools(tools)

        # Build the task prompt
        task_prompt = self._format_input(input_data)

        # Initialize messages
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]

        # Simple ReAct loop (max 5 iterations)
        all_messages = list(messages)
        max_iterations = 5
        tools_invoked = []

        for _ in range(max_iterations):
            # Call the LLM
            response = await llm_with_tools.ainvoke(all_messages)
            all_messages.append(response)

            # Check if there are tool calls
            if not response.tool_calls:
                break

            # Execute each tool call
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tools_invoked.append(tool_name)

                # Find and execute the tool
                tool_result = None
                for tool in tools:
                    if tool.name == tool_name:
                        try:
                            # Use ainvoke for proper async execution
                            tool_result = await tool.ainvoke(tool_args)
                        except Exception as e:
                            tool_result = {"success": False, "error": str(e)}
                        break

                if tool_result is None:
                    tool_result = {"success": False, "error": f"Tool '{tool_name}' not found"}

                # Add tool result as a message
                tool_message = ToolMessage(
                    content=json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result),
                    tool_call_id=tool_call["id"],
                )
                all_messages.append(tool_message)

        # Extract the final response
        raw_response = ""
        for msg in reversed(all_messages):
            if isinstance(msg, AIMessage) and msg.content:
                raw_response = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        # Parse the output
        return self._parse_output(raw_response, tools_invoked)

    def _format_input(self, input_data: ExtractorInput) -> str:
        """Format the extraction task prompt."""
        parts = [
            "## Extraction Task",
            "",
            f"**PDF Path:** {input_data.pdf_path}",
        ]

        if input_data.mismo_xml:
            parts.append("**MISMO XML:** Available (use parse_mismo_xml first!)")
        else:
            parts.append("**MISMO XML:** Not available")

        if input_data.tables_path:
            parts.append(f"**Tables Path:** {input_data.tables_path}")

        parts.extend([
            "",
            "## Instructions",
            "",
            "1. Use the appropriate extraction tools to get appraisal data",
            "2. Ensure all critical fields have >= 90% confidence",
            "3. Merge results from multiple tools, preferring higher confidence",
            "4. Output the final extracted data in the specified JSON format",
        ])

        return "\n".join(parts)

    def _parse_output(
        self,
        response: str,
        tools_invoked: List[str],
    ) -> ExtractorOutput:
        """Parse agent response into structured output."""
        sections = {}
        field_confidences = {}
        field_sources = {}

        try:
            # Try to find JSON in the response
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r'\{[\s\S]*\}', response)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = None

            if json_str:
                data = json.loads(json_str)
                sections = data.get("sections", {})
                field_confidences = data.get("field_confidences", {})
                field_sources = data.get("field_sources", {})

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
        except Exception as e:
            logger.error(f"Error parsing extractor output: {e}")

        return ExtractorOutput(
            sections=sections,
            field_confidences=field_confidences,
            field_sources=field_sources,
            tools_invoked=list(set(tools_invoked)),
        )


async def run_extractor_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node function for ExtractorAgent.

    Args:
        state: AppraisalExtractionState dict

    Returns:
        Updated state with extraction results
    """
    from datetime import datetime
    import time

    start_time = time.time()
    agent = ExtractorAgent()

    input_data = ExtractorInput(
        pdf_path=state["pdf_path"],
        mismo_xml=state.get("mismo_xml"),
        tables_path=state.get("tables_path"),
    )

    output = await agent.run(input_data)
    duration_ms = int((time.time() - start_time) * 1000)

    # Update audit trail
    audit = state.get("audit_trail", {})
    if "agent_calls" not in audit:
        audit["agent_calls"] = []
    audit["agent_calls"].append({
        "agent_name": "ExtractorAgent",
        "timestamp": datetime.utcnow().isoformat(),
        "input_summary": f"PDF: {state['pdf_path']}, MISMO: {'yes' if state.get('mismo_xml') else 'no'}",
        "output_summary": f"Extracted {len(output.sections)} sections",
        "tools_used": output.tools_invoked,
        "duration_ms": duration_ms,
    })

    # Record field history for each extracted field
    if "field_history" not in audit:
        audit["field_history"] = []
    for section, fields in output.sections.items():
        for field_name, value in fields.items():
            field_key = f"{section}.{field_name}"
            confidence = output.field_confidences.get(section, {}).get(field_name, 0.7)
            source = output.field_sources.get(section, {}).get(field_name, "unknown")
            audit["field_history"].append({
                "field_key": field_key,
                "timestamp": datetime.utcnow().isoformat(),
                "action": "extracted",
                "value": value,
                "source": source,
                "confidence": confidence,
                "notes": None,
            })

    # Update state
    return {
        **state,
        "sections": output.sections,
        "field_confidences": output.field_confidences,
        "field_sources": output.field_sources,
        "tools_invoked": output.tools_invoked,
        "audit_trail": audit,
    }
