"""Tests for Orchestrator class (US-006)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import Orchestrator, OrchestrationError
from roots.core.schema import (
    AgentNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
    SubProcessNodeConfig,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope
from roots.storage.base import StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    """Collects emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


def make_two_node_process() -> ProcessDefinition:
    """Agent -> End linear process."""
    return ProcessDefinition(
        id="two-node",
        name="Two Node Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="step1",
                type=NodeType.AGENT,
                label="Step 1",
                config=AgentNodeConfig(agent="echo", output_key="step1_out"),
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
    reg.register_local("echo", echo_agent)
    return reg


@pytest.fixture
def decision_engine() -> DecisionEngine:
    return DecisionEngine(default_model="gpt-4o-mini")


@pytest.fixture
def sink() -> CollectorSink:
    return CollectorSink()


@pytest.fixture
def emitter(sink: CollectorSink) -> EventEmitter:
    return EventEmitter(sinks=[sink])


@pytest.fixture
def orchestrator(
    sqlite_storage: StorageBackend,
    registry: AgentRegistry,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
) -> Orchestrator:
    return Orchestrator(
        storage=sqlite_storage,
        agent_registry=registry,
        decision_engine=decision_engine,
        event_emitter=emitter,
        poll_interval=0.01,
    )


# --- Tests ---


class TestOwnerIdUniqueness:
    def test_owner_id_unique_per_instance(
        self,
        sqlite_storage: StorageBackend,
        registry: AgentRegistry,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        o1 = Orchestrator(sqlite_storage, registry, decision_engine, emitter)
        o2 = Orchestrator(sqlite_storage, registry, decision_engine, emitter)
        assert o1.owner_id != o2.owner_id
        assert o1.owner_id.startswith("orchestrator-")
        assert o2.owner_id.startswith("orchestrator-")


class TestStartRun:
    async def test_start_run_creates_pending_run(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        run = await orchestrator.start_run("two-node", {"input": "hello"})
        assert run.status == RunStatus.PENDING
        assert run.process_id == "two-node"
        assert run.current_node_id == "step1"

    async def test_start_run_sets_entry_point(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        run = await orchestrator.start_run("two-node", {"key": "value"})
        assert run.current_node_id == proc.entry_point

    async def test_start_run_nonexistent_process_raises(
        self,
        orchestrator: Orchestrator,
    ) -> None:
        with pytest.raises(OrchestrationError, match="not found"):
            await orchestrator.start_run("nonexistent", {})


class TestTickAll:
    async def test_tick_all_processes_pending_and_running(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        run1 = await orchestrator.start_run("two-node", {"input": "a"})
        run2 = await orchestrator.start_run("two-node", {"input": "b"})

        await orchestrator.tick_all()

        # Both runs should have advanced (pending -> running, step1 executed)
        r1 = await sqlite_storage.get_run(run1.id)
        r2 = await sqlite_storage.get_run(run2.id)
        assert r1 is not None
        assert r2 is not None
        # After one tick on the two-node process: step1 executed, now at "done"
        assert r1.current_node_id == "done"
        assert r2.current_node_id == "done"


class TestExecuteRun:
    async def test_execute_run_completes(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        run = await orchestrator.start_run("two-node", {"input": "hello"})
        await orchestrator.execute_run(run.id)

        completed_run = await sqlite_storage.get_run(run.id)
        assert completed_run is not None
        assert completed_run.status == RunStatus.COMPLETED

    async def test_execute_run_accumulates_state(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        run = await orchestrator.start_run("two-node", {"input": "hello"})
        await orchestrator.execute_run(run.id)

        completed_run = await sqlite_storage.get_run(run.id)
        assert completed_run is not None
        assert "step1_out" in completed_run.work_item_state


class TestRunLoop:
    async def test_run_loop_handles_cancellation(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)
        await orchestrator.start_run("two-node", {"input": "hello"})

        task = asyncio.create_task(orchestrator.run_loop())
        # Give loop time to tick
        await asyncio.sleep(0.05)
        task.cancel()
        # Should not raise CancelledError
        await task

    async def test_run_loop_completes_runs(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)
        run = await orchestrator.start_run("two-node", {"input": "hello"})

        task = asyncio.create_task(orchestrator.run_loop())
        # Give loop time to process
        await asyncio.sleep(0.1)
        task.cancel()
        await task

        completed_run = await sqlite_storage.get_run(run.id)
        assert completed_run is not None
        assert completed_run.status == RunStatus.COMPLETED


class TestStartExecuteCompletedFlow:
    async def test_full_flow(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """start_run -> execute_run -> verify completed."""
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)

        # Start
        run = await orchestrator.start_run("two-node", {"input": "test"})
        assert run.status == RunStatus.PENDING

        # Execute
        await orchestrator.execute_run(run.id)

        # Verify completed
        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == RunStatus.COMPLETED
        assert "step1_out" in final.work_item_state


class TestSubprocessStubHandler:
    async def test_subprocess_node_raises_not_implemented(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Subprocess nodes raise OrchestrationError with a clear not-yet-implemented message."""
        proc = ProcessDefinition(
            id="subprocess-proc",
            name="Subprocess Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="sub-node",
                    type=NodeType.SUBPROCESS,
                    label="Sub",
                    config=SubProcessNodeConfig(
                        process_id="child-proc",
                        output_key="child_result",
                    ),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="sub-node", to_node="done")],
            entry_point="sub-node",
        )
        await sqlite_storage.save_process(proc)

        run = await orchestrator.start_run("subprocess-proc", {"input": "test"})

        with pytest.raises(OrchestrationError, match="not yet implemented"):
            await orchestrator.execute_run(run.id)
