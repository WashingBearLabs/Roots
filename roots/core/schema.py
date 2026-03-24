"""Core schema definitions for Roots process orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, model_validator

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
