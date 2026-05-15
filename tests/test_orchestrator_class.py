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
from roots.events.types import EventEnvelope, EventType
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


class TestSubprocessHandler:
    def _make_child_process(self) -> ProcessDefinition:
        """Child: echo agent -> END."""
        return ProcessDefinition(
            id="child-proc",
            name="Child Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="step",
                    type=NodeType.AGENT,
                    label="Step",
                    config=AgentNodeConfig(agent="echo", output_key="step_out"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="step", to_node="done")],
            entry_point="step",
        )

    def _make_parent_process(
        self,
        input_mapping: dict[str, str] | None = None,
        output_mapping: dict[str, str] | None = None,
    ) -> ProcessDefinition:
        """Parent: SUBPROCESS node -> END."""
        return ProcessDefinition(
            id="parent-proc",
            name="Parent Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="sub-node",
                    type=NodeType.SUBPROCESS,
                    label="Sub",
                    config=SubProcessNodeConfig(
                        process_id="child-proc",
                        output_key="child_result",
                        input_mapping=input_mapping or {},
                        output_mapping=output_mapping or {},
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

    async def test_subprocess_executes_child_and_completes(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Parent run completes after child process executes to completion."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_parent_process())

        run = await orchestrator.start_run("parent-proc", {"input": "test"})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.COMPLETED

        children = await sqlite_storage.get_child_runs(run.id)
        assert len(children) == 1
        child = children[0]
        assert child.status == RunStatus.COMPLETED
        assert child.parent_run_id == run.id
        assert child.parent_node_id == "sub-node"

    async def test_subprocess_input_mapping_applied(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """input_mapping copies specified parent keys into child initial state."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(
            self._make_parent_process(input_mapping={"parent_val": "child_input"})
        )

        run = await orchestrator.start_run("parent-proc", {"parent_val": "hello"})
        await orchestrator.execute_run(run.id)

        children = await sqlite_storage.get_child_runs(run.id)
        assert len(children) == 1
        # child_input was set from parent_val
        assert children[0].work_item_state.get("child_input") == "hello"

    async def test_subprocess_missing_input_key_raises(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Missing input_mapping parent key raises OrchestrationError."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(
            self._make_parent_process(input_mapping={"missing_key": "child_input"})
        )

        run = await orchestrator.start_run("parent-proc", {"other": "value"})
        with pytest.raises(OrchestrationError, match="missing_key"):
            await orchestrator.execute_run(run.id)

    async def test_subprocess_output_mapping_applied(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """output_mapping copies child state keys into result stored at output_key."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(
            self._make_parent_process(output_mapping={"step_out": "result"})
        )

        run = await orchestrator.start_run("parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.COMPLETED
        child_result = parent.work_item_state.get("child_result")
        assert isinstance(child_result, dict)
        assert "result" in child_result

    async def test_subprocess_missing_output_key_produces_none(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Missing child output key in output_mapping produces None, not KeyError."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(
            self._make_parent_process(output_mapping={"nonexistent_key": "result"})
        )

        run = await orchestrator.start_run("parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.COMPLETED
        child_result = parent.work_item_state.get("child_result")
        assert isinstance(child_result, dict)
        assert child_result.get("result") is None

    async def test_subprocess_depth_injected_into_child_state(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """_subprocess_depth incremented in child initial state."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_parent_process())

        run = await orchestrator.start_run("parent-proc", {})
        await orchestrator.execute_run(run.id)

        children = await sqlite_storage.get_child_runs(run.id)
        assert len(children) == 1
        assert children[0].work_item_state.get("_subprocess_depth") == 1

    async def test_subprocess_child_run_id_stored_in_parent_state(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """_subprocess_run_<node_id> key is present in parent final state."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_parent_process())

        run = await orchestrator.start_run("parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert "_subprocess_run_sub-node" in parent.work_item_state

    async def test_subprocess_events_emitted(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
        sink: CollectorSink,
    ) -> None:
        """SUBPROCESS_STARTED and SUBPROCESS_COMPLETED events are emitted."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_parent_process())

        run = await orchestrator.start_run("parent-proc", {})
        await orchestrator.execute_run(run.id)

        event_types = [e.event for e in sink.events]
        assert EventType.SUBPROCESS_STARTED in event_types
        assert EventType.SUBPROCESS_COMPLETED in event_types

        started = next(e for e in sink.events if e.event == EventType.SUBPROCESS_STARTED)
        completed = next(e for e in sink.events if e.event == EventType.SUBPROCESS_COMPLETED)
        assert started.metadata.get("child_run_id") is not None
        assert completed.metadata.get("child_run_id") == started.metadata["child_run_id"]
