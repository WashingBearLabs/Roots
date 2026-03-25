"""API Explorer Demo — echo agent.

Returns the work item state back as an echo with a timestamp,
useful for experimenting with the Roots API.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Echo the current work item state with a timestamp."""
    return {
        "echo": input.get("work_item_state", {}),
        "timestamp": datetime.now(UTC).isoformat(),
    }
