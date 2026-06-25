"""Tests for Orchestrator class (US-006)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine
from roots.core.escalation import EscalationTrigger
from roots.core.orchestrator import Orchestrator, OrchestrationError, ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    CheckpointNodeConfig,
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
        agent_invoker=AgentInvoker(registry),
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
        o1 = Orchestrator(sqlite_storage, AgentInvoker(registry), decision_engine, emitter)
        o2 = Orchestrator(sqlite_storage, AgentInvoker(registry), decision_engine, emitter)
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


class TestMidNodeCancel:
    async def test_external_cancel_mid_node_sticks(
        self,
        sqlite_storage: StorageBackend,
        registry: AgentRegistry,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """A cancel landing while a node runs must survive the post-node persist.

        The node's handler flips the run to CANCELLED (simulating an external
        cancel request). The post-node persist must NOT overwrite that with
        RUNNING/next — the run stays CANCELLED and does not advance.
        """

        async def cancelling_agent(input: dict[str, Any]) -> dict[str, Any]:
            await sqlite_storage.update_run_status(
                input["run_id"], RunStatus.CANCELLED, "step1"
            )
            return {"output": {"done": True}, "escalate": False}

        registry.register_local("canceller", cancelling_agent)

        proc = ProcessDefinition(
            id="cancel-proc",
            name="Cancel Proc",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="step1",
                    type=NodeType.AGENT,
                    label="Step 1",
                    config=AgentNodeConfig(agent="canceller", output_key="out"),
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
        await sqlite_storage.save_process(proc)

        orch = Orchestrator(
            sqlite_storage, AgentInvoker(registry), decision_engine, emitter
        )
        run = await orch.start_run("cancel-proc", {"input": "x"})

        runner = ProcessRunner(
            run.id,
            sqlite_storage,
            AgentInvoker(registry),
            decision_engine,
            emitter,
            owner_id="test-owner",
        )
        # First tick: pending -> running, step1 executes and cancels mid-node.
        should_continue = await runner.tick()

        assert should_continue is False
        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == RunStatus.CANCELLED
        # Must not have advanced to the End node.
        assert final.current_node_id != "done"


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

    async def test_subprocess_dotted_input_mapping_resolves(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """A dotted input_mapping key resolves a value nested under an output_key."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(
            self._make_parent_process(
                input_mapping={"epic_plan.data": "child_input"}
            )
        )

        run = await orchestrator.start_run(
            "parent-proc", {"epic_plan": {"data": "hello"}}
        )
        await orchestrator.execute_run(run.id)

        children = await sqlite_storage.get_child_runs(run.id)
        assert len(children) == 1
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

    # --- US-002: Subprocess pause cascading ---

    def _make_pausing_child_process(self) -> ProcessDefinition:
        """Child: CHECKPOINT -> END (pauses at checkpoint on first execution)."""
        return ProcessDefinition(
            id="pausing-child-proc",
            name="Pausing Child",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="cp",
                    type=NodeType.CHECKPOINT,
                    label="Review",
                    config=CheckpointNodeConfig(prompt="Needs review"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="cp", to_node="done")],
            entry_point="cp",
        )

    def _make_pausing_parent_process(self) -> ProcessDefinition:
        """Parent: SUBPROCESS(pausing-child-proc) -> END."""
        return ProcessDefinition(
            id="pausing-parent-proc",
            name="Pausing Parent",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="sub",
                    type=NodeType.SUBPROCESS,
                    label="Sub",
                    config=SubProcessNodeConfig(
                        process_id="pausing-child-proc",
                        output_key="child_result",
                        input_mapping={},
                        output_mapping={},
                    ),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="sub", to_node="done")],
            entry_point="sub",
        )

    async def test_subprocess_initial_pause_cascades_to_parent(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Child pause cascades to parent with SUBPROCESS_PAUSED escalation."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED
        assert "_subprocess_run_sub" in parent.work_item_state

        escalation = await sqlite_storage.get_pending_escalation(run.id)
        assert escalation is not None
        assert escalation.trigger_type == EscalationTrigger.SUBPROCESS_PAUSED
        child_run_id = parent.work_item_state["_subprocess_run_sub"]
        assert child_run_id in escalation.reason

    async def test_subprocess_resume_child_completed(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Resuming parent when child completed extracts output; no duplicate child run."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED
        child_run_id = parent.work_item_state["_subprocess_run_sub"]

        # Advance child past checkpoint to "done" and complete it
        child = await sqlite_storage.get_run(child_run_id)
        assert child is not None
        assert child.status == RunStatus.PAUSED
        await sqlite_storage.update_run_status(child_run_id, RunStatus.RUNNING, "done")
        child_runner_proc = orchestrator
        await child_runner_proc.execute_run(child_run_id)

        child_after = await sqlite_storage.get_run(child_run_id)
        assert child_after is not None
        assert child_after.status == RunStatus.COMPLETED

        # Resume parent — should detect child completed and finish
        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )
        await orchestrator.execute_run(run.id)

        parent_after = await sqlite_storage.get_run(run.id)
        assert parent_after is not None
        assert parent_after.status == RunStatus.COMPLETED

        # No duplicate child runs
        children = await sqlite_storage.get_child_runs(run.id)
        assert len(children) == 1

    async def test_subprocess_resume_child_still_paused(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Resuming parent when child still paused re-pauses parent without new escalation."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED

        # Resolve original escalation so we can detect if a new one is created
        escalation = await sqlite_storage.get_pending_escalation(run.id)
        assert escalation is not None
        await sqlite_storage.resolve_escalation(escalation.id, {"resolved_by": "test"})

        # Resume parent while child is still paused
        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )
        await orchestrator.execute_run(run.id)

        parent_after = await sqlite_storage.get_run(run.id)
        assert parent_after is not None
        assert parent_after.status == RunStatus.PAUSED

        # No new escalation created — re-pause used self._escalated = True directly
        new_escalation = await sqlite_storage.get_pending_escalation(run.id)
        assert new_escalation is None

    async def test_subprocess_resume_missing_child_run_raises(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Resuming parent with a nonexistent stored child_run_id raises OrchestrationError."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED

        # Overwrite stored child_run_id with a nonexistent ID
        bad_state = dict(parent.work_item_state)
        bad_state["_subprocess_run_sub"] = "run-nonexistent-id"
        await sqlite_storage.update_work_item_state(run.id, bad_state)

        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )

        with pytest.raises(OrchestrationError, match="not found in storage"):
            await orchestrator.execute_run(run.id)

    # --- US-003: Subprocess failure propagation ---

    async def test_subprocess_child_failed_propagates_to_parent(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
        sink: CollectorSink,
    ) -> None:
        """Child run FAILED → parent node fails; SUBPROCESS_FAILED, NODE_FAILED, RUN_FAILED emitted."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED
        child_run_id: str = parent.work_item_state["_subprocess_run_sub"]

        # Simulate child run failing
        await sqlite_storage.update_run_status(child_run_id, RunStatus.FAILED)

        # Resume parent — should detect child FAILED and fail itself
        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )
        await orchestrator.execute_run(run.id)

        parent_after = await sqlite_storage.get_run(run.id)
        assert parent_after is not None
        assert parent_after.status == RunStatus.FAILED

        event_types = [e.event for e in sink.events]
        assert EventType.SUBPROCESS_FAILED in event_types
        assert EventType.NODE_FAILED in event_types
        assert EventType.RUN_FAILED in event_types

        subprocess_failed_evt = next(
            e for e in sink.events if e.event == EventType.SUBPROCESS_FAILED
        )
        assert subprocess_failed_evt.metadata.get("child_run_id") == child_run_id
        assert subprocess_failed_evt.metadata.get("child_status") == RunStatus.FAILED

    async def test_subprocess_child_cancelled_propagates_to_parent(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Child run CANCELLED → parent node fails (same behavior as FAILED)."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.PAUSED
        child_run_id: str = parent.work_item_state["_subprocess_run_sub"]

        await sqlite_storage.update_run_status(child_run_id, RunStatus.CANCELLED)

        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )
        await orchestrator.execute_run(run.id)

        parent_after = await sqlite_storage.get_run(run.id)
        assert parent_after is not None
        assert parent_after.status == RunStatus.FAILED

    async def test_subprocess_failure_metadata_has_child_context(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
        sink: CollectorSink,
    ) -> None:
        """NODE_FAILED and RUN_FAILED metadata include child_run_id and child_status."""
        await sqlite_storage.save_process(self._make_pausing_child_process())
        await sqlite_storage.save_process(self._make_pausing_parent_process())

        run = await orchestrator.start_run("pausing-parent-proc", {})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        child_run_id: str = parent.work_item_state["_subprocess_run_sub"]

        await sqlite_storage.update_run_status(child_run_id, RunStatus.FAILED)

        assert parent.current_node_id is not None
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, parent.current_node_id
        )
        await orchestrator.execute_run(run.id)

        node_failed_evt = next(
            e for e in sink.events
            if e.event == EventType.NODE_FAILED
            and e.metadata.get("reason") == "subprocess_failed"
        )
        run_failed_evt = next(
            e for e in sink.events
            if e.event == EventType.RUN_FAILED
            and e.metadata.get("reason") == "subprocess_failed"
        )
        assert node_failed_evt.metadata.get("child_run_id") == child_run_id
        assert node_failed_evt.metadata.get("child_status") == RunStatus.FAILED
        assert run_failed_evt.metadata.get("child_run_id") == child_run_id
        assert run_failed_evt.metadata.get("child_status") == RunStatus.FAILED

    # --- US-004: Subprocess depth limit ---

    def _make_depth_limited_parent(self, max_depth: int = 5) -> ProcessDefinition:
        """Parent: SUBPROCESS(child-proc, max_depth=N) -> END."""
        return ProcessDefinition(
            id="depth-parent-proc",
            name="Depth Parent",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="sub-node",
                    type=NodeType.SUBPROCESS,
                    label="Sub",
                    config=SubProcessNodeConfig(
                        process_id="child-proc",
                        output_key="child_result",
                        max_depth=max_depth,
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

    async def test_subprocess_depth_limit_exceeded_raises(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """OrchestrationError raised when _subprocess_depth >= max_depth."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_depth_limited_parent(max_depth=2))

        # Pre-inject depth at the limit to simulate being 2 levels deep already
        run = await orchestrator.start_run("depth-parent-proc", {"_subprocess_depth": 2})
        with pytest.raises(OrchestrationError, match="Subprocess depth limit exceeded: 2/2"):
            await orchestrator.execute_run(run.id)

    async def test_subprocess_depth_error_message_format(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Error message includes current depth and max_depth."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_depth_limited_parent(max_depth=3))

        run = await orchestrator.start_run("depth-parent-proc", {"_subprocess_depth": 5})
        with pytest.raises(OrchestrationError, match="Subprocess depth limit exceeded: 5/3"):
            await orchestrator.execute_run(run.id)

    async def test_subprocess_default_depth_limit_blocks_at_five(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Default max_depth=5 prevents execution when depth reaches 5."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_depth_limited_parent())  # default max_depth=5

        run = await orchestrator.start_run("depth-parent-proc", {"_subprocess_depth": 5})
        with pytest.raises(OrchestrationError, match="Subprocess depth limit exceeded: 5/5"):
            await orchestrator.execute_run(run.id)

    async def test_subprocess_depth_below_limit_succeeds(
        self,
        sqlite_storage: StorageBackend,
        orchestrator: Orchestrator,
    ) -> None:
        """Subprocess executes normally when depth is below max_depth."""
        await sqlite_storage.save_process(self._make_child_process())
        await sqlite_storage.save_process(self._make_depth_limited_parent(max_depth=3))

        # Depth 2 is below max_depth=3, so subprocess should succeed
        run = await orchestrator.start_run("depth-parent-proc", {"_subprocess_depth": 2})
        await orchestrator.execute_run(run.id)

        parent = await sqlite_storage.get_run(run.id)
        assert parent is not None
        assert parent.status == RunStatus.COMPLETED
