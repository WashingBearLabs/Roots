"""Configuration override application for installed Root packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from roots.core.schema import ProcessDefinition
from roots.packaging.manifest import ConfigOverride, ConfigTemplate, RootManifest


class ConfigError(Exception):
    """Raised when a configuration override cannot be applied."""


def _validate_value(value: Any, override: ConfigOverride) -> None:
    """Validate a value against a ConfigOverride's constraints."""
    if override.constraints is None:
        return

    if "min" in override.constraints:
        if value < override.constraints["min"]:
            raise ConfigError(
                f"Value {value!r} for '{override.path}' is below "
                f"minimum {override.constraints['min']}"
            )

    if "max" in override.constraints:
        if value > override.constraints["max"]:
            raise ConfigError(
                f"Value {value!r} for '{override.path}' is above "
                f"maximum {override.constraints['max']}"
            )

    if "enum" in override.constraints:
        if value not in override.constraints["enum"]:
            raise ConfigError(
                f"Value {value!r} for '{override.path}' is not in "
                f"allowed values: {override.constraints['enum']}"
            )


def _coerce_value(value: Any, value_type: str) -> Any:
    """Coerce a value to the expected type."""
    try:
        if value_type == "int":
            return int(value)
        elif value_type == "float":
            return float(value)
        elif value_type == "bool":
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    return True
                elif value.lower() in ("false", "0", "no"):
                    return False
                raise ValueError(f"Cannot convert {value!r} to bool")
            return bool(value)
        elif value_type == "string":
            return str(value)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"Cannot convert {value!r} to {value_type}: {exc}"
        ) from exc
    return value


def apply_override(
    process: ProcessDefinition, path: str, value: Any,
    config_overrides: list[ConfigOverride] | None = None,
) -> ProcessDefinition:
    """Apply a single configuration override to a process definition.

    The path uses dot-notation: "nodes.<node_id>.config.<field>"
    or "nodes.<node_id>.config.retry.<field>" for retry settings.

    Returns a new ProcessDefinition with the override applied.
    Raises ConfigError if the path is invalid or value fails constraints.
    """
    parts = path.split(".")

    if len(parts) < 4 or parts[0] != "nodes" or parts[2] != "config":
        raise ConfigError(
            f"Invalid override path '{path}'. "
            f"Expected format: nodes.<node_id>.config.<field> "
            f"or nodes.<node_id>.config.retry.<field>"
        )

    node_id = parts[1]
    field_parts = parts[3:]  # e.g. ["confidence_threshold"] or ["retry", "max_attempts"]

    # Find the matching ConfigOverride for constraint validation
    if config_overrides:
        matching = [o for o in config_overrides if o.path == path]
        if matching:
            override = matching[0]
            value = _coerce_value(value, override.value_type)
            _validate_value(value, override)

    # Find the node
    node = process.get_node(node_id)
    if node is None:
        raise ConfigError(
            f"Node '{node_id}' not found in process '{process.id}'. "
            f"Available nodes: {', '.join(n.id for n in process.nodes)}"
        )

    # Dump the process to a dict, modify, and reconstruct.
    # model_dump(mode='json') serializes BaseModel configs to empty dicts,
    # so we must manually re-dump node configs for proper round-tripping.
    process_data = process.model_dump(mode="json")
    for n in process_data["nodes"]:
        node_obj = process.get_node(n["id"])
        if node_obj is not None and isinstance(node_obj.config, BaseModel):
            n["config"] = node_obj.config.model_dump(mode="json")

    # Find the node in the serialized data
    node_data = None
    for n in process_data["nodes"]:
        if n["id"] == node_id:
            node_data = n
            break

    if node_data is None:
        raise ConfigError(f"Node '{node_id}' not found in serialized process")

    # Handle retry fields (path: nodes.<id>.config.retry.<field>)
    if field_parts[0] == "retry":
        if len(field_parts) != 2:
            raise ConfigError(
                f"Invalid retry override path '{path}'. "
                f"Expected: nodes.<node_id>.config.retry.<field>"
            )
        retry_field = field_parts[1]
        if node_data.get("retry") is None:
            raise ConfigError(
                f"Node '{node_id}' does not have retry configuration"
            )
        if retry_field not in node_data["retry"]:
            raise ConfigError(
                f"Retry field '{retry_field}' not found on node '{node_id}'. "
                f"Available retry fields: {', '.join(node_data['retry'].keys())}"
            )
        node_data["retry"][retry_field] = value
    else:
        # Handle config fields (path: nodes.<id>.config.<field>)
        if len(field_parts) != 1:
            raise ConfigError(
                f"Invalid override path '{path}'. "
                f"Expected: nodes.<node_id>.config.<field>"
            )
        config_field = field_parts[0]
        if config_field not in node_data["config"]:
            raise ConfigError(
                f"Config field '{config_field}' not found on node '{node_id}'. "
                f"Available config fields: {', '.join(node_data['config'].keys())}"
            )
        node_data["config"][config_field] = value

    return ProcessDefinition.model_validate(process_data)


def apply_overrides_from_file(
    process: ProcessDefinition,
    overrides_path: Path,
    config_overrides: list[ConfigOverride] | None = None,
) -> ProcessDefinition:
    """Read a YAML file of overrides and apply each sequentially.

    Expected YAML format:
        overrides:
          nodes.triage.config.confidence_threshold: 0.9
          nodes.triage.config.model: "gpt-4o"
          nodes.respond.retry.max_attempts: 5

    Returns the modified ProcessDefinition.
    Raises ConfigError if the file is invalid or any override fails.
    """
    try:
        raw = yaml.safe_load(overrides_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise ConfigError(f"Failed to read overrides file: {exc}") from exc

    if not isinstance(raw, dict) or "overrides" not in raw:
        raise ConfigError(
            "Overrides file must contain a top-level 'overrides' mapping"
        )

    overrides = raw["overrides"]
    if not isinstance(overrides, dict):
        raise ConfigError("'overrides' must be a mapping of path: value pairs")

    result = process
    for path, value in overrides.items():
        result = apply_override(result, path, value, config_overrides=config_overrides)

    return result


def list_overrides(manifest: RootManifest) -> list[ConfigOverride]:
    """Return the config overrides from the manifest."""
    return list(manifest.config_overrides)


def list_templates(manifest: RootManifest) -> list[ConfigTemplate]:
    """Return the config templates from the manifest."""
    return list(manifest.config_templates)


def apply_template(
    process: ProcessDefinition,
    template: ConfigTemplate,
    config_overrides: list[ConfigOverride] | None = None,
) -> ProcessDefinition:
    """Apply all overrides from a config template to a process definition.

    Returns a new ProcessDefinition with the template overrides applied.
    Raises ConfigError if any override in the template fails.
    """
    result = process
    for path, value in template.overrides.items():
        result = apply_override(result, path, value, config_overrides=config_overrides)
    return result
