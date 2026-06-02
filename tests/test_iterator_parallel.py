"""Tests for iterator parallel execution core handler (US-006, US-007)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import OrchestrationError, ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    CheckpointNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
    ItemFailureMode,
    IteratorNodeConfig,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope, EventType
from roots.storage.base import RunRecord, StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


def _make_child_process(proc_id: str) -> ProcessDefinition:
    return ProcessDefinition(
        id=proc_id,
        name="Child Process",
        version="1.0.0",
        entry_point="agent1",
        nodes=[
            NodeDefinition(
                id="agent1",
                type=NodeType.AGENT,
                label="Agent",
                config=AgentNodeConfig(agent="echo", output_key="echo_out"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="agent1", to_node="done")],
    )


def _make_failing_child_process(proc_id: str) -> ProcessDefinition:
    return ProcessDefinition(
        id=proc_id,
        name="Failing Child",
        version="1.0.0",
        entry_point="fail_end",
        nodes=[
            NodeDefinition(
                id="fail_end",
                type=NodeType.END,
                label="Fail",
                config=EndNodeConfig(status=EndStatus.FAILED),
            )
        ],
        edges=[],
    )


def _make_checkpoint_child_process(proc_id: str) -> ProcessDefinition:
    """Child process with a checkpoint node — always pauses on first run."""
    return ProcessDefinition(
        id=proc_id,
        name="Checkpoint Child",
        version="1.0.0",
        entry_point="cp",
        nodes=[
            NodeDefinition(
                id="cp",
                type=NodeType.CHECKPOINT,
                label="Checkpoint",
                config=CheckpointNodeConfig(prompt="Review item"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="cp", to_node="done")],
    )


def _make_parallel_iterator_process(
    proc_id: str,
    child_proc_id: str,
    items_key: str = "items",
    item_key: str = "item",
    output_key: str = "results",
    on_item_failure: ItemFailureMode = ItemFailureMode.STOP,
    max_failures: int = 1,
    max_concurrency: int | None = None,
    input_mapping: dict[str, str] | None = None,
) -> ProcessDefinition:
    return ProcessDefinition(
        id=proc_id,
        name="Parallel Iterator Process",
        version="1.0.0",
        entry_point="iter",
        nodes=[
            NodeDefinition(
                id="iter",
                type=NodeType.ITERATOR,
                label="Iterator",
                config=IteratorNodeConfig(
                    items_key=items_key,
                    process_id=child_proc_id,
                    execution_mode=ExecutionMode.PARALLEL,
                    output_key=output_key,
                    item_key=item_key,
                    on_item_failure=on_item_failure,
                    max_failures=max_failures,
                    max_concurrency=max_concurrency,
                    input_mapping=input_mapping or {},
                ),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="iter", to_node="done")],
    )


@pytest.fixture
async def sqlite_storage() -> Any:
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


async def _run_parallel_process(
    storage: StorageBackend,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
    parent_proc: ProcessDefinition,
    *child_procs: ProcessDefinition,
    work_item: dict[str, Any],
) -> tuple[str, RunRecord]:
    await storage.save_process(parent_proc)
    for cp in child_procs:
        await storage.save_process(cp)

    run = await storage.create_run(parent_proc.id, work_item)
    runner = _make_runner(run.id, storage, invoker, decision_engine, emitter)
    await runner.run_to_completion()

    final_run = await storage.get_run(run.id)
    assert final_run is not None
    return run.id, final_run


# ---- Basic parallel execution ----


class TestIteratorParallelBasic:
    """Parallel mode starts child runs concurrently and collects all results."""

    async def test_parallel_iterates_all_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3

    async def test_parallel_creates_child_run_per_item(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x", "y", "z"]},
        )

        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 3

    async def test_parallel_result_envelope_structure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each result has _item_index, _status, _item_value, output."""
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["p", "q"]},
        )

        results = final_run.work_item_state["results"]
        for env in results:
            assert "_item_index" in env
            assert "_status" in env
            assert "_item_value" in env
            assert "output" in env
            assert env["_status"] == "completed"

    async def test_parallel_empty_list_returns_completed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": []},
        )

        assert final_run.status == RunStatus.COMPLETED
        assert final_run.work_item_state.get("results") == []

    async def test_parallel_lifecycle_events_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_STARTED, ITERATOR_ITEM_COMPLETED, ITERATOR_COMPLETED all emitted."""
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b"]},
        )
        await asyncio.sleep(0)

        started = [e for e in sink.events if e.event == EventType.ITERATOR_STARTED]
        completed = [e for e in sink.events if e.event == EventType.ITERATOR_COMPLETED]
        item_done = [e for e in sink.events if e.event == EventType.ITERATOR_ITEM_COMPLETED]

        assert len(started) == 1
        assert len(completed) == 1
        assert len(item_done) == 2


# ---- Order preservation ----


class TestIteratorParallelOrderPreservation:
    """Results are assembled in _item_index order regardless of completion order."""

    async def test_results_in_original_index_order(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["first", "second", "third"]},
        )

        results = final_run.work_item_state["results"]
        assert len(results) == 3
        assert [r["_item_index"] for r in results] == [0, 1, 2]
        assert [r["_item_value"] for r in results] == ["first", "second", "third"]

    async def test_item_key_in_child_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "child", item_key="my_item"
        )
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["hello"]},
        )

        results = final_run.work_item_state["results"]
        assert len(results) == 1
        child_output = results[0]["output"]
        assert "my_item" in child_output
        assert child_output["my_item"] == "hello"


# ---- Concurrency limit ----


class TestIteratorParallelConcurrencyLimit:
    """max_concurrency caps concurrent tasks via Semaphore."""

    async def test_max_concurrency_respected(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """With max_concurrency=1, all items still complete (serial via semaphore)."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "child", max_concurrency=1
        )
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3

    async def test_max_concurrency_2_completes_all(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """With max_concurrency=2, 4 items complete in two waves."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "child", max_concurrency=2
        )
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["w", "x", "y", "z"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 4

    async def test_no_concurrency_limit_completes_all(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Without max_concurrency, all items run unrestricted."""
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["1", "2", "3", "4", "5"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 5


# ---- Crash recovery ----


class TestIteratorParallelCrashRecovery:
    """Presence of branch results triggers resume; uncompleted items re-run."""

    async def test_fresh_run_all_items_execute(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        run_id, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 2

    async def test_resume_skips_completed_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """After crash, parallel iterator resumes by skipping branch-persisted items."""
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)

        run = await sqlite_storage.create_run(
            parent_proc.id, {"items": ["a", "b", "c"]}
        )
        # Simulate crash after item-0 completed
        envelope_0: dict[str, Any] = {
            "_item_index": 0,
            "_status": "completed",
            "_item_value": "a",
            "output": {"item": "a"},
        }
        await sqlite_storage.save_branch_result(
            run.id, "iter", "item-0", "completed", envelope_0
        )
        await sqlite_storage.update_run_status(run.id, RunStatus.RUNNING, "iter")

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        # Only 2 child runs created (item-0 was skipped via crash recovery)
        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 2

        final_run = await sqlite_storage.get_run(run.id)
        assert final_run is not None
        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert results is not None
        assert len(results) == 3
        # First result comes from cached branch (crash recovery)
        assert results[0]["_item_value"] == "a"
        assert results[0]["_status"] == "completed"

    async def test_branch_results_cleared_on_success(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results cleared after successful parallel completion."""
        parent_proc = _make_parallel_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        run_id, _ = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b"]},
        )

        branches = await sqlite_storage.get_branch_results(run_id, "iter")
        assert len(branches) == 0

    async def test_branch_results_preserved_on_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results preserved on failure (for recovery)."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["x"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        branches = await sqlite_storage.get_branch_results(run.id, "iter")
        assert len(branches) >= 1
        assert branches[0].status == "failed"


# ---- Failure mode handling ----


class TestIteratorParallelFailureModes:
    """Failure modes: stop, stop_after_n, continue."""

    async def test_stop_mode_raises_on_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a", "b"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="item.*failed|failed.*item"):
            await runner.run_to_completion()

    async def test_continue_mode_processes_all_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3
        assert all(r["_status"] == "failed" for r in results)

    async def test_continue_mode_failed_envelope_has_error(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x"]},
        )

        results = final_run.work_item_state.get("results")
        assert results is not None
        envelope = results[0]
        assert envelope["_status"] == "failed"
        assert "_error" in envelope["output"]
        assert isinstance(envelope["output"]["_error"], str)

    async def test_stop_after_n_halts_after_max_failures(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP_AFTER_N,
            max_failures=2,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(
            parent_proc.id, {"items": ["a", "b", "c"]}
        )
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="stop_after_n|max_failures"):
            await runner.run_to_completion()

    async def test_stop_mode_emits_iterator_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["x"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        await asyncio.sleep(0)
        failed_events = [e for e in sink.events if e.event == EventType.ITERATOR_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].metadata.get("reason") == "item_failure"

    async def test_stop_after_n_emits_iterator_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP_AFTER_N,
            max_failures=1,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["x"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        await asyncio.sleep(0)
        failed_events = [e for e in sink.events if e.event == EventType.ITERATOR_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].metadata.get("reason") == "max_failures_reached"


# ---- Constraints: checkpoint-as-failure and ITERATOR_ITEM_FAILED events ----


class TestIteratorParallelConstraints:
    """US-007: checkpoint children treated as failure; ITERATOR_ITEM_FAILED events emitted."""

    async def test_checkpoint_child_treated_as_failure_with_clear_message(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Child run pausing at checkpoint in parallel mode → failure with clear error."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "cp-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_checkpoint_child_process("cp-child")

        _, final_run = await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x"]},
        )

        results = final_run.work_item_state.get("results")
        assert results is not None and len(results) == 1
        envelope = results[0]
        assert envelope["_status"] == "failed"
        error_msg = envelope["output"]["_error"]
        assert isinstance(error_msg, str)
        # Must mention parallel mode and suggest sequential as the fix
        assert "parallel" in error_msg.lower()
        assert "sequential" in error_msg.lower()

    async def test_checkpoint_child_emits_item_failed_event(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_ITEM_FAILED is emitted when child pauses at checkpoint in parallel mode."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "cp-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_checkpoint_child_process("cp-child")

        await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x"]},
        )
        await asyncio.sleep(0)

        item_failed = [e for e in sink.events if e.event == EventType.ITERATOR_ITEM_FAILED]
        assert len(item_failed) == 1
        assert item_failed[0].metadata.get("item_index") == 0

    async def test_iterator_item_failed_events_emitted_for_each_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_ITEM_FAILED emitted once per failed item in CONTINUE mode."""
        parent_proc = _make_parallel_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        await _run_parallel_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )
        await asyncio.sleep(0)

        item_failed_events = [
            e for e in sink.events if e.event == EventType.ITERATOR_ITEM_FAILED
        ]
        assert len(item_failed_events) == 3
        indices = {e.metadata.get("item_index") for e in item_failed_events}
        assert indices == {0, 1, 2}
