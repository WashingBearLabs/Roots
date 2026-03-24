"""YAML parsing pipeline for Roots process definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from roots.core.schema import ProcessDefinition


def _format_validation_errors(
    error: ValidationError, raw_data: dict[str, Any] | None = None
) -> list[str]:
    """Transform Pydantic ValidationError into user-friendly messages.

    If the error's loc starts with ('nodes', <index>, ...), resolve the node
    at that index to include its id and type in the message.
    """
    messages: list[str] = []
    for err in error.errors():
        loc = err["loc"]
        msg = err["msg"]

        # Try to resolve node context
        if (
            raw_data is not None
            and len(loc) >= 2
            and loc[0] == "nodes"
            and isinstance(loc[1], int)
        ):
            idx = loc[1]
            nodes = raw_data.get("nodes", [])
            if idx < len(nodes) and isinstance(nodes[idx], dict):
                node_id = nodes[idx].get("id", f"index {idx}")
                node_type = nodes[idx].get("type", "unknown")
                remaining = " -> ".join(str(p) for p in loc[2:])
                if remaining:
                    messages.append(
                        f"Node '{node_id}' ({node_type}): "
                        f"{remaining} \u2014 {msg}"
                    )
                else:
                    messages.append(
                        f"Node '{node_id}' ({node_type}): {msg}"
                    )
                continue

        # Generic fallback
        loc_str = " -> ".join(str(p) for p in loc)
        if loc_str:
            messages.append(f"{loc_str} \u2014 {msg}")
        else:
            messages.append(msg)

    return messages


def parse_process_dict(data: dict[str, Any]) -> ProcessDefinition:
    """Validate a dict against the ProcessDefinition model.

    Used by the API which receives JSON dicts, not YAML.

    Raises:
        ValidationError: If the dict fails Pydantic validation.
    """
    return ProcessDefinition.model_validate(data)


def load_process_yaml(path: str | Path) -> ProcessDefinition:
    """Read a YAML file and return a validated ProcessDefinition.

    Raises:
        yaml.YAMLError: If the file contains invalid YAML syntax.
        ValidationError: If the parsed data fails schema validation.
        FileNotFoundError: If the file does not exist.
    """
    file_path = Path(path)
    raw = file_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise yaml.YAMLError(
            f"Expected a YAML mapping at top level, got {type(data).__name__}"
        )
    return parse_process_dict(cast(dict[str, Any], data))


def validate_process_yaml(path: str | Path) -> list[str]:
    """Validate a YAML file and return a list of error strings.

    Returns an empty list if the file is valid.
    """
    try:
        file_path = Path(path)
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"File not found: {path}"]

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"YAML syntax error: {exc}"]

    if not isinstance(data, dict):
        return [
            f"Expected a YAML mapping at top level, got {type(data).__name__}"
        ]

    data_dict = cast(dict[str, Any], data)
    try:
        parse_process_dict(data_dict)
    except ValidationError as exc:
        return _format_validation_errors(exc, raw_data=data_dict)

    return []
