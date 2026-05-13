"""Core schema definitions for Roots process orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

if TYPE_CHECKING:
    from typing import Self


class NodeType(StrEnum):
    AGENT = "agent"
    AGENT_POOL = "agent_pool"
    DECISION = "decision"
    CHECKPOINT = "checkpoint"
    FORK = "fork"
    JOIN = "join"
    EMIT = "emit"
    END = "end"


class BackoffStrategy(StrEnum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class OnExhaustion(StrEnum):
    FAIL = "fail"
    ROUTE = "route"


class RetryConfig(BaseModel):
    max_attempts: int = 1
    backoff: BackoffStrategy = BackoffStrategy.FIXED
    backoff_seconds: float = 5.0
    on_exhaustion: OnExhaustion = OnExhaustion.FAIL
    fallback_edge: str | None = None

    @model_validator(mode="after")
    def validate_fallback_edge(self) -> Self:
        if self.on_exhaustion == OnExhaustion.ROUTE and self.fallback_edge is None:
            raise ValueError(
                "fallback_edge is required when on_exhaustion is 'route'"
            )
        return self


class ExecutionMode(StrEnum):
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    FIRST_PASS = "first_pass"


class Aggregation(StrEnum):
    MERGE_ALL = "merge_all"
    MAJORITY_VOTE = "majority_vote"
    WEIGHTED_VOTE = "weighted_vote"
    UNANIMOUS = "unanimous"


class TieBreak(StrEnum):
    FIRST_AGENT = "first_agent"
    REJECT = "reject"


class DecisionMode(StrEnum):
    DETERMINISTIC = "deterministic"
    AI_BOUNDED = "ai_bounded"
    AI_CHECKPOINT = "ai_checkpoint"
    AI_AUTONOMOUS = "ai_autonomous"


class MergeStrategy(StrEnum):
    MERGE_ALL = "merge_all"
    COLLECT = "collect"


class EndStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


_VOTE_AGGREGATIONS = {
    Aggregation.MAJORITY_VOTE,
    Aggregation.WEIGHTED_VOTE,
    Aggregation.UNANIMOUS,
}


class AgentNodeConfig(BaseModel):
    agent: str
    output_key: str
    error_key: str | None = None


class VoteConfig(BaseModel):
    vote_key: str
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    weights: dict[str, float] | None = None
    tie_break: TieBreak = TieBreak.FIRST_AGENT


class AgentPoolNodeConfig(BaseModel):
    agents: list[str] = Field(min_length=1)
    execution_mode: ExecutionMode
    aggregation: Aggregation = Aggregation.MERGE_ALL
    output_key: str
    vote_config: VoteConfig | None = None

    @model_validator(mode="after")
    def validate_vote_config(self) -> Self:
        is_vote = self.aggregation in _VOTE_AGGREGATIONS
        if is_vote and self.vote_config is None:
            raise ValueError(
                "vote_config is required when aggregation is a vote type"
            )
        if not is_vote and self.vote_config is not None:
            raise ValueError(
                "vote_config is not allowed when aggregation is 'merge_all'"
            )
        if is_vote and self.execution_mode == ExecutionMode.FIRST_PASS:
            raise ValueError(
                "vote aggregation is not compatible with execution_mode 'first_pass'"
            )
        if self.vote_config is not None:
            if self.vote_config.weights is not None:
                invalid_keys = set(self.vote_config.weights) - set(self.agents)
                if invalid_keys:
                    raise ValueError(
                        f"vote_config weight keys {invalid_keys!r} are not in the agents list"
                    )
            if (
                self.aggregation == Aggregation.WEIGHTED_VOTE
                and self.vote_config.weights is None
            ):
                raise ValueError(
                    "weights are required when aggregation is 'weighted_vote'"
                )
        return self


class DecisionEdge(BaseModel):
    target: str
    condition: str | None = None
    label: str | None = None
    description: str | None = None


class DecisionNodeConfig(BaseModel):
    mode: DecisionMode
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    model: str | None = None
    context_prompt: str | None = None
    checkpoint_prompt: str | None = None
    edges: list[DecisionEdge] = Field(min_length=1)
    history_depth: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_mode_constraints(self) -> Self:
        if self.mode == DecisionMode.DETERMINISTIC:
            for edge in self.edges:
                if not edge.condition:
                    raise ValueError(
                        "all edges must have a non-empty condition "
                        "when mode is 'deterministic'"
                    )
        else:
            if self.confidence_threshold is None:
                raise ValueError(
                    "confidence_threshold is required for AI decision modes"
                )
        return self


class CheckpointNodeConfig(BaseModel):
    prompt: str


class ForkNodeConfig(BaseModel):
    pass


class JoinNodeConfig(BaseModel):
    merge_strategy: MergeStrategy = MergeStrategy.MERGE_ALL
    collect_key: str | None = None
    allow_partial: bool = False

    @model_validator(mode="after")
    def validate_collect_key(self) -> Self:
        if (
            self.merge_strategy == MergeStrategy.COLLECT
            and self.collect_key is None
        ):
            raise ValueError(
                "collect_key is required when merge_strategy is 'collect'"
            )
        return self


class EmitNodeConfig(BaseModel):
    event_type: str
    payload_keys: list[str] = Field(default_factory=list)


class EndNodeConfig(BaseModel):
    status: EndStatus


CONFIG_MAP: dict[NodeType, type[BaseModel]] = {
    NodeType.AGENT: AgentNodeConfig,
    NodeType.AGENT_POOL: AgentPoolNodeConfig,
    NodeType.DECISION: DecisionNodeConfig,
    NodeType.CHECKPOINT: CheckpointNodeConfig,
    NodeType.FORK: ForkNodeConfig,
    NodeType.JOIN: JoinNodeConfig,
    NodeType.EMIT: EmitNodeConfig,
    NodeType.END: EndNodeConfig,
}


class NodeDefinition(BaseModel):
    id: str
    type: NodeType
    label: str
    config: dict[str, Any] | BaseModel
    metadata: dict[str, Any] | None = None
    retry: RetryConfig | None = None

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if isinstance(self.config, dict):
            config_cls = CONFIG_MAP[self.type]
            try:
                self.config = config_cls.model_validate(self.config)
            except Exception as exc:
                raise ValueError(
                    f"Node '{self.id}' ({self.type}): {exc}"
                ) from exc
        if self.retry is not None and self.type not in (
            NodeType.AGENT,
            NodeType.AGENT_POOL,
        ):
            raise ValueError(
                "retry config is only valid on agent and agent_pool nodes"
            )
        if self.metadata is None:
            self.metadata = {}
        return self


class EdgeDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    label: str | None = None
    condition: str | None = None
    emit_event: bool = False


class ProcessDefinition(BaseModel):
    id: str
    name: str
    version: str
    description: str | None = None
    work_item_schema: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    nodes: list[NodeDefinition]
    edges: list[EdgeDefinition]
    entry_point: str
    fork_join_map: dict[str, str] = Field(default_factory=dict)

    _node_map: dict[str, NodeDefinition] = PrivateAttr(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    @model_validator(mode="after")
    def validate_process(self) -> Self:
        self._node_map = {node.id: node for node in self.nodes}

        if self.entry_point not in self._node_map:
            raise ValueError(
                f"entry_point '{self.entry_point}' does not reference "
                f"an existing node"
            )

        for edge in self.edges:
            if edge.from_node not in self._node_map:
                raise ValueError(
                    f"edge '{edge.id}' references unknown from node "
                    f"'{edge.from_node}'"
                )
            if edge.to_node not in self._node_map:
                raise ValueError(
                    f"edge '{edge.id}' references unknown to node "
                    f"'{edge.to_node}'"
                )

        return self

    def get_node(self, node_id: str) -> NodeDefinition | None:
        return self._node_map.get(node_id)

    def recompute_fork_join_map(self) -> None:
        """Recompute fork→join mapping via structural validation."""
        from roots.core.validator import validate_structure

        validate_structure(self)

    def get_outbound_edges(
        self, node_id: str
    ) -> list[EdgeDefinition | DecisionEdge]:
        node = self._node_map.get(node_id)
        if node is None:
            return []
        if (
            node.type == NodeType.DECISION
            and isinstance(node.config, DecisionNodeConfig)
        ):
            return list(node.config.edges)
        return [e for e in self.edges if e.from_node == node_id]
