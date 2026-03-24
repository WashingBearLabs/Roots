"""Local callable invocation for Roots agent registry."""

from __future__ import annotations

import asyncio
import inspect

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentOutput


class AgentNotFoundError(Exception):
    """Raised when an agent name is not found in the registry."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"Agent '{agent_name}' not found in registry")


class AgentInvocationError(Exception):
    """Raised when a callable fails during invocation."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        original: Exception,
    ) -> None:
        self.agent_name = agent_name
        self.original = original
        super().__init__(f"Agent '{agent_name}' invocation failed: {message}")


class AgentInvoker:
    """Invokes local Python callables registered as agents."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    async def invoke(self, agent_name: str, input: AgentInput) -> AgentOutput:
        """Invoke a registered local agent by name.

        Raises AgentNotFoundError if the agent is not registered.
        Raises AgentInvocationError if the callable raises an exception.
        """
        registration = self._registry.get(agent_name)
        if registration is None:
            raise AgentNotFoundError(agent_name)

        fn = registration.callable
        assert fn is not None, "LOCAL agent must have a callable"
        input_dict = input.model_dump()

        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(input_dict)
            else:
                result = await asyncio.to_thread(fn, input_dict)
        except Exception as exc:
            raise AgentInvocationError(
                agent_name=agent_name,
                message=str(exc),
                original=exc,
            ) from exc

        return AgentOutput(**result)
