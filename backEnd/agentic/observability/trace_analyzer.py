"""
LangSmith Trace Analyzer

Fetches and analyzes LangGraph workflow traces from LangSmith
for debugging, alerting, and audit trail purposes.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cost per 1K tokens (approximate)
TOKEN_COSTS = {
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-5": {"input": 0.01, "output": 0.03},
    "gpt-5.2": {"input": 0.01, "output": 0.03},
    "default": {"input": 0.001, "output": 0.002},
}


@dataclass
class AgentRun:
    """Summary of a single agent/tool run."""
    name: str
    run_type: str  # "chain", "llm", "tool"
    status: str  # "success", "error", "pending"
    duration_seconds: float
    tokens: int = 0
    error: Optional[str] = None
    inputs_summary: Optional[str] = None
    outputs_summary: Optional[str] = None
    flagged_count: int = 0


@dataclass
class TraceSummary:
    """Complete summary of a LangGraph workflow trace."""
    trace_id: str
    study_id: Optional[str] = None
    langsmith_url: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0
    total_tokens: int = 0
    total_cost_usd: float = 0
    agents: List[AgentRun] = field(default_factory=list)
    flagged_fields: List[str] = field(default_factory=list)
    final_state: str = "unknown"
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Firestore storage."""
        return {
            "trace_id": self.trace_id,
            "study_id": self.study_id,
            "langsmith_url": self.langsmith_url,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "agents": [
                {
                    "name": a.name,
                    "run_type": a.run_type,
                    "status": a.status,
                    "duration_seconds": round(a.duration_seconds, 2),
                    "tokens": a.tokens,
                    "error": a.error,
                    "flagged_count": a.flagged_count,
                }
                for a in self.agents
            ],
            "flagged_fields": self.flagged_fields,
            "final_state": self.final_state,
            "errors": self.errors,
        }

    def to_slack_message(self) -> str:
        """Format as Slack message for alerts."""
        # Status emoji
        if self.errors:
            status_emoji = "🚨"
            status_text = "Workflow Error"
        elif self.flagged_fields:
            status_emoji = "⚠️"
            status_text = "Workflow Needs Review"
        else:
            status_emoji = "✅"
            status_text = "Workflow Completed"

        # Build agent trace
        agent_lines = []
        for agent in self.agents:
            status_icon = "✅" if agent.status == "success" else "❌" if agent.status == "error" else "⏳"
            line = f"├─ {agent.name}: {agent.duration_seconds:.0f}s"
            if agent.flagged_count > 0:
                line += f" → flagged {agent.flagged_count} fields"
            if agent.error:
                line += f" ❌"
            agent_lines.append(f"{status_icon} {line}")

        # Make last line use └─ instead of ├─
        if agent_lines:
            agent_lines[-1] = agent_lines[-1].replace("├─", "└─")

        agent_trace = "\n".join(agent_lines)

        # Flagged fields
        flagged_text = ""
        if self.flagged_fields:
            flagged_list = "\n".join([f"  • {f}" for f in self.flagged_fields[:10]])
            if len(self.flagged_fields) > 10:
                flagged_list += f"\n  • ... and {len(self.flagged_fields) - 10} more"
            flagged_text = f"\n\n*❌ {len(self.flagged_fields)} Flagged Fields:*\n{flagged_list}"

        # Errors
        error_text = ""
        if self.errors:
            error_list = "\n".join([f"  • {e[:100]}" for e in self.errors[:5]])
            error_text = f"\n\n*🔴 Errors:*\n{error_list}"

        # Format duration
        minutes = int(self.duration_seconds // 60)
        seconds = int(self.duration_seconds % 60)
        duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

        # Build message
        message = f"""{status_emoji} *{status_text}* - Study: {self.study_id or 'Unknown'}

*📊 Trace Summary:*
```
{agent_trace}
```
{flagged_text}{error_text}

*💰 Cost:* ${self.total_cost_usd:.4f} | *⏱️ Duration:* {duration_str} | *🔢 Tokens:* {self.total_tokens:,}
"""

        if self.langsmith_url:
            message += f"\n*🔗 LangSmith:* {self.langsmith_url}"

        return message


class TraceAnalyzer:
    """Fetches and analyzes LangSmith traces."""

    def __init__(self):
        self.api_key = os.environ.get("LANGCHAIN_API_KEY")
        self.project_name = os.environ.get("LANGCHAIN_PROJECT", "basis-agentic")
        self.client = None
        self._initialized = False

    def _ensure_client(self) -> bool:
        """Initialize LangSmith client."""
        if self._initialized:
            return self.client is not None

        self._initialized = True

        if not self.api_key:
            logger.warning("LANGCHAIN_API_KEY not set, trace analysis disabled")
            return False

        try:
            from langsmith import Client
            self.client = Client()
            return True
        except ImportError:
            logger.warning("langsmith package not installed")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize LangSmith client: {e}")
            return False

    def get_trace_by_id(self, trace_id: str) -> Optional[TraceSummary]:
        """Fetch and analyze a specific trace by ID."""
        if not self._ensure_client():
            return None

        try:
            runs = list(self.client.list_runs(
                project_name=self.project_name,
                trace_id=trace_id,
                limit=100
            ))

            if not runs:
                logger.warning(f"No runs found for trace {trace_id}")
                return None

            return self._analyze_runs(runs, trace_id)

        except Exception as e:
            logger.error(f"Failed to fetch trace {trace_id}: {e}")
            return None

    def get_latest_trace(self, study_id: Optional[str] = None) -> Optional[TraceSummary]:
        """Get the most recent trace, optionally filtered by study_id."""
        if not self._ensure_client():
            return None

        try:
            # Get recent root runs
            runs = list(self.client.list_runs(
                project_name=self.project_name,
                limit=50,
                is_root=True
            ))

            if not runs:
                return None

            # Find the most recent workflow run
            latest = None
            for run in runs:
                if run.run_type in ["chain", "workflow"]:
                    # If study_id filter provided, check inputs
                    if study_id:
                        inputs = run.inputs or {}
                        run_study_id = inputs.get("study_id") or inputs.get("configurable", {}).get("study_id")
                        if run_study_id != study_id:
                            continue
                    latest = run
                    break

            if not latest:
                return None

            # Get all runs in this trace
            trace_runs = list(self.client.list_runs(
                project_name=self.project_name,
                trace_id=latest.trace_id,
                limit=100
            ))

            return self._analyze_runs(trace_runs, str(latest.trace_id))

        except Exception as e:
            logger.error(f"Failed to fetch latest trace: {e}")
            return None

    def _analyze_runs(self, runs: List[Any], trace_id: str) -> TraceSummary:
        """Analyze a list of runs into a TraceSummary."""
        summary = TraceSummary(trace_id=trace_id)

        # Sort by dotted_order for proper sequencing
        runs.sort(key=lambda x: x.dotted_order or "")

        # Find root run for timing
        root_run = None
        for run in runs:
            if run.parent_run_id is None:
                root_run = run
                break

        if root_run:
            summary.started_at = root_run.start_time
            summary.completed_at = root_run.end_time
            if root_run.start_time and root_run.end_time:
                summary.duration_seconds = (root_run.end_time - root_run.start_time).total_seconds()

            # Extract study_id from inputs
            inputs = root_run.inputs or {}
            summary.study_id = inputs.get("study_id") or inputs.get("configurable", {}).get("study_id")

        # Build LangSmith URL
        org_id = os.environ.get("LANGCHAIN_ORG_ID", "")
        if org_id:
            summary.langsmith_url = f"https://smith.langchain.com/o/{org_id}/projects/p/{self.project_name}/r/{trace_id}"
        else:
            summary.langsmith_url = f"https://smith.langchain.com/public/{trace_id}/r"

        # Analyze each run
        seen_agents = set()
        for run in runs:
            # Skip if we've already processed this agent (avoid duplicates)
            agent_key = f"{run.name}_{run.run_type}"

            # Calculate duration
            duration = 0
            if run.start_time and run.end_time:
                duration = (run.end_time - run.start_time).total_seconds()

            # Count tokens
            tokens = 0
            if hasattr(run, 'total_tokens') and run.total_tokens:
                tokens = run.total_tokens
            elif run.outputs:
                # Try to extract from LLM outputs
                usage = run.outputs.get("llm_output", {}).get("token_usage", {})
                tokens = usage.get("total_tokens", 0)

            summary.total_tokens += tokens

            # Track errors
            if run.error:
                summary.errors.append(f"{run.name}: {run.error[:200]}")

            # Extract flagged fields from verifier outputs
            if "verifier" in run.name.lower() and run.outputs:
                output = run.outputs.get("output", {})
                if isinstance(output, dict):
                    flagged = output.get("flagged_fields", [])
                    if flagged:
                        summary.flagged_fields = list(set(summary.flagged_fields + flagged))

            # Only add main agents (not every LLM call)
            if run.run_type == "chain" and run.name not in ["LangGraph", "RunnableSequence"]:
                if agent_key not in seen_agents:
                    seen_agents.add(agent_key)

                    # Determine status
                    status = "success"
                    if run.error:
                        status = "error"
                    elif run.status == "pending":
                        status = "pending"

                    # Check for flagged count in outputs
                    flagged_count = 0
                    if run.outputs:
                        output = run.outputs.get("output", {})
                        if isinstance(output, dict):
                            flagged_count = len(output.get("flagged_fields", []))

                    agent_run = AgentRun(
                        name=run.name,
                        run_type=run.run_type,
                        status=status,
                        duration_seconds=duration,
                        tokens=tokens,
                        error=run.error[:200] if run.error else None,
                        flagged_count=flagged_count,
                    )
                    summary.agents.append(agent_run)

        # Calculate cost estimate
        summary.total_cost_usd = self._estimate_cost(summary.total_tokens)

        # Determine final state
        if summary.errors:
            summary.final_state = "error"
        elif summary.flagged_fields:
            summary.final_state = "needs_review"
        else:
            summary.final_state = "completed"

        return summary

    def _estimate_cost(self, tokens: int) -> float:
        """Estimate cost based on token count."""
        # Assume 50/50 input/output split and default pricing
        cost_per_1k = TOKEN_COSTS["default"]
        input_tokens = tokens // 2
        output_tokens = tokens - input_tokens

        cost = (input_tokens / 1000 * cost_per_1k["input"]) + \
               (output_tokens / 1000 * cost_per_1k["output"])

        return cost


# Singleton instance
_analyzer: Optional[TraceAnalyzer] = None


def get_trace_analyzer() -> TraceAnalyzer:
    """Get the singleton TraceAnalyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = TraceAnalyzer()
    return _analyzer


async def get_trace_summary(trace_id: str) -> Optional[TraceSummary]:
    """Convenience function to get a trace summary."""
    analyzer = get_trace_analyzer()
    return analyzer.get_trace_by_id(trace_id)


async def get_latest_trace_summary(study_id: Optional[str] = None) -> Optional[TraceSummary]:
    """Convenience function to get the latest trace summary."""
    analyzer = get_trace_analyzer()
    return analyzer.get_latest_trace(study_id)


async def save_trace_to_firestore(
    study_id: str,
    trace_id: Optional[str] = None,
) -> bool:
    """
    Save trace summary to Firestore for audit trail.

    Stores in studies/{study_id}/traces/{trace_id}

    Args:
        study_id: Study ID
        trace_id: Optional specific trace ID (uses latest if not provided)

    Returns:
        True if saved successfully
    """
    try:
        from ..firestore.client import get_firestore_client

        analyzer = get_trace_analyzer()

        if trace_id:
            summary = analyzer.get_trace_by_id(trace_id)
        else:
            summary = analyzer.get_latest_trace(study_id)

        if not summary:
            logger.warning(f"No trace found for study {study_id}")
            return False

        # Get Firestore client
        db = get_firestore_client()

        # Save to subcollection
        trace_ref = db.collection("studies").document(study_id).collection("traces").document(summary.trace_id)
        trace_ref.set(summary.to_dict())

        logger.info(f"Saved trace {summary.trace_id} to Firestore for study {study_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to save trace to Firestore: {e}")
        return False


async def get_trace_from_firestore(
    study_id: str,
    trace_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get trace summary from Firestore.

    Args:
        study_id: Study ID
        trace_id: Trace ID

    Returns:
        Trace data dict or None
    """
    try:
        from ..firestore.client import get_firestore_client

        db = get_firestore_client()
        trace_ref = db.collection("studies").document(study_id).collection("traces").document(trace_id)
        doc = trace_ref.get()

        if doc.exists:
            return doc.to_dict()
        return None

    except Exception as e:
        logger.error(f"Failed to get trace from Firestore: {e}")
        return None


async def list_traces_from_firestore(
    study_id: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    List recent traces for a study from Firestore.

    Args:
        study_id: Study ID
        limit: Maximum number of traces to return

    Returns:
        List of trace data dicts
    """
    try:
        from ..firestore.client import get_firestore_client

        db = get_firestore_client()
        traces_ref = (
            db.collection("studies")
            .document(study_id)
            .collection("traces")
            .order_by("started_at", direction="DESCENDING")
            .limit(limit)
        )

        traces = []
        for doc in traces_ref.stream():
            trace_data = doc.to_dict()
            trace_data["id"] = doc.id
            traces.append(trace_data)

        return traces

    except Exception as e:
        logger.error(f"Failed to list traces from Firestore: {e}")
        return []
