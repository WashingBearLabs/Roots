"""In-memory agent registry for Roots framework."""

from __future__ import annotations

from typing import Any, Callable

from roots.agents.types import AgentRegistration, AgentType


class AgentRegistry:
    """Registry for managing agent registrations at runtime."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRegistration] = {}

    def register(self, registration: AgentRegistration) -> None:
        """Register an agent. Raises ValueError if name already registered."""
        if registration.name in self._agents:
            raise ValueError(
                f"Agent '{registration.name}' is already registered"
            )
        self._agents[registration.name] = registration

    def get(self, name: str) -> AgentRegistration | None:
        """Look up an agent by name. Returns None if not found."""
        return self._agents.get(name)

    def list(self) -> list[AgentRegistration]:
        """Return all registered agents."""
        return list(self._agents.values())

    def deregister(self, name: str) -> bool:
        """Remove an agent. Returns True if removed, False if not found."""
        if name in self._agents:
            del self._agents[name]
            return True
        return False

    def register_local(
        self,
        name: str,
        callable: Callable[..., Any],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Convenience method for registering a local agent."""
        registration = AgentRegistration(
            name=name,
            agent_type=AgentType.LOCAL,
            callable=callable,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        self.register(registration)
