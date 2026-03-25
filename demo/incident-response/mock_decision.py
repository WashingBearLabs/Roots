"""Mock LLM callable for incident response triage.

Implements the LLMCompletionFunc protocol using keyword matching
instead of a real LLM call. Used as the default decision engine
when no --model flag is provided.
"""

from __future__ import annotations

import json
from typing import Any

from roots.core.llm import LLMResponse, ToolCall

# Keyword → (selected_edge_target, confidence, reasoning)
_KEYWORD_RULES: list[tuple[str, str, float, str]] = [
    ("brute force", "respond", 0.92, "Brute force detected — reset credentials"),
    ("malware", "respond", 0.88, "Malware detected — isolate endpoint"),
    ("exfiltration", "respond", 0.85, "Data exfiltration — block source IP"),
    ("port scan", "close", 0.45, "Port scan is low severity — close as benign"),
]

_DEFAULT_TARGET = "escalation_review"
_DEFAULT_CONFIDENCE = 0.6
_DEFAULT_REASONING = "Unrecognized pattern — escalate to analyst"


async def mock_triage_decision(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | str | None = None,
) -> LLMResponse:
    """Mock LLM callable that routes incidents based on keyword matching.

    Scans the user message content for keywords and returns an
    LLMResponse with a ToolCall matching the make_decision schema.
    """
    # Extract text from messages to scan for keywords
    text = ""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            text += content.lower() + " "

    # Match keywords
    target = _DEFAULT_TARGET
    confidence = _DEFAULT_CONFIDENCE
    reasoning = _DEFAULT_REASONING

    for keyword, kw_target, kw_conf, kw_reason in _KEYWORD_RULES:
        if keyword in text:
            target = kw_target
            confidence = kw_conf
            reasoning = kw_reason
            break

    decision = {
        "selected_edge_target": target,
        "confidence": confidence,
        "reasoning": reasoning,
    }

    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(name="make_decision", arguments=json.dumps(decision)),
        ],
    )
