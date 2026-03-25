"""Extract agent contracts and config overrides from a process definition."""

from __future__ import annotations

from typing import Any

from roots.agents.registry import AgentRegistry
from roots.core.schema import (
    AgentNodeConfig,
    AgentPoolNodeConfig,
    CheckpointNodeConfig,
    DecisionNodeConfig,
    ExecutionMode,
    JoinNodeConfig,
    NodeType,
    ProcessDefinition,
)
from roots.packaging.manifest import AgentContract, ConfigOverride


def extract_agent_contracts(
    process: ProcessDefinition,
    registry: AgentRegistry | None = None,
) -> list[AgentContract]:
    """Walk all nodes and extract agent contracts.

    For agent_pool nodes with first_pass mode, all agents after the first
    are marked as optional (they are fallbacks).
    """
    # Collect (agent_name, required) pairs preserving first-seen order
    seen: dict[str, bool] = {}

    for node in process.nodes:
        if node.type == NodeType.AGENT and isinstance(
            node.config, AgentNodeConfig
        ):
            name = node.config.agent
            if name not in seen:
                seen[name] = True

        elif node.type == NodeType.AGENT_POOL and isinstance(
            node.config, AgentPoolNodeConfig
        ):
            is_first_pass = (
                node.config.execution_mode == ExecutionMode.FIRST_PASS
            )
            for idx, name in enumerate(node.config.agents):
                if name not in seen:
                    if is_first_pass and idx > 0:
                        seen[name] = False
                    else:
                        seen[name] = True

    contracts: list[AgentContract] = []
    for name, required in seen.items():
        kwargs: dict[str, Any] = {"name": name, "required": required}

        if registry is not None:
            reg = registry.get(name)
            if reg is not None:
                kwargs["input_schema"] = reg.input_schema
                kwargs["output_schema"] = reg.output_schema
                kwargs["timeout_seconds"] = reg.timeout_seconds
                if reg.metadata and "description" in reg.metadata:
                    kwargs["description"] = reg.metadata["description"]

        contracts.append(AgentContract(**kwargs))

    return sorted(contracts, key=lambda c: c.name)


def extract_config_overrides(
    process: ProcessDefinition,
) -> list[ConfigOverride]:
    """Walk all nodes and extract commonly-tunable parameters."""
    overrides: list[ConfigOverride] = []

    for node in process.nodes:
        prefix = f"nodes.{node.id}.config"

        if node.type == NodeType.DECISION and isinstance(
            node.config, DecisionNodeConfig
        ):
            if node.config.confidence_threshold is not None:
                overrides.append(
                    ConfigOverride(
                        path=f"{prefix}.confidence_threshold",
                        description=f"Confidence threshold for decision '{node.id}'",
                        default_value=node.config.confidence_threshold,
                        value_type="float",
                        constraints={"min": 0.0, "max": 1.0},
                    )
                )
            if node.config.model is not None:
                overrides.append(
                    ConfigOverride(
                        path=f"{prefix}.model",
                        description=f"Model for decision '{node.id}'",
                        default_value=node.config.model,
                        value_type="string",
                    )
                )
            if node.config.context_prompt is not None:
                overrides.append(
                    ConfigOverride(
                        path=f"{prefix}.context_prompt",
                        description=f"Context prompt for decision '{node.id}'",
                        default_value=node.config.context_prompt,
                        value_type="string",
                    )
                )

        if node.retry is not None:
            retry_prefix = f"nodes.{node.id}.config.retry"
            overrides.append(
                ConfigOverride(
                    path=f"{retry_prefix}.max_attempts",
                    description=f"Max retry attempts for '{node.id}'",
                    default_value=node.retry.max_attempts,
                    value_type="int",
                    constraints={"min": 1},
                )
            )
            overrides.append(
                ConfigOverride(
                    path=f"{retry_prefix}.backoff_seconds",
                    description=f"Retry backoff seconds for '{node.id}'",
                    default_value=node.retry.backoff_seconds,
                    value_type="float",
                    constraints={"min": 0.0},
                )
            )

        if node.type == NodeType.CHECKPOINT and isinstance(
            node.config, CheckpointNodeConfig
        ):
            overrides.append(
                ConfigOverride(
                    path=f"{prefix}.prompt",
                    description=f"Checkpoint prompt for '{node.id}'",
                    default_value=node.config.prompt,
                    value_type="string",
                )
            )

        if node.type == NodeType.JOIN and isinstance(
            node.config, JoinNodeConfig
        ):
            overrides.append(
                ConfigOverride(
                    path=f"{prefix}.allow_partial",
                    description=f"Allow partial results for join '{node.id}'",
                    default_value=node.config.allow_partial,
                    value_type="bool",
                )
            )

    return overrides
