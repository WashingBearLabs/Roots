"""Tests for agent input/output schema validation (US-005)."""

import pytest

from roots.agents.invoker import (
    AgentInvoker,
    AgentSchemaValidationError,
)
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput


_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {"type": "string"},
    },
    "required": ["result"],
}


def _make_input(**overrides: object) -> AgentInput:
    defaults = {
        "work_item_state": {"name": "Alice", "age": 30},
        "node_config": {"step": 1},
        "run_id": "run-001",
    }
    defaults.update(overrides)
    return AgentInput(**defaults)


def _echo_agent(data: dict) -> dict:
    return {"output": data["work_item_state"]}


def _fixed_output_agent(output: dict):
    def _agent(data: dict) -> dict:
        return {"output": output}
    return _agent


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_valid_input_passes_validation(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent", _echo_agent, input_schema=_INPUT_SCHEMA
        )
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("agent", _make_input())
        assert result.output == {"name": "Alice", "age": 30}

    @pytest.mark.asyncio
    async def test_invalid_input_raises_schema_error(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent", _echo_agent, input_schema=_INPUT_SCHEMA
        )
        invoker = AgentInvoker(registry)
        bad_input = _make_input(work_item_state={"age": "not-an-int"})

        with pytest.raises(AgentSchemaValidationError) as exc_info:
            await invoker.invoke("agent", bad_input)

        err = exc_info.value
        assert err.agent_name == "agent"
        assert err.direction == "input"
        assert len(err.validation_errors) > 0

    @pytest.mark.asyncio
    async def test_invalid_input_blocks_invocation(self) -> None:
        """The callable should never be called when input validation fails."""
        call_count = 0

        def _counting_agent(data: dict) -> dict:
            nonlocal call_count
            call_count += 1
            return {"output": {"ok": True}}

        registry = AgentRegistry()
        registry.register_local(
            "agent", _counting_agent, input_schema=_INPUT_SCHEMA
        )
        invoker = AgentInvoker(registry)
        bad_input = _make_input(work_item_state={"age": "not-an-int"})

        with pytest.raises(AgentSchemaValidationError):
            await invoker.invoke("agent", bad_input)

        assert call_count == 0

    @pytest.mark.asyncio
    async def test_input_error_includes_field_details(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent", _echo_agent, input_schema=_INPUT_SCHEMA
        )
        invoker = AgentInvoker(registry)
        bad_input = _make_input(work_item_state={"name": 123})

        with pytest.raises(AgentSchemaValidationError) as exc_info:
            await invoker.invoke("agent", bad_input)

        errors = exc_info.value.validation_errors
        assert any("name" in str(e.get("path", [])) for e in errors)


class TestOutputValidation:
    @pytest.mark.asyncio
    async def test_valid_output_passes_validation(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent",
            _fixed_output_agent({"result": "ok"}),
            output_schema=_OUTPUT_SCHEMA,
        )
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("agent", _make_input())
        assert result.output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_invalid_output_raises_schema_error(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent",
            _fixed_output_agent({"wrong_field": 42}),
            output_schema=_OUTPUT_SCHEMA,
        )
        invoker = AgentInvoker(registry)

        with pytest.raises(AgentSchemaValidationError) as exc_info:
            await invoker.invoke("agent", _make_input())

        err = exc_info.value
        assert err.agent_name == "agent"
        assert err.direction == "output"
        assert len(err.validation_errors) > 0

    @pytest.mark.asyncio
    async def test_output_error_is_post_invocation(self) -> None:
        """Output validation happens after the callable runs."""
        call_count = 0

        def _counting_agent(data: dict) -> dict:
            nonlocal call_count
            call_count += 1
            return {"output": {"wrong_field": 42}}

        registry = AgentRegistry()
        registry.register_local(
            "agent", _counting_agent, output_schema=_OUTPUT_SCHEMA
        )
        invoker = AgentInvoker(registry)

        with pytest.raises(AgentSchemaValidationError):
            await invoker.invoke("agent", _make_input())

        assert call_count == 1


class TestNoSchemaPermissive:
    @pytest.mark.asyncio
    async def test_no_schemas_allows_any_data(self) -> None:
        registry = AgentRegistry()
        registry.register_local("agent", _echo_agent)
        invoker = AgentInvoker(registry)
        result = await invoker.invoke(
            "agent",
            _make_input(work_item_state={"anything": [1, 2, 3]}),
        )
        assert result.output == {"anything": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_no_input_schema_skips_input_validation(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent",
            _fixed_output_agent({"result": "ok"}),
            output_schema=_OUTPUT_SCHEMA,
        )
        invoker = AgentInvoker(registry)
        # Any input should pass since no input_schema is set
        result = await invoker.invoke(
            "agent",
            _make_input(work_item_state={"random": True}),
        )
        assert result.output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_no_output_schema_skips_output_validation(self) -> None:
        registry = AgentRegistry()
        registry.register_local(
            "agent",
            _fixed_output_agent({"literally": "anything"}),
            input_schema=_INPUT_SCHEMA,
        )
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("agent", _make_input())
        assert result.output == {"literally": "anything"}


class TestSchemaValidationErrorInheritance:
    def test_inherits_from_agent_invocation_error(self) -> None:
        from roots.agents.invoker import AgentInvocationError

        err = AgentSchemaValidationError(
            agent_name="test",
            direction="input",
            validation_errors=[{"message": "bad"}],
        )
        assert isinstance(err, AgentInvocationError)
        assert err.agent_name == "test"
        assert err.direction == "input"
        assert err.validation_errors == [{"message": "bad"}]
