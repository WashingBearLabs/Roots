"""Node Explorer Demo — educational agents.

Each agent demonstrates a specific node type with deterministic,
predictable outputs suitable for a guided tour.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Closure for tracking retry attempts across calls per run
_content_deep_call_counts: dict[str, int] = {}


async def classify_item(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates the 'agent' node type.

    Output is written to state['classify_output'].
    """
    await asyncio.sleep(0.3)
    return {
        "output": {
            "category": "document",
            "confidence": 0.95,
        },
    }


async def check_format(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates 'agent_pool' — format validation member.

    One of three validators in the pool. Checks document format.
    """
    await asyncio.sleep(0.3)
    return {
        "output": {
            "format_score": 0.9,
            "format_valid": True,
        },
    }


async def validate_schema(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates 'agent_pool' — schema validation member.

    One of three validators in the pool. Validates document schema.
    """
    await asyncio.sleep(0.3)
    return {
        "output": {
            "schema_score": 0.85,
            "schema_valid": True,
        },
    }


async def analyze_content_quality(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates 'agent_pool' — content quality member.

    One of three validators in the pool. Analyzes content quality.
    Contributes the overall_score used by the quality_gate decision.
    """
    await asyncio.sleep(0.3)
    return {
        "output": {
            "quality_score": 0.8,
            "overall_score": 0.85,
        },
    }


async def analyze_content_deep(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates the 'retry' feature on agent nodes.

    Fails on the first call with a simulated transient error,
    then succeeds on the second call. Output is written to
    state['content_analysis'].
    """
    await asyncio.sleep(0.3)
    state = input.get("work_item_state", {})
    run_id = state.get("_run_id", "default")

    count = _content_deep_call_counts.get(run_id, 0) + 1
    _content_deep_call_counts[run_id] = count

    if count == 1:
        raise Exception("Simulated transient failure")  # noqa: TRY002

    return {
        "output": {
            "depth": "thorough",
            "findings": [
                "Structure is well-organized",
                "Content meets quality standards",
                "No issues detected",
            ],
        },
    }


async def analyze_metadata_deep(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """DEMO: Demonstrates a fork branch agent.

    Runs in parallel with analyze_content_deep inside a fork/join.
    Output is written to state['metadata_analysis'].
    """
    await asyncio.sleep(0.3)
    return {
        "output": {
            "metadata_score": 0.9,
            "fields_checked": 12,
        },
    }
