"""
Corrector Agent for fixing appraisal extraction errors.

Role: "Fix the flagged errors using alternative extraction methods."

Strategy:
- Use a DIFFERENT method than the one that originally failed
- Apply verifier's reasoning as context for re-extraction
- Explain what was wrong and how it was fixed

This agent is called when the verifier finds suspicious fields.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from ...config.llm_providers import get_llm_for_stage
from .schemas import CorrectorInput, CorrectorOutput, FieldCorrection, SuspiciousField
from .tools import get_corrector_tools

logger = logging.getLogger(__name__)


class CorrectorAgent:
    """
    Agent that corrects extraction errors using alternative methods.

    For each suspicious field:
    1. Understands why it was flagged
    2. Uses a different extraction method
    3. Validates the correction makes sense
    4. Documents what was wrong and how it was fixed
    """

    def __init__(self, model_override: Optional[str] = None):
        self.stage_name = "appraisal_correction"
        self._model_override = model_override
        self._llm: Optional[BaseChatModel] = None

    @property
    def llm(self) -> BaseChatModel:
        """Get the LLM for this agent (lazy loaded)."""
        if self._llm is None:
            self._llm = get_llm_for_stage(self.stage_name)
        return self._llm

    def get_system_prompt(self) -> str:
        """Return the system prompt for correction."""
        return """You are an expert at correcting appraisal data extraction errors.
You receive fields that were flagged as suspicious and must fix them.

## Available Tools

1. **extract_with_azure_di** (PAID $0.10-0.50)
   - Full document re-extraction with Azure Document Intelligence
   - Use if verifier suggests azure_di or if original source was vision

2. **extract_with_vision** (EXPENSIVE $0.10-0.20)
   - Visual extraction of specific fields
   - Use if verifier suggests vision or if original source was azure_di

3. **vision_recheck_field** (PAID $0.05-0.10)
   - Targeted single-field re-extraction
   - Best for OCR errors on specific fields

## Correction Strategy

1. **Use a DIFFERENT method than what originally failed**
   - If original source was "azure_di", use vision_recheck_field or extract_with_vision
   - If original source was "vision", try extract_with_azure_di

2. **Use verifier's reasoning as context**
   - If flagged as "ocr_error", focus on visual verification
   - If flagged as "implausible", verify the field is being read correctly
   - If flagged as "inconsistent", check both conflicting fields

3. **Validate the correction**
   - The new value should make more sense than the old one
   - If the new value is ALSO suspicious, note it in the correction

## Output Format

After corrections, provide your results in this JSON format:
```json
{
    "corrections_made": [
        {
            "field_key": "improvements.year_built",
            "old_value": "I995",
            "new_value": 1995,
            "correction_source": "vision_recheck_field",
            "correction_reasoning": "OCR misread '1' as 'I'. Vision verified the year is 1995."
        }
    ],
    "updated_sections": {
        "improvements": {
            "year_built": 1995,
            ...
        }
    },
    "correction_summary": "Fixed 1 OCR error: year_built I995 → 1995"
}
```

