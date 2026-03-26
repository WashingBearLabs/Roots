"""Inspect a .root package and produce a human-readable summary."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from roots.packaging.archive import read_archive


def _format_schema(schema: dict[str, Any] | None) -> str:
    """Format a JSON schema dict into a compact one-line representation."""
    if not schema:
        return "{}"
    props = schema.get("properties", {})
    if not props:
        return json.dumps(schema)
    parts = []
    for key, val in props.items():
        vtype = val.get("type", "any") if isinstance(val, dict) else "any"
        parts.append(f"{key}: {vtype}")
    result = "{" + ", ".join(parts)
    if len(result) > 60:
        result = result[:56] + "...}"
    else:
        result += "}"
    return result


def _format_constraints(override: Any) -> str:
    """Format constraints into a readable bracket notation."""
    if override.constraints is None:
        return ""
    c = override.constraints
    min_val = c.get("min")
    max_val = c.get("max")
    if min_val is not None and max_val is not None:
        return f"[{min_val}-{max_val}]"
    if min_val is not None:
        return f"[>={min_val}]"
    if max_val is not None:
        return f"[<={max_val}]"
    return ""


def inspect_package(
    package_path: str | Path,
    output_json: bool = False,
) -> None:
    """Read a .root archive and display its contents.

    Args:
        package_path: Path to the .root archive file.
        output_json: If True, output raw manifest JSON instead of
            formatted output.
    """
    package_path = Path(package_path)

    if not package_path.exists():
        raise FileNotFoundError(f"Package not found: {package_path}")

    manifest, contents = read_archive(package_path)

    if output_json:
        print(manifest.model_dump_json(indent=2))
        return

    # Checksum verification
    checksum_ok = False
    if manifest.checksum is not None:
        process_bytes = contents.get("process.yaml")
        if process_bytes is not None:
            actual = hashlib.sha256(process_bytes).hexdigest()
            checksum_ok = actual == manifest.checksum

    # Parse process.yaml to get node/edge stats
    import yaml

    process_data = yaml.safe_load(contents["process.yaml"])
    nodes = process_data.get("nodes", [])
    edges = process_data.get("edges", [])
    entry_point = process_data.get("entry_point", "")
    node_type_counts: Counter[str] = Counter()
    for node in nodes:
        node_type_counts[node.get("type", "unknown")] += 1

    console = Console()

    # Header
    header = f"Root Package: {manifest.name} v{manifest.package_version}"
    if manifest.author:
        header += f"\nAuthor: {manifest.author}"
    if manifest.description:
        header += f"\nDescription: {manifest.description}"
    if manifest.license:
        header += f"\nLicense: {manifest.license}"
    if manifest.tags:
        header += f"\nTags: {', '.join(manifest.tags)}"

    console.print(Panel(header, title="Package Info"))

    # Process summary
    type_parts = []
    for ntype, count in sorted(node_type_counts.items()):
        type_parts.append(f"{count} {ntype}")
    type_summary = ", ".join(type_parts)

    console.print(
        f"\n[bold]Process:[/bold] {process_data.get('id', manifest.package_id)} "
        f"({len(nodes)} nodes, {len(edges)} edges)"
    )
    console.print(f"  Entry point: {entry_point}")
    console.print(f"  Node types: {type_summary}")

    # Required agents
    required = [c for c in manifest.agent_contracts if c.required]
    optional = [c for c in manifest.agent_contracts if not c.required]

    if required:
        console.print(f"\n[bold]Required Agents ({len(required)}):[/bold]")
        for agent in required:
            desc = f" — {agent.description}" if agent.description else ""
            console.print(f"  [red]✗[/red] {agent.name}{desc}")
            if agent.input_schema:
                console.print(
                    f"    Input:  {_format_schema(agent.input_schema)}"
                )
            if agent.output_schema:
                console.print(
                    f"    Output: {_format_schema(agent.output_schema)}"
                )

    if optional:
        console.print(f"\n[bold]Optional Agents ({len(optional)}):[/bold]")
        for agent in optional:
            desc = f" — {agent.description}" if agent.description else ""
            console.print(f"  [red]✗[/red] {agent.name}{desc}")
            if agent.input_schema:
                console.print(
                    f"    Input:  {_format_schema(agent.input_schema)}"
                )
            if agent.output_schema:
                console.print(
                    f"    Output: {_format_schema(agent.output_schema)}"
                )

    # Config overrides
    if manifest.config_overrides:
        console.print(
            f"\n[bold]Configurable Parameters ({len(manifest.config_overrides)}):[/bold]"
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("Path")
        table.add_column("Type")
        table.add_column("Constraints")
        table.add_column("Default")

        for override in manifest.config_overrides:
            constraints = _format_constraints(override)
            table.add_row(
                override.path,
                override.value_type,
                constraints,
                str(override.default_value),
            )

        console.print(table)

    # Config templates
    if manifest.config_templates:
        console.print(
            f"\n[bold]Configuration Templates ({len(manifest.config_templates)}):[/bold]"
        )
        for tmpl in manifest.config_templates:
            console.print(f"  [cyan]{tmpl.name}[/cyan] — {tmpl.description}")
            for path, value in tmpl.overrides.items():
                console.print(f"    {path} = {value!r}")

    # Footer info
    console.print()
    has_defaults = "Yes" if manifest.has_defaults else "No"
    if manifest.has_defaults and manifest.defaults_module:
        has_defaults += f" ({manifest.defaults_module}/)"
    console.print(f"[bold]Default Implementations:[/bold] {has_defaults}")

    has_readme = "Yes" if "README.md" in contents else "No"
    console.print(f"[bold]README:[/bold] {has_readme}")

    if manifest.checksum:
        status = (
            "[green]SHA-256 verified ✓[/green]"
            if checksum_ok
            else "[red]SHA-256 FAILED ✗[/red]"
        )
        console.print(f"[bold]Checksum:[/bold] {status}")
