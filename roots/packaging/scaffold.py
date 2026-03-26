"""Scaffold default agent implementations from a process definition."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from roots.core.validator import load_process_yaml
from roots.packaging.extractor import extract_agent_contracts
from roots.packaging.manifest import AgentContract


def scaffold_defaults(process_path: str | Path, output_dir: str | Path | None = None) -> Path:
    """Generate a defaults/ directory with stub agent implementations.

    Args:
        process_path: Path to a process YAML file.
        output_dir: Where to create the defaults/ directory. Defaults to
            a ``defaults/`` directory alongside the process YAML.

    Returns:
        The path to the created defaults/ directory.
    """
    process_path = Path(process_path)
    process = load_process_yaml(process_path)
    contracts = extract_agent_contracts(process)

    if output_dir is None:
        defaults_dir = process_path.parent / "defaults"
    else:
        defaults_dir = Path(output_dir)

    defaults_dir.mkdir(parents=True, exist_ok=True)

    # Write __init__.py
    (defaults_dir / "__init__.py").write_text(
        '"""Default agent implementations for '
        f'{process.name}."""\n'
    )

    # Write agents.py
    agents_code = _generate_agents_module(contracts, process.name)
    (defaults_dir / "agents.py").write_text(agents_code)

    return defaults_dir


def _generate_agents_module(
    contracts: list[AgentContract],
    process_name: str,
) -> str:
    """Generate the agents.py source code with stubs and register_agents."""
    lines: list[str] = []
    lines.append(f'"""Default agent stubs for {process_name}."""\n')
    lines.append("")

    # Generate a stub for each contract
    for contract in contracts:
        lines.append(_generate_stub(contract))
        lines.append("")

    # Generate register_agents function
    lines.append("")
    lines.append("def register_agents(roots):")
    lines.append(f'    """Register all default agent implementations."""')
    if not contracts:
        lines.append("    return []")
    else:
        lines.append("    registered = []")
        for contract in contracts:
            func_name = _safe_function_name(contract.name)
            reg_kwargs = _build_register_kwargs(contract)
            lines.append(
                f"    roots.register_agent("
                f'"{contract.name}", {func_name}{reg_kwargs})'
            )
            lines.append(f'    registered.append("{contract.name}")')
        lines.append("    return registered")
    lines.append("")

    return "\n".join(lines)


def _generate_stub(contract: AgentContract) -> str:
    """Generate a single async stub function for a contract."""
    func_name = _safe_function_name(contract.name)
    lines: list[str] = []
    lines.append(f"async def {func_name}(input: dict) -> dict:")

    # Build docstring
    doc_lines: list[str] = []
    if contract.description:
        doc_lines.append(f"    {contract.description}")
    else:
        doc_lines.append(f"    Default implementation for {contract.name}.")

    if contract.input_schema:
        doc_lines.append("")
        doc_lines.append(
            f"    Expected input schema: {_format_schema_summary(contract.input_schema)}"
        )
    if contract.output_schema:
        doc_lines.append(
            f"    Expected output schema: {_format_schema_summary(contract.output_schema)}"
        )

    doc_lines.append("")
    doc_lines.append("    TODO: Replace with your actual implementation.")

    lines.append('    """')
    lines.extend(doc_lines)
    lines.append('    """')

    # Generate return value that matches output schema structure
    return_value = _generate_stub_return(contract.output_schema)
    lines.append(f"    # TODO: implement")
    lines.append(f"    return {return_value}")

    return "\n".join(lines)


def _safe_function_name(agent_name: str) -> str:
    """Convert an agent name to a valid Python function name."""
    # Replace non-alphanumeric characters with underscores
    name = re.sub(r"[^a-zA-Z0-9_]", "_", agent_name)
    # Ensure it doesn't start with a digit
    if name and name[0].isdigit():
        name = f"agent_{name}"
    return name


def _format_schema_summary(schema: dict[str, Any]) -> str:
    """Format a JSON schema into a compact summary for docstrings."""
    props = schema.get("properties", {})
    if not props:
        return json.dumps(schema, separators=(", ", ": "))

    parts: list[str] = []
    for key, val in props.items():
        type_str = val.get("type", "any")
        parts.append(f"{key}: {type_str}")
    return "{" + ", ".join(parts) + "}"


def _generate_stub_return(output_schema: dict[str, Any] | None) -> str:
    """Generate a minimal valid return dict matching the output schema."""
    if output_schema is None:
        return "{}"

    props = output_schema.get("properties", {})
    if not props:
        return "{}"

    result: dict[str, Any] = {}
    for key, val in props.items():
        result[key] = _default_for_type(val.get("type", "string"))

    return repr(result)


def _default_for_type(type_str: str) -> Any:
    """Return a sensible default value for a JSON schema type."""
    defaults: dict[str, Any] = {
        "string": "",
        "number": 0.0,
        "integer": 0,
        "boolean": False,
        "array": [],
        "object": {},
    }
    return defaults.get(type_str, None)


def _build_register_kwargs(contract: AgentContract) -> str:
    """Build keyword argument string for register_agent call."""
    parts: list[str] = []
    if contract.input_schema:
        parts.append(f"input_schema={contract.input_schema!r}")
    if contract.output_schema:
        parts.append(f"output_schema={contract.output_schema!r}")
    if parts:
        return ", " + ", ".join(parts)
    return ""
