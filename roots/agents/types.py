"""Agent registration data models for Roots agent registry."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from typing import Self


class AgentType(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class AgentRegistration(BaseModel):
    name: str
    agent_type: AgentType
    callable: Callable[..., Any] | None = None
    callback_url: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    timeout_seconds: int = 300
    metadata: dict[str, Any] | None = None

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def validate_type_fields(self) -> Self:
        if self.agent_type == AgentType.LOCAL and self.callable is None:
            raise ValueError(
                "callable is required when agent_type is 'local'"
            )
        if self.agent_type == AgentType.REMOTE and self.callback_url is None:
            raise ValueError(
                "callback_url is required when agent_type is 'remote'"
            )
        return self


class AgentInput(BaseModel):
    work_item_state: dict[str, Any]
    node_config: dict[str, Any]
    run_id: str


class AgentOutput(BaseModel):
    output: dict[str, Any]
    escalate: bool = False
    escalation_reason: str | None = None
