"""Tests for agent registration data models."""

import pytest

from roots.agents.types import (
    AgentInput,
    AgentOutput,
    AgentRegistration,
    AgentType,
)


def _dummy_callable(data: dict) -> dict:
    return {"result": "ok"}


class TestAgentType:
    def test_local_value(self) -> None:
        assert AgentType.LOCAL == "local"

    def test_remote_value(self) -> None:
        assert AgentType.REMOTE == "remote"


class TestAgentRegistrationValid:
    def test_local_agent_with_callable(self) -> None:
        reg = AgentRegistration(
            name="my-agent",
            agent_type=AgentType.LOCAL,
            callable=_dummy_callable,
        )
        assert reg.name == "my-agent"
        assert reg.agent_type == AgentType.LOCAL
        assert reg.callable is _dummy_callable
        assert reg.timeout_seconds == 300

    def test_remote_agent_with_callback_url(self) -> None:
        reg = AgentRegistration(
            name="remote-agent",
            agent_type=AgentType.REMOTE,
            callback_url="https://example.com/agent",
        )
        assert reg.name == "remote-agent"
        assert reg.agent_type == AgentType.REMOTE
        assert reg.callback_url == "https://example.com/agent"

    def test_local_agent_with_all_fields(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        reg = AgentRegistration(
            name="full-agent",
            agent_type=AgentType.LOCAL,
            callable=_dummy_callable,
            input_schema=schema,
            output_schema=schema,
            timeout_seconds=60,
            metadata={"version": "1.0"},
        )
        assert reg.input_schema == schema
        assert reg.output_schema == schema
        assert reg.timeout_seconds == 60
        assert reg.metadata == {"version": "1.0"}

    def test_remote_agent_with_schemas(self) -> None:
        reg = AgentRegistration(
            name="remote-with-schema",
            agent_type=AgentType.REMOTE,
            callback_url="https://example.com/agent",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert reg.input_schema is not None
        assert reg.output_schema is not None


class TestAgentRegistrationInvalid:
    def test_local_agent_without_callable_raises(self) -> None:
        with pytest.raises(ValueError, match="callable is required"):
            AgentRegistration(
                name="bad-local",
                agent_type=AgentType.LOCAL,
            )

    def test_remote_agent_without_callback_url_raises(self) -> None:
        with pytest.raises(ValueError, match="callback_url is required"):
            AgentRegistration(
                name="bad-remote",
                agent_type=AgentType.REMOTE,
            )

    def test_local_agent_with_callback_url_no_callable_raises(self) -> None:
        with pytest.raises(ValueError, match="callable is required"):
            AgentRegistration(
                name="bad-local",
                agent_type=AgentType.LOCAL,
                callback_url="https://example.com/agent",
            )

    def test_remote_agent_with_callable_no_url_raises(self) -> None:
        with pytest.raises(ValueError, match="callback_url is required"):
            AgentRegistration(
                name="bad-remote",
                agent_type=AgentType.REMOTE,
                callable=_dummy_callable,
            )


class TestAgentInput:
    def test_creation(self) -> None:
        inp = AgentInput(
            work_item_state={"key": "value"},
            node_config={"agent": "my-agent"},
            run_id="run-123",
        )
        assert inp.work_item_state == {"key": "value"}
        assert inp.node_config == {"agent": "my-agent"}
        assert inp.run_id == "run-123"

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValueError):
            AgentInput(
                work_item_state={"key": "value"},
                node_config={"agent": "my-agent"},
            )  # type: ignore[call-arg]


class TestAgentOutput:
    def test_defaults(self) -> None:
        out = AgentOutput(output={"result": "done"})
        assert out.output == {"result": "done"}
        assert out.escalate is False
        assert out.escalation_reason is None

    def test_escalation(self) -> None:
        out = AgentOutput(
            output={"result": "issue"},
            escalate=True,
            escalation_reason="needs human review",
        )
        assert out.escalate is True
        assert out.escalation_reason == "needs human review"
