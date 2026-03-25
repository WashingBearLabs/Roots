"""Incident Response Demo — mock agents.

Each agent simulates a step in the incident response pipeline,
returning plausible results based on the work item state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from typing import Any


async def ingest_incident(input: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw incident data into a standard format."""
    await asyncio.sleep(0.2)
    state = input.get("work_item_state", {})
    raw = state.get("raw_alert", {})

    return {
        "source_ip": raw.get("source_ip", "203.0.113.42"),
        "event_type": raw.get("event_type", "brute_force_attempt"),
        "severity": raw.get("severity", "high"),
        "timestamp": raw.get("timestamp", datetime.now(UTC).isoformat()),
    }


async def threat_intel_lookup(input: dict[str, Any]) -> dict[str, Any]:
    """Look up threat intelligence for the incident source IP."""
    await asyncio.sleep(0.3)
    state = input.get("work_item_state", {})
    incident = state.get("normalized_incident", {})
    event_type = incident.get("event_type", "unknown")

    score_map: dict[str, float] = {
        "brute_force_attempt": 0.72,
        "malware_detected": 0.91,
        "data_exfiltration": 0.88,
        "port_scan": 0.25,
    }

    return {
        "threat_score": score_map.get(event_type, 0.5),
        "known_iocs": [incident.get("source_ip", "203.0.113.42")],
        "threat_category": event_type.replace("_", " ").title(),
    }


async def geo_lookup(input: dict[str, Any]) -> dict[str, Any]:
    """Look up geographic information for the incident source IP."""
    await asyncio.sleep(0.2)
    state = input.get("work_item_state", {})
    incident = state.get("normalized_incident", {})
    event_type = incident.get("event_type", "unknown")

    is_suspicious = event_type in ("malware_detected", "data_exfiltration")

    return {
        "country": "RU" if is_suspicious else "US",
        "city": "Moscow" if is_suspicious else "Ashburn",
        "is_vpn": is_suspicious,
        "is_tor": event_type == "data_exfiltration",
    }


async def execute_response(input: dict[str, Any]) -> dict[str, Any]:
    """Execute the incident response action determined by triage."""
    await asyncio.sleep(0.4)
    state = input.get("work_item_state", {})
    incident = state.get("normalized_incident", {})
    event_type = incident.get("event_type", "unknown")

    action_map: dict[str, str] = {
        "brute_force_attempt": "reset_credentials",
        "malware_detected": "isolate_endpoint",
        "data_exfiltration": "block_ip",
        "port_scan": "close_benign",
    }
    action = action_map.get(event_type, "escalate_to_analyst")

    return {
        "action_taken": action,
        "success": True,
        "details": f"Executed {action} for {event_type} from {incident.get('source_ip', 'unknown')}",
    }
