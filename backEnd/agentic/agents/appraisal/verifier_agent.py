"""
Verifier Agent for appraisal data verification.

Role: "Be skeptical. Find errors. Question everything."

Checks for:
- Plausibility (year_built 1800-current, GLA 500-10000)
- OCR errors (0↔O, 1↔I, digit transposition)
- Cross-field consistency (GLA vs bedrooms, contract vs appraised)
- Low confidence fields

The agent is intentionally thorough - better to flag than miss an error.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from ...config.llm_providers import get_llm_for_stage
from .schemas import SuspiciousField, VerifierInput, VerifierOutput
from .tools import get_verifier_tools

logger = logging.getLogger(__name__)


class VerifierAgent:
    """
    Agent that verifies extracted appraisal data for plausibility.

    Uses a combination of:
    1. Rule-based validation (via validate_extraction tool)
    2. LLM reasoning for subtle errors
    3. Visual re-verification for suspicious fields
    """

    def __init__(self, model_override: Optional[str] = None):
        self.stage_name = "appraisal_verification"
        self._model_override = model_override
        self._llm: Optional[BaseChatModel] = None

    @property
    def llm(self) -> BaseChatModel:
        """Get the LLM for this agent (lazy loaded)."""
        if self._llm is None:
            self._llm = get_llm_for_stage(self.stage_name)
        return self._llm

    def get_system_prompt(self) -> str:
        """Return the system prompt for verification."""
        return """You are a skeptical appraisal data verifier. Your role is to find errors
in extracted data before it's used for IRS cost segregation.

## Your Mindset

Be SKEPTICAL. Question everything. It's better to flag a correct value than
miss an error that could cause IRS audit issues.

## Available Tools

1. **validate_extraction** (FREE)
   - Runs rule-based validation checks
   - Catches common issues: missing fields, out-of-range values, date formats

2. **vision_recheck_field** (PAID $0.05-0.10)
   - Visual re-extraction of a single field
   - Use when you suspect OCR error or need visual confirmation

## What to Check

### Plausibility Checks
- year_built: Should be 1800-2026 (current year + 1 for new construction)
- gross_living_area: Residential typically 500-15000 sq ft
- contract_price/appraised_value: Should be $10,000-$50,000,000
- bedrooms: Typically 1-10 for residential
- bathrooms: Typically 1-15 for residential

### OCR Error Patterns
- 0 ↔ O (zero vs letter O)
- 1 ↔ I or l (one vs I or lowercase L)
- 5 ↔ S
- 8 ↔ B
- Digit transposition (1995 vs 1959)

### Consistency Checks
- GLA should be reasonable for bedroom count (typically 300-1500 sq ft per bedroom)
- Contract price vs appraised value: should be within 20%
- Effective date should be recent (within 6 months typically)
- Year built should be before effective date

### Low Confidence Fields
- Any field with confidence < 0.80 should be flagged
- Critical fields with confidence < 0.90 MUST be flagged

## Output Format

After verification, provide your findings in this JSON format:
```json
{
    "all_plausible": false,
    "suspicious_fields": [
        {
            "field_key": "improvements.year_built",
            "current_value": "I995",
            "issue_type": "ocr_error",
            "reasoning": "Looks like OCR misread '1' as 'I'. Should be 1995.",
            "suggested_recheck_method": "vision"
        }
    ],
    "recommend_correction": true,
    "verification_notes": "Found 2 OCR errors and 1 implausible value."
}
```

## Issue Types
- "ocr_error": Likely character misread by OCR
- "implausible": Value outside reasonable range
- "inconsistent": Conflicts with other fields
- "low_confidence": Below confidence threshold
- "missing": Required field is empty