Be thorough in your corrections. Document every change clearly for the audit trail.
"""

    def get_tools(self) -> List[BaseTool]:
        """Return correction tools."""
        return get_corrector_tools()

    async def run(
        self,
        input_data: CorrectorInput,
    ) -> CorrectorOutput:
        """
        Execute corrections for suspicious fields.

        Args:
            input_data: CorrectorInput with sections and suspicious fields

        Returns:
            CorrectorOutput with corrections and updated sections
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

        # Simple ReAct loop (max 5 iterations for corrections)
        all_messages = list(messages)
        max_iterations = 5

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
        return self._parse_output(raw_response, input_data.sections)

    def _format_input(self, input_data: CorrectorInput) -> str:
        """Format the correction task prompt."""
        parts = [
            "## Correction Task",
            "",
            f"**PDF Path:** {input_data.pdf_path}",
            "",
            "### Current Sections (with errors)",
            "```json",
            json.dumps(input_data.sections, indent=2),
            "```",
            "",
            "### Original Field Sources (use DIFFERENT method for corrections!)",
            "```json",
            json.dumps(input_data.field_sources, indent=2),
            "```",
            "",
            "### Suspicious Fields to Correct",
        ]

        for i, sf in enumerate(input_data.suspicious_fields, 1):
            # Get original source for this field
            section = sf.field_key.split(".")[0] if "." in sf.field_key else "unknown"
            field_name = sf.field_key.split(".")[-1] if "." in sf.field_key else sf.field_key
            original_source = input_data.field_sources.get(section, {}).get(field_name, "unknown")

            parts.extend([
                "",
                f"**Field {i}: {sf.field_key}**",
                f"- Current value: {sf.current_value}",
                f"- Original source: {original_source} (DO NOT use this method again!)",
                f"- Issue type: {sf.issue_type}",
                f"- Reasoning: {sf.reasoning}",
                f"- Suggested method: {sf.suggested_recheck_method}",
            ])

        parts.extend([
            "",
            "## Instructions",
            "",
            "1. For each suspicious field, use a DIFFERENT extraction method than the original source",
            "2. Verify the new value makes sense",
            "3. Update the sections with corrected values",
            "4. Document each correction clearly",
            "5. Output results in the specified JSON format",
        ])

        return "\n".join(parts)

    def _parse_output(
        self,
        response: str,
        original_sections: Dict[str, Dict[str, Any]],
    ) -> CorrectorOutput:
        """Parse agent response into structured output."""
        corrections_made = []
        updated_sections = dict(original_sections)  # Start with original
        correction_summary = "No corrections applied"

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
                correction_summary = data.get("correction_summary", "Corrections applied")

                # Parse corrections
                for corr_data in data.get("corrections_made", []):
                    corrections_made.append(FieldCorrection(
                        field_key=corr_data.get("field_key", "unknown"),
                        old_value=corr_data.get("old_value"),
                        new_value=corr_data.get("new_value"),
                        correction_source=corr_data.get("correction_source", "unknown"),
                        correction_reasoning=corr_data.get("correction_reasoning", ""),
                    ))

                # Get updated sections
                if "updated_sections" in data:
                    updated_sections = data["updated_sections"]

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
        except Exception as e:
            logger.error(f"Error parsing corrector output: {e}")

        return CorrectorOutput(
            corrections_made=corrections_made,
            updated_sections=updated_sections,
            correction_summary=correction_summary,
        )


async def run_corrector_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node function for CorrectorAgent.

    Args:
        state: AppraisalExtractionState dict

    Returns:
        Updated state with corrections applied
    """
    from datetime import datetime
    import time

    start_time = time.time()
    agent = CorrectorAgent()

    # Convert suspicious field dicts back to SuspiciousField objects
    suspicious_fields = [
        SuspiciousField(
            field_key=sf["field_key"],
            current_value=sf["current_value"],
            issue_type=sf["issue_type"],
            reasoning=sf["reasoning"],
            suggested_recheck_method=sf["suggested_recheck_method"],
        )
        for sf in state["suspicious_fields"]
    ]

    input_data = CorrectorInput(
        sections=state["sections"],
        suspicious_fields=suspicious_fields,
        field_sources=state.get("field_sources", {}),
        pdf_path=state["pdf_path"],
    )

    output = await agent.run(input_data)
    duration_ms = int((time.time() - start_time) * 1000)

    # Convert corrections to dicts for state
    correction_dicts = [
        {
            "field_key": c.field_key,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "correction_source": c.correction_source,
            "correction_reasoning": c.correction_reasoning,
        }
        for c in output.corrections_made
    ]

    # Increment iteration count
    iterations = state.get("iterations", 0) + 1

    # Update audit trail
    audit = state.get("audit_trail", {})
    if "agent_calls" not in audit:
        audit["agent_calls"] = []
    audit["agent_calls"].append({
        "agent_name": "CorrectorAgent",
        "timestamp": datetime.utcnow().isoformat(),
        "input_summary": f"Correcting {len(suspicious_fields)} flagged fields",
        "output_summary": output.correction_summary,
        "tools_used": list(set(c.correction_source for c in output.corrections_made)),
        "duration_ms": duration_ms,
    })

    # Record corrections in field history
    if "field_history" not in audit:
        audit["field_history"] = []
    for c in output.corrections_made:
        audit["field_history"].append({
            "field_key": c.field_key,
            "timestamp": datetime.utcnow().isoformat(),
            "action": "corrected",
            "value": c.new_value,
            "source": c.correction_source,
            "confidence": 0.85,  # Corrected values have moderate-high confidence
            "notes": f"Was: {c.old_value}. {c.correction_reasoning}",
        })

    # Update state with corrected sections
    return {
        **state,
        "sections": output.updated_sections,
        "corrections_made": state.get("corrections_made", []) + correction_dicts,
        "iterations": iterations,
        # Clear suspicious fields after correction attempt
        "suspicious_fields": [],
        "audit_trail": audit,
    }
