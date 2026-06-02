"""YAML parsing pipeline for Roots process definitions."""

from __future__ import annotations

import warnings
from collections import deque
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from roots.core.schema import (
    DecisionNodeConfig,
    NodeType,
    OnExhaustion,
    ProcessDefinition,
)


class ProcessValidationError(Exception):
    """Raised when structural validation of a process definition fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            f"Process validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def _validate_fork_join_pairing(
    process: ProcessDefinition,
    adjacency: dict[str, list[str]],
) -> tuple[list[str], dict[str, str]]:
    """Validate fork/join pairing and return errors + fork→join mapping."""
    errors: list[str] = []
    fork_join_map: dict[str, str] = {}
    node_type_map = {node.id: node.type for node in process.nodes}

    for node in process.nodes:
        if node.type != NodeType.FORK:
            continue

        branches = adjacency.get(node.id, [])
        if len(branches) < 2:
            errors.append(
                f"Fork node '{node.id}' has no outbound edges "
                f"— need at least 2 branches"
            )
            continue

        join_targets: set[str] = set()
        for branch_start in branches:
            # BFS from branch_start until hitting a join or dead end
            visited: set[str] = set()
            queue: deque[str] = deque([branch_start])
            found_join: str | None = None

            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)

                current_type = node_type_map.get(current)
                if current_type == NodeType.JOIN:
                    found_join = current
                    break
                if current_type == NodeType.END:
                    errors.append(
                        f"Fork node '{node.id}': branch starting at "
                        f"'{branch_start}' reaches end node without "
                        f"passing through a join"
                    )
                    found_join = None
                    break
                if current_type == NodeType.FORK:
                    errors.append(
                        f"Fork node '{node.id}': branch starting at "
                        f"'{branch_start}' contains nested fork node '{current}' "
                        f"— nested fork/join is not supported"
                    )
                    found_join = None
                    break
                for neighbor in adjacency.get(current, []):
                    if neighbor not in visited:
                        queue.append(neighbor)

            if found_join is None and not any(
                node_type_map.get(v) in (NodeType.JOIN, NodeType.END, NodeType.FORK)
                for v in visited
            ):
                errors.append(
                    f"Fork node '{node.id}': branch starting at "
                    f"'{branch_start}' has no path to a join node"
                )
            elif found_join is not None:
                join_targets.add(found_join)

        if len(join_targets) > 1:
            sorted_joins = sorted(join_targets)
            errors.append(
                f"Fork node '{node.id}': branches converge at different "
                f"join nodes ('{sorted_joins[0]}', '{sorted_joins[1]}')"
            )
        elif len(join_targets) == 1:
            fork_join_map[node.id] = next(iter(join_targets))

    return errors, fork_join_map


def validate_structure(process: ProcessDefinition) -> list[str]:
    """Validate graph-level structural rules on a ProcessDefinition.

    Returns a list of error strings. Warnings (e.g. unreachable nodes)
    are emitted via the warnings module, not included in the error list.
    """
    errors: list[str] = []

    node_ids = {node.id for node in process.nodes}
    from_nodes_in_edges = {edge.from_node for edge in process.edges}

    # Decision edge exclusivity
    for node in process.nodes:
        if node.type == NodeType.DECISION and node.id in from_nodes_in_edges:
            errors.append(
                f"Decision node '{node.id}' must not have top-level "
                f"outbound edges \u2014 define edges in the node's config block"
            )

    # Edge completeness: non-end, non-decision, non-join nodes need outbound edges
    exempt_types = {NodeType.END, NodeType.DECISION, NodeType.JOIN}
    nodes_with_outbound = {edge.from_node for edge in process.edges}
    for node in process.nodes:
        if node.type not in exempt_types and node.id not in nodes_with_outbound:
            errors.append(
                f"Node '{node.id}' ({node.type}) has no outbound edges"
            )

    # End node existence
    if not any(node.type == NodeType.END for node in process.nodes):
        errors.append("Process has no end node")

    # Reachability via BFS from entry_point
    visited: set[str] = set()
    queue: deque[str] = deque([process.entry_point])
    # Build adjacency from top-level edges + decision config edges
    adjacency: dict[str, list[str]] = {node.id: [] for node in process.nodes}
    for edge in process.edges:
        adjacency[edge.from_node].append(edge.to_node)
    for node in process.nodes:
        if node.type == NodeType.DECISION:
            if isinstance(node.config, DecisionNodeConfig):
                for dedge in node.config.edges:
                    adjacency[node.id].append(dedge.target)

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adjacency.get(current, []):
            if neighbor not in visited:
                queue.append(neighbor)

    for node in process.nodes:
        if node.id not in visited:
            warnings.warn(
                f"Node '{node.id}' is unreachable from entry point",
                stacklevel=2,
            )

    # Fork/join pairing
    fork_join_errors, fork_join_map = _validate_fork_join_pairing(
        process, adjacency
    )
    errors.extend(fork_join_errors)
    if not fork_join_errors:
        process.fork_join_map = fork_join_map

    # Unpaired join nodes
    paired_joins = set(fork_join_map.values())
    for node in process.nodes:
        if node.type == NodeType.JOIN and node.id not in paired_joins:
            errors.append(
                f"Join node '{node.id}' is not paired with any fork node"
            )

    # Fallback edge validity
    for node in process.nodes:
        if node.retry is not None:
            if (
                node.retry.on_exhaustion == OnExhaustion.ROUTE
                and node.retry.fallback_edge is not None
                and node.retry.fallback_edge not in node_ids
            ):
                errors.append(
                    f"Node '{node.id}': fallback_edge "
                    f"'{node.retry.fallback_edge}' does not reference "
                    f"a valid node"
                )

    return errors


def format_validation_errors(
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
    process = ProcessDefinition.model_validate(data)
    process.recompute_fork_join_map()
    return process


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
    process = parse_process_dict(cast(dict[str, Any], data))
    struct_errors = validate_structure(process)
    if struct_errors:
        raise ProcessValidationError(struct_errors)
    return process


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
        process = parse_process_dict(data_dict)
    except ValidationError as exc:
        return format_validation_errors(exc, raw_data=data_dict)

    return validate_structure(process)
