"""Tests for the in-memory agent registry."""

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentRegistration, AgentType


def _dummy_callable(data: dict) -> dict:
    return {"result": "ok"}


def _another_callable(data: dict) -> dict:
    return {"result": "other"}


def _make_local_registration(name: str = "test-agent") -> AgentRegistration:
    return AgentRegistration(
        name=name,
        agent_type=AgentType.LOCAL,
        callable=_dummy_callable,
    )


class TestRegister:
    def test_register_and_get(self) -> None:
        registry = AgentRegistry()
        reg = _make_local_registration("my-agent")
        registry.register(reg)
        result = registry.get("my-agent")
        assert result is reg

    def test_get_returns_none_for_unknown(self) -> None:
        registry = AgentRegistry()
        assert registry.get("nonexistent") is None

    def test_duplicate_registration_raises(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_local_registration("dup-agent"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_local_registration("dup-agent"))

    def test_register_multiple_agents(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_local_registration("agent-a"))
        registry.register(_make_local_registration("agent-b"))
        assert registry.get("agent-a") is not None
        assert registry.get("agent-b") is not None


class TestList:
    def test_list_empty(self) -> None:
        registry = AgentRegistry()
        assert registry.list() == []

    def test_list_returns_all(self) -> None:
        registry = AgentRegistry()
        reg_a = _make_local_registration("agent-a")
        reg_b = _make_local_registration("agent-b")
        registry.register(reg_a)
        registry.register(reg_b)
        result = registry.list()
        assert len(result) == 2
        assert reg_a in result
        assert reg_b in result


class TestDeregister:
    def test_deregister_existing(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_local_registration("agent-x"))
        assert registry.deregister("agent-x") is True
        assert registry.get("agent-x") is None

    def test_deregister_nonexistent(self) -> None:
        registry = AgentRegistry()
        assert registry.deregister("ghost") is False

    def test_deregister_allows_reregistration(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_local_registration("agent-y"))
        registry.deregister("agent-y")
        registry.register(_make_local_registration("agent-y"))
        assert registry.get("agent-y") is not None


class TestRegisterLocal:
    def test_register_local_basic(self) -> None:
        registry = AgentRegistry()
        registry.register_local("local-agent", _dummy_callable)
        result = registry.get("local-agent")
        assert result is not None
        assert result.name == "local-agent"
        assert result.agent_type == AgentType.LOCAL
        assert result.callable is _dummy_callable
        assert result.input_schema is None
        assert result.output_schema is None

    def test_register_local_with_schemas(self) -> None:
        registry = AgentRegistry()
        in_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        out_schema = {"type": "object", "properties": {"y": {"type": "string"}}}
        registry.register_local(
            "schema-agent",
            _dummy_callable,
            input_schema=in_schema,
            output_schema=out_schema,
        )
        result = registry.get("schema-agent")
        assert result is not None
        assert result.input_schema == in_schema
        assert result.output_schema == out_schema

    def test_register_local_duplicate_raises(self) -> None:
        registry = AgentRegistry()
        registry.register_local("dup-local", _dummy_callable)
        with pytest.raises(ValueError, match="already registered"):
            registry.register_local("dup-local", _another_callable)
