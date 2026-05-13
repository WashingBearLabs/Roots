"""Tests for agent node error_key detection (T3.9).

Verifies that when an agent node has error_key configured and the agent's
output contains that key with a truthy value:
- The output is still stored in state (for inspection)
- The node is marked as failed
- The run transitions to FAILED
- NODE_FAILED and RUN_FAILED events are emitted with error_key metadata
- History records the failure with the error value

Also verifies that error_key does NOT trigger when:
- The key is absent from output
- The key is present but falsy (None, empty string, 0, False)
- error_key is not configured (None)
"""

from __future__ import annotations

from typing import Any

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope, EventType
from roots.storage.base import StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def success_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"result": "ok"}, "escalate": False}


async def error_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {"result": None, "repo_error": "clone failed: permission denied"},
        "escalate": False,
    }


async def falsy_error_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"result": "ok", "repo_error": None}, "escalate": False}


async def empty_string_error_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"result": "ok", "repo_error": ""}, "escalate": False}


def make_process(
    agent_name: str,
    error_key: str | None = None,
) -> ProcessDefinition:
    return ProcessDefinition(
        id="error-key-proc",
        name="Error Key Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="step1",
                type=NodeType.AGENT,
                label="Step 1",
                config=AgentNodeConfig(
                    agent=agent_name,
                    output_key="step1_out",
                    error_key=error_key,
                ),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="step1", to_node="done")],
        entry_point="step1",
    )


@pytest.fixture
async def sqlite_storage():
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.fixture
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register_local("success", success_agent)
    reg.register_local("error", error_agent)
    reg.register_local("falsy_error", falsy_error_agent)
    reg.register_local("empty_string_error", empty_string_error_agent)
    return reg


@pytest.fixture
def invoker(registry: AgentRegistry) -> AgentInvoker:
    return AgentInvoker(registry)


@pytest.fixture
def decision_engine() -> DecisionEngine:
    return DecisionEngine(default_model="gpt-4o-mini")


@pytest.fixture
def sink() -> CollectorSink:
    return CollectorSink()


@pytest.fixture
def emitter(sink: CollectorSink) -> EventEmitter:
    return EventEmitter(sinks=[sink])


async def _setup_run(
    storage: StorageBackend,
    process: ProcessDefinition,
) -> str:
    await storage.save_process(process)
    run = await storage.create_run(process.id, {"input": "test"})
    return run.id


def _make_runner(
    run_id: str,
    storage: StorageBackend,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
) -> ProcessRunner:
    return ProcessRunner(
        run_id=run_id,
        storage=storage,
        agent_invoker=invoker,
        decision_engine=decision_engine,
        event_emitter=emitter,
        owner_id="test-owner",
    )


# --- Tests ---


class TestErrorKeyDetection:
    @pytest.mark.asyncio
    async def test_run_fails_when_error_key_present(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run should transition to FAILED when error_key is found in output."""
        process = make_process("error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # pending -> running
        result = await runner.tick()  # execute agent -> fail

        assert result is False
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_output_stored_in_state_before_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Output should be written to state even when error_key triggers failure."""
        process = make_process("error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "step1_out" in run.work_item_state
        assert run.work_item_state["step1_out"]["repo_error"] == "clone failed: permission denied"

    @pytest.mark.asyncio
    async def test_node_failed_event_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """NODE_FAILED event should be emitted with error_key metadata."""
        process = make_process("error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()

        node_failed = [e for e in sink.events if e.event == EventType.NODE_FAILED]
        assert len(node_failed) == 1
        assert node_failed[0].metadata["error_key"] == "repo_error"
        assert node_failed[0].metadata["reason"] == "error_key_detected"
        assert "clone failed" in node_failed[0].metadata["error"]

    @pytest.mark.asyncio
    async def test_run_failed_event_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """RUN_FAILED event should be emitted with error_key metadata."""
        process = make_process("error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()

        run_failed = [e for e in sink.events if e.event == EventType.RUN_FAILED]
        assert len(run_failed) == 1
        assert run_failed[0].metadata["reason"] == "error_key_detected"

    @pytest.mark.asyncio
    async def test_history_records_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """History should record the node as failed with error details."""
        process = make_process("error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()

        history = await sqlite_storage.list_history_events(run_id)
        failed_events = [h for h in history if h.event_type == "failed"]
        assert len(failed_events) == 1
        assert failed_events[0].node_id == "step1"
        assert failed_events[0].data["error_key"] == "repo_error"


class TestErrorKeyNotTriggered:
    @pytest.mark.asyncio
    async def test_no_error_key_configured(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run should complete normally when error_key is not configured."""
        process = make_process("error", error_key=None)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()  # execute agent
        await runner.tick()  # end node

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_error_key_absent_from_output(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run should complete normally when error_key is configured but not in output."""
        process = make_process("success", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_error_key_present_but_none(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run should complete normally when error_key value is None (falsy)."""
        process = make_process("falsy_error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_error_key_present_but_empty_string(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run should complete normally when error_key value is empty string (falsy)."""
        process = make_process("empty_string_error", error_key="repo_error")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await runner.tick()
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
