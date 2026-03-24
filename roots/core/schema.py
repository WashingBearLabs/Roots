"""Core schema definitions for Roots process orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

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


class DecisionMode(StrEnum):
    DETERMINISTIC = "deterministic"
    AI_BOUNDED = "ai_bounded"
    AI_CHECKPOINT = "ai_checkpoint"
    AI_AUTONOMOUS = "ai_autonomous"


class AgentNodeConfig(BaseModel):
    agent: str
    output_key: str


class AgentPoolNodeConfig(BaseModel):
    agents: list[str] = Field(min_length=1)
    execution_mode: ExecutionMode
    aggregation: Aggregation = Aggregation.MERGE_ALL
    output_key: str


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


class NodeDefinition(BaseModel):
    id: str
    type: NodeType
    label: str
    config: dict[str, Any]
    metadata: dict[str, Any] | None = None
    retry: RetryConfig | None = None

    @model_validator(mode="after")
    def validate_retry(self) -> Self:
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