Remember: Be thorough. Flag anything suspicious. Let the corrector agent fix it.
"""

    def get_tools(self) -> List[BaseTool]:
        """Return verification tools."""
        return get_verifier_tools()

    async def run(
        self,
        input_data: VerifierInput,
    ) -> VerifierOutput:
        """
        Execute verification with skeptical analysis.

        Args:
            input_data: VerifierInput with sections and confidences

        Returns:
            VerifierOutput with suspicious fields flagged
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

        # Simple ReAct loop (max 3 iterations for verification)
        all_messages = list(messages)
        max_iterations = 3

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
        return self._parse_output(raw_response)

    def _format_input(self, input_data: VerifierInput) -> str:
        """Format the verification task prompt."""
        parts = [
            "## Verification Task",
            "",
            "Review the following extracted appraisal data for errors:",
            "",
            "### Extracted Sections",
            "```json",
            json.dumps(input_data.sections, indent=2),
            "```",
            "",
            "### Field Confidences",
            "```json",
            json.dumps(input_data.field_confidences, indent=2),
            "```",
            "",
            "### Field Sources",
            "```json",
            json.dumps(input_data.field_sources, indent=2),
            "```",
            "",
            "## Instructions",
            "",
            "1. First, use validate_extraction to run rule-based checks",
            "2. Review the results and apply your skeptical analysis",
            "3. Look for OCR errors, implausible values, and inconsistencies",
            "4. Flag any critical fields with confidence < 0.90",
            "5. If you suspect an OCR error, you can use vision_recheck_field to verify",
            "6. Output your findings in the specified JSON format",
        ]

        return "\n".join(parts)

    def _parse_output(self, response: str) -> VerifierOutput:
        """Parse agent response into structured output."""
        all_plausible = True
        suspicious_fields = []
        recommend_correction = False
        verification_notes = None

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
                all_plausible = data.get("all_plausible", True)
                recommend_correction = data.get("recommend_correction", False)
                verification_notes = data.get("verification_notes")

                for field_data in data.get("suspicious_fields", []):
                    suspicious_fields.append(SuspiciousField(
                        field_key=field_data.get("field_key", "unknown"),
                        current_value=field_data.get("current_value"),
                        issue_type=field_data.get("issue_type", "unknown"),
                        reasoning=field_data.get("reasoning", ""),
                        suggested_recheck_method=field_data.get("suggested_recheck_method", "vision"),
                    ))

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
            # If parsing fails, assume not all plausible to be safe
            all_plausible = False
        except Exception as e:
            logger.error(f"Error parsing verifier output: {e}")
            all_plausible = False

        return VerifierOutput(
            all_plausible=all_plausible,
            suspicious_fields=suspicious_fields,
            recommend_correction=recommend_correction or len(suspicious_fields) > 0,
            verification_notes=verification_notes,
        )


async def run_verifier_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node function for VerifierAgent.

    Args:
        state: AppraisalExtractionState dict

    Returns:
        Updated state with verification results
    """
    from datetime import datetime
    import time

    start_time = time.time()
    agent = VerifierAgent()

    input_data = VerifierInput(
        sections=state["sections"],
        field_confidences=state["field_confidences"],
        field_sources=state["field_sources"],
    )

    output = await agent.run(input_data)
    duration_ms = int((time.time() - start_time) * 1000)

    # Convert suspicious fields to dicts for state
    suspicious_dicts = [
        {
            "field_key": sf.field_key,
            "current_value": sf.current_value,
            "issue_type": sf.issue_type,
            "reasoning": sf.reasoning,
            "suggested_recheck_method": sf.suggested_recheck_method,
        }
        for sf in output.suspicious_fields
    ]

    # Update audit trail
    audit = state.get("audit_trail", {})
    if "agent_calls" not in audit:
        audit["agent_calls"] = []
    audit["agent_calls"].append({
        "agent_name": "VerifierAgent",
        "timestamp": datetime.utcnow().isoformat(),
        "input_summary": f"Verifying {len(state['sections'])} sections",
        "output_summary": f"all_plausible={output.all_plausible}, flagged={len(suspicious_dicts)} fields",
        "tools_used": ["validate_extraction"],
        "duration_ms": duration_ms,
    })

    # Record verification flags in field history
    if "field_history" not in audit:
        audit["field_history"] = []
    for sf in output.suspicious_fields:
        audit["field_history"].append({
            "field_key": sf.field_key,
            "timestamp": datetime.utcnow().isoformat(),
            "action": "flagged",
            "value": sf.current_value,
            "source": "verifier",
            "confidence": 0.0,  # Flagged = low confidence
            "notes": f"{sf.issue_type}: {sf.reasoning}",
        })

    # Update state
    return {
        **state,
        "all_plausible": output.all_plausible,
        "suspicious_fields": suspicious_dicts,
        "audit_trail": audit,
    }
