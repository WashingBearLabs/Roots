"""Tests for agent and agent_pool node handlers (US-003)."""

from __future__ import annotations

from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import OrchestrationError, ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    AgentPoolNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope
from roots.storage.base import StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


async def upper_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Returns uppercased version of 'text' key."""
    text = input["work_item_state"].get("text", "")
    return {"output": {"upper": text.upper()}, "escalate": False}


async def reverse_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Returns reversed version of 'text' key."""
    text = input["work_item_state"].get("text", "")
    return {"output": {"reverse": text[::-1]}, "escalate": False}


async def escalating_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {"status": "needs_review"},
        "escalate": True,
        "escalation_reason": "Agent needs human review",
    }


async def failing_agent(input: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("Agent exploded")


async def counter_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Increments a counter in state, useful for sequential chaining tests."""
    count = input["work_item_state"].get("count", 0)
    return {"output": {"count": count + 1}, "escalate": False}


def make_agent_process(agent_name: str = "echo") -> ProcessDefinition:
    """Single agent → end."""
    return ProcessDefinition(
        id="agent-proc",
        name="Agent Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="agent1",
                type=NodeType.AGENT,
                label="Agent 1",
                config=AgentNodeConfig(agent=agent_name, output_key="result"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="agent1", to_node="done")],
        entry_point="agent1",
    )


def make_pool_process(
    agents: list[str],
    execution_mode: ExecutionMode,
) -> ProcessDefinition:
    """Agent pool → end."""
    return ProcessDefinition(
        id="pool-proc",
        name="Pool Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="pool1",
                type=NodeType.AGENT_POOL,
                label="Pool 1",
                config=AgentPoolNodeConfig(
                    agents=agents,
                    execution_mode=execution_mode,
                    output_key="pool_result",
                ),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="pool1", to_node="done")],
        entry_point="pool1",
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
    reg.register_local("echo", echo_agent)
    reg.register_local("upper", upper_agent)
    reg.register_local("reverse", reverse_agent)
    reg.register_local("escalating", escalating_agent)
    reg.register_local("failing", failing_agent)
    reg.register_local("counter", counter_agent)
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
    state: dict[str, Any] | None = None,
) -> str:
    await storage.save_process(process)
    run = await storage.create_run(process.id, state or {"input": "hello"})
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


# --- Single Agent Handler Tests ---


class TestAgentHandler:
    async def test_agent_invokes_and_returns_output(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_agent_process("echo")
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert "result" in run.work_item_state
        assert run.work_item_state["result"]["echo"]["input"] == "hello"

    async def test_agent_events_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        proc = make_agent_process("echo")
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await emitter.close()

        invoked = [e for e in sink.events if e.event == "roots.agent.invoked"]
        returned = [e for e in sink.events if e.event == "roots.agent.returned"]

        assert len(invoked) == 1
        assert len(returned) == 1
        assert invoked[0].metadata["agent"] == "echo"
        assert returned[0].metadata["agent"] == "echo"
        assert invoked[0].node_id == "agent1"

    async def test_agent_escalation_pauses_run(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_agent_process("escalating")
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "paused"

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "agent_explicit_signal"
        assert esc.reason == "Agent needs human review"


# --- Agent Pool Tests ---


class TestAgentPoolParallel:
    async def test_parallel_invokes_all_and_merges(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(["upper", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        pool_result = run.work_item_state["pool_result"]
        assert pool_result["upper"] == "HELLO"
        assert pool_result["reverse"] == "olleh"

    async def test_parallel_all_fail_raises(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(["failing", "failing"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="All 2 agents failed"):
            await runner.tick()

    async def test_parallel_partial_failure_succeeds(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(["upper", "failing"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.work_item_state["pool_result"]["upper"] == "HELLO"

    async def test_parallel_events_emitted_for_each_agent(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(["upper", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hi"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        await emitter.close()

        invoked = [e for e in sink.events if e.event == "roots.agent.invoked"]
        returned = [e for e in sink.events if e.event == "roots.agent.returned"]

        assert len(invoked) == 2
        assert len(returned) == 2
        agent_names = {e.metadata["agent"] for e in invoked}
        assert agent_names == {"upper", "reverse"}


class TestAgentPoolSequential:
    async def test_sequential_chains_outputs(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(
            ["counter", "counter"], ExecutionMode.SEQUENTIAL
        )
        run_id = await _setup_run(sqlite_storage, proc, {"count": 0})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        # First counter: 0→1, second counter sees count=1→2
        assert run.work_item_state["pool_result"]["count"] == 2

    async def test_sequential_escalation(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(
            ["echo", "escalating"], ExecutionMode.SEQUENTIAL
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "paused"

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "agent_explicit_signal"


class TestAgentPoolFirstPass:
    async def test_first_pass_returns_first_success(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(
            ["failing", "upper", "reverse"], ExecutionMode.FIRST_PASS
        )
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        # failing skipped, upper is first success
        assert run.work_item_state["pool_result"]["upper"] == "HELLO"
        # reverse should NOT be in output (stopped at first success)
        assert "reverse" not in run.work_item_state["pool_result"]

    async def test_first_pass_all_fail_raises(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(["failing", "failing"], ExecutionMode.FIRST_PASS)
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="All agents failed"):
            await runner.tick()

    async def test_first_pass_escalating_agent_skipped(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Escalating agent counts as failure in first_pass; next agent used."""
        proc = make_pool_process(
            ["escalating", "upper"], ExecutionMode.FIRST_PASS
        )
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.work_item_state["pool_result"]["upper"] == "HELLO"

    async def test_first_pass_all_escalate_raises(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_pool_process(
            ["escalating", "escalating"], ExecutionMode.FIRST_PASS
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="All agents failed"):
            await runner.tick()
