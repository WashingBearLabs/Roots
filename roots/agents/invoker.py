"""Agent invocation for Roots agent registry (local and remote)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import httpx
import jsonschema

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentOutput, AgentRegistration, AgentType


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


class AgentSchemaValidationError(AgentInvocationError):
    """Raised when agent input or output fails schema validation."""

    def __init__(
        self,
        agent_name: str,
        direction: str,
        validation_errors: list[dict[str, Any]],
    ) -> None:
        self.direction = direction
        self.validation_errors = validation_errors
        error_summary = "; ".join(
            e.get("message", str(e)) for e in validation_errors
        )
        super().__init__(
            agent_name=agent_name,
            message=f"{direction} schema validation failed: {error_summary}",
            original=ValueError(error_summary),
        )


class AgentInvoker:
    """Invokes local and remote agents registered in the registry."""

    def __init__(
        self,
        registry: AgentRegistry,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._registry = registry
        self._http_client = http_client or httpx.AsyncClient()

    async def invoke(self, agent_name: str, input: AgentInput) -> AgentOutput:
        """Invoke a registered agent by name.

        Raises AgentNotFoundError if the agent is not registered.
        Raises AgentSchemaValidationError if input/output fails schema validation.
        Raises AgentInvocationError if invocation fails.
        """
        registration = self._registry.get(agent_name)
        if registration is None:
            raise AgentNotFoundError(agent_name)

        if registration.input_schema is not None:
            self._validate_schema(
                agent_name, "input", input.work_item_state, registration.input_schema
            )

        if registration.agent_type == AgentType.REMOTE:
            result = await self._invoke_remote(agent_name, input)
        else:
            result = await self._invoke_local(agent_name, input)

        if registration.output_schema is not None:
            self._validate_schema(
                agent_name, "output", result.output, registration.output_schema
            )

        return result

    @staticmethod
    def _validate_schema(
        agent_name: str,
        direction: str,
        instance: dict[str, Any],
        schema: dict[str, Any],
    ) -> None:
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(instance))
        if errors:
            validation_errors = [
                {
                    "path": list(e.absolute_path),
                    "message": e.message,
                    "validator": e.validator,
                }
                for e in errors
            ]
            raise AgentSchemaValidationError(
                agent_name=agent_name,
                direction=direction,
                validation_errors=validation_errors,
            )

    async def _invoke_local(
        self, agent_name: str, input: AgentInput
    ) -> AgentOutput:
        registration = self._registry.get(agent_name)
        assert registration is not None
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

    async def _invoke_remote(
        self, agent_name: str, input: AgentInput
    ) -> AgentOutput:
        registration = self._registry.get(agent_name)
        assert registration is not None
        assert registration.callback_url is not None, (
            "REMOTE agent must have a callback_url"
        )

        try:
            response = await self._http_client.post(
                registration.callback_url,
                json=input.model_dump(mode="json"),
                timeout=registration.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AgentInvocationError(
                agent_name=agent_name,
                message=(
                    f"Request timed out after {registration.timeout_seconds}s"
                ),
                original=exc,
            ) from exc
        except httpx.ConnectError as exc:
            raise AgentInvocationError(
                agent_name=agent_name,
                message=f"Connection failed: {exc}",
                original=exc,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AgentInvocationError(
                agent_name=agent_name,
                message=(
                    f"HTTP {exc.response.status_code}: "
                    f"{exc.response.text}"
                ),
                original=exc,
            ) from exc

        return AgentOutput(**response.json())
