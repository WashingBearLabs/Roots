"""Tests for iterator sequential execution core handler (US-003, US-004)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.escalation import EscalationTrigger
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
    """Collects emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


def _make_child_process(proc_id: str) -> ProcessDefinition:
    """Child process: echo agent → done(completed)."""
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
    """Child process that immediately terminates with FAILED status."""
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


def _make_iterator_process(
    proc_id: str,
    child_proc_id: str,
    items_key: str = "items",
    item_key: str = "item",
    output_key: str = "results",
    on_item_failure: ItemFailureMode = ItemFailureMode.STOP,
    max_depth: int = 5,
    max_failures: int = 1,
    input_mapping: dict[str, str] | None = None,
) -> ProcessDefinition:
    """Parent process with one iterator node followed by an end node."""
    return ProcessDefinition(
        id=proc_id,
        name="Iterator Process",
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
                    execution_mode=ExecutionMode.SEQUENTIAL,
                    output_key=output_key,
                    item_key=item_key,
                    on_item_failure=on_item_failure,
                    max_depth=max_depth,
                    max_failures=max_failures,
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


async def _run_iterator_process(
    storage: StorageBackend,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
    parent_proc: ProcessDefinition,
    *child_procs: ProcessDefinition,
    work_item: dict[str, Any],
    parent_metadata: dict[str, Any] | None = None,
) -> tuple[str, RunRecord]:
    """Save processes, create and run parent, return (run_id, final_run)."""
    await storage.save_process(parent_proc)
    for cp in child_procs:
        await storage.save_process(cp)

    run = await storage.create_run(
        parent_proc.id,
        work_item,
        metadata=parent_metadata,
    )
    runner = _make_runner(run.id, storage, invoker, decision_engine, emitter)
    await runner.run_to_completion()

    final_run = await storage.get_run(run.id)
    assert final_run is not None
    return run.id, final_run


# ---- Validation tests (no real storage needed) ----


class TestIteratorValidation:
    """Runtime validation of items_key."""

    async def test_missing_items_key_raises(
        self,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        runner = ProcessRunner(
            run_id="r1",
            storage=MagicMock(),
            agent_invoker=invoker,
            decision_engine=decision_engine,
            event_emitter=emitter,
            owner_id="owner",
        )
        runner._process_id = "proc"

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        )

        with pytest.raises(OrchestrationError, match="items_key.*not found"):
            await runner._handle_iterator(node, {"other": "value"})

    async def test_non_list_string_raises(
        self,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        runner = ProcessRunner(
            run_id="r1",
            storage=MagicMock(),
            agent_invoker=invoker,
            decision_engine=decision_engine,
            event_emitter=emitter,
            owner_id="owner",
        )
        runner._process_id = "proc"

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        )

        with pytest.raises(OrchestrationError, match="must be a list"):
            await runner._handle_iterator(node, {"items": "not-a-list"})

    async def test_non_list_dict_raises(
        self,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        runner = ProcessRunner(
            run_id="r1",
            storage=MagicMock(),
            agent_invoker=invoker,
            decision_engine=decision_engine,
            event_emitter=emitter,
            owner_id="owner",
        )
        runner._process_id = "proc"

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        )

        with pytest.raises(OrchestrationError, match="must be a list"):
            await runner._handle_iterator(node, {"items": {"key": "val"}})

    async def test_non_list_int_raises(
        self,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        runner = ProcessRunner(
            run_id="r1",
            storage=MagicMock(),
            agent_invoker=invoker,
            decision_engine=decision_engine,
            event_emitter=emitter,
            owner_id="owner",
        )
        runner._process_id = "proc"

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        )

        with pytest.raises(OrchestrationError, match="must be a list"):
            await runner._handle_iterator(node, {"items": 42})


# ---- Depth enforcement tests ----


class TestIteratorDepthEnforcement:
    """Subprocess depth enforcement."""

    async def test_depth_exceeded_raises(
        self,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """If current depth == max_depth, child_depth > max_depth → error."""
        storage = AsyncMock()
        runner = ProcessRunner(
            run_id="r1",
            storage=storage,
            agent_invoker=invoker,
            decision_engine=decision_engine,
            event_emitter=emitter,
            owner_id="owner",
        )
        runner._process_id = "proc"

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
                max_depth=5,
            ),
        )

        with pytest.raises(OrchestrationError, match="depth.*exceeded|exceeded.*depth"):
            await runner._handle_iterator(node, {"items": ["x"], "_subprocess_depth": 5})

    async def test_depth_at_zero_allows_child(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """A run at depth 0 (no metadata) can spawn children (depth 1 ≤ 5)."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a"]},
        )
        assert final_run.status == RunStatus.COMPLETED

    async def test_child_run_has_depth_in_metadata(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Child runs are created with _subprocess_depth in their metadata."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, _ = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a"]},
        )

        # Find child runs created for process "child"
        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 1
        child_run = child_runs[0]
        assert child_run.metadata is not None
        assert child_run.metadata.get("_subprocess_depth") == 1
        assert child_run.metadata.get("parent_run_id") is not None
        assert child_run.metadata.get("parent_node_id") == "iter"


# ---- Sequential iteration tests ----


class TestIteratorSequential:
    """Sequential mode: one item at a time."""

    async def test_sequential_iterates_all_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3

    async def test_result_envelope_structure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each result has the uniform envelope: _item_index, _status, _item_value, output."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x", "y"]},
        )

        results = final_run.work_item_state["results"]
        for i, env in enumerate(results):
            assert env["_item_index"] == i
            assert env["_status"] == "completed"
            assert env["_item_value"] == ["x", "y"][i]
            assert isinstance(env["output"], dict)

    async def test_result_indices_sequential(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Results are 0-indexed in order."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["first", "second", "third"]},
        )

        results = final_run.work_item_state["results"]
        assert [r["_item_index"] for r in results] == [0, 1, 2]
        assert [r["_item_value"] for r in results] == ["first", "second", "third"]

    async def test_item_key_in_child_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Child work_item_state contains the item under item_key."""
        parent_proc = _make_iterator_process("parent", "child", item_key="my_item")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["hello"]},
        )

        results = final_run.work_item_state["results"]
        assert len(results) == 1
        child_output = results[0]["output"]
        # Child state should contain "my_item" (injected by iterator)
        assert "my_item" in child_output
        assert child_output["my_item"] == "hello"

    async def test_input_mapping_applied_to_child(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """input_mapping copies parent state keys into child work_item."""
        parent_proc = _make_iterator_process(
            "parent", "child",
            input_mapping={"ctx": "context"},
        )
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["item1"], "ctx": "shared-context"},
        )

        results = final_run.work_item_state["results"]
        child_output = results[0]["output"]
        # "context" (renamed from "ctx") should appear in child state
        assert "context" in child_output
        assert child_output["context"] == "shared-context"

    async def test_three_child_runs_created(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """One child run is created per item."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 3


# ---- Empty list tests ----


class TestIteratorEmptyList:
    """Empty items list: no error, empty results."""

    async def test_empty_list_returns_completed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": []},
        )

        assert final_run.status == RunStatus.COMPLETED

    async def test_empty_list_result_is_empty_list(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": []},
        )

        results = final_run.work_item_state.get("results")
        assert results == []

    async def test_empty_list_no_child_runs(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": []},
        )

        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 0


# ---- Default failure behavior tests ----


class TestIteratorFailureBehavior:
    """on_item_failure defaults to stop — fails iterator on first child failure."""

    async def test_default_stop_fails_on_first_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Default on_item_failure=stop: OrchestrationError raised on first failure."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a", "b", "c"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="item.*failed|failed.*item"):
            await runner.run_to_completion()

    async def test_stop_mode_only_runs_first_item(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """With stop mode, only one child run is created before raising."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a", "b", "c"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        child_runs = await sqlite_storage.list_runs(process_id="fail-child")
        assert len(child_runs) == 1

    async def test_continue_mode_processes_all_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """on_item_failure=continue: iterator continues past failures."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        # Iterator completes (all failures accumulated)
        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3
        for r in results:
            assert r["_status"] == "failed"

    async def test_default_is_stop_mode(self) -> None:
        """IteratorNodeConfig defaults on_item_failure to 'stop'."""
        config = IteratorNodeConfig(
            items_key="items",
            process_id="sub",
            execution_mode=ExecutionMode.SEQUENTIAL,
            output_key="results",
            item_key="item",
        )
        assert config.on_item_failure == ItemFailureMode.STOP


# ---- US-005: Failure handling modes ----


class TestIteratorFailureModes:
    """on_item_failure modes: continue, stop, stop_after_n."""

    async def test_failed_envelope_has_error_key(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed items use uniform envelope with output._error as str."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x"]},
        )

        results = final_run.work_item_state.get("results")
        assert results is not None and len(results) == 1
        envelope = results[0]
        assert envelope["_status"] == "failed"
        assert isinstance(envelope["output"], dict)
        assert "_error" in envelope["output"]
        assert isinstance(envelope["output"]["_error"], str)

    async def test_all_fail_with_continue_completes(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """With continue mode, iterator completes even when all items fail."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.CONTINUE,
        )
        child_proc = _make_failing_child_process("fail-child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3
        assert all(r["_status"] == "failed" for r in results)
        assert all("_error" in r["output"] for r in results)

    async def test_stop_after_n_halts_after_max_failures(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """stop_after_n halts when failure count reaches max_failures."""
        parent_proc = _make_iterator_process(
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

    async def test_stop_after_n_only_creates_n_child_runs(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """stop_after_n creates exactly max_failures child runs when all fail."""
        parent_proc = _make_iterator_process(
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

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        child_runs = await sqlite_storage.list_runs(process_id="fail-child")
        assert len(child_runs) == 2

    async def test_stop_after_n_preserves_completed_results(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """stop_after_n preserves completed branch results in storage on failure."""
        parent_proc = _make_iterator_process(
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

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        branches = await sqlite_storage.get_branch_results(run.id, "iter")
        assert len(branches) == 2
        assert all(b.status == "failed" for b in branches)

    async def test_stop_after_n_emits_iterator_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_FAILED is emitted when stop_after_n limit is reached."""
        parent_proc = _make_iterator_process(
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

    async def test_stop_after_n_emits_item_failed_per_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_ITEM_FAILED emitted for each failed item in stop_after_n mode."""
        parent_proc = _make_iterator_process(
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

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        await asyncio.sleep(0)
        item_failed_events = [
            e for e in sink.events if e.event == EventType.ITERATOR_ITEM_FAILED
        ]
        assert len(item_failed_events) == 2


# ---- Branch results persistence tests ----


class TestIteratorBranchPersistence:
    """Results are persisted via save_branch_result; cleared on success."""

    async def test_successful_completion_clears_branch_results(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results are cleared after successful completion (US-004)."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        run_id, _ = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b"]},
        )

        # Results cleared on success — not preserved after completion
        branches = await sqlite_storage.get_branch_results(run_id, "iter")
        assert len(branches) == 0

    async def test_failed_item_saved_as_failed_branch(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """On STOP failure, failed branch result is preserved for crash recovery."""
        parent_proc = _make_iterator_process(
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

        # Branch results preserved on failure (not cleared)
        branches = await sqlite_storage.get_branch_results(run.id, "iter")
        assert len(branches) == 1
        assert branches[0].branch_id == "item-0"
        assert branches[0].status == "failed"


# ---- Lifecycle event tests ----


class TestIteratorLifecycleEvents:
    """ITERATOR_STARTED, ITERATOR_ITEM_COMPLETED, ITERATOR_COMPLETED emitted."""

    async def _collect_events(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
        work_item: dict[str, Any],
        on_item_failure: ItemFailureMode = ItemFailureMode.STOP,
        child_proc_id: str = "child",
        use_failing: bool = False,
    ) -> tuple[str, list[EventEnvelope]]:
        parent_proc = _make_iterator_process(
            "parent", child_proc_id,
            on_item_failure=on_item_failure,
        )
        if use_failing:
            child_proc = _make_failing_child_process(child_proc_id)
        else:
            child_proc = _make_child_process(child_proc_id)

        run_id, _ = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item=work_item,
        )
        # Let pending event tasks complete
        await asyncio.sleep(0)
        return run_id, sink.events

    async def test_iterator_started_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": ["a", "b"]},
        )

        started = [e for e in events if e.event == EventType.ITERATOR_STARTED]
        assert len(started) == 1
        assert started[0].metadata.get("items_count") == 2

    async def test_iterator_item_completed_emitted_per_item(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": ["a", "b", "c"]},
        )

        item_completed = [
            e for e in events if e.event == EventType.ITERATOR_ITEM_COMPLETED
        ]
        assert len(item_completed) == 3
        indices = sorted(e.metadata["item_index"] for e in item_completed)
        assert indices == [0, 1, 2]

    async def test_iterator_completed_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": ["a", "b"]},
        )

        completed = [e for e in events if e.event == EventType.ITERATOR_COMPLETED]
        assert len(completed) == 1
        assert completed[0].metadata.get("items_count") == 2
        assert completed[0].metadata.get("results_count") == 2

    async def test_iterator_completed_emitted_for_empty_list(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": []},
        )

        completed = [e for e in events if e.event == EventType.ITERATOR_COMPLETED]
        assert len(completed) == 1
        assert completed[0].metadata.get("items_count") == 0
        assert completed[0].metadata.get("results_count") == 0

    async def test_iterator_item_failed_emitted_on_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": ["x"]},
            on_item_failure=ItemFailureMode.CONTINUE,
            child_proc_id="fail-child",
            use_failing=True,
        )

        failed = [e for e in events if e.event == EventType.ITERATOR_ITEM_FAILED]
        assert len(failed) == 1
        assert failed[0].metadata.get("item_index") == 0

    async def test_iterator_failed_emitted_on_stop_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_FAILED emitted before OrchestrationError on stop-mode failure."""
        parent_proc = _make_iterator_process(
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
        events = sink.events

        failed = [e for e in events if e.event == EventType.ITERATOR_FAILED]
        assert len(failed) == 1
        assert failed[0].metadata.get("failed_at_index") == 0

    async def test_event_ordering_started_before_completed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """ITERATOR_STARTED precedes ITERATOR_COMPLETED."""
        _, events = await self._collect_events(
            sqlite_storage, invoker, decision_engine, sink, emitter,
            work_item={"items": ["a"]},
        )

        iter_events = [
            e for e in events
            if e.event in (EventType.ITERATOR_STARTED, EventType.ITERATOR_COMPLETED)
        ]
        event_types = [e.event for e in iter_events]
        assert EventType.ITERATOR_STARTED in event_types
        assert EventType.ITERATOR_COMPLETED in event_types
        started_idx = event_types.index(EventType.ITERATOR_STARTED)
        completed_idx = event_types.index(EventType.ITERATOR_COMPLETED)
        assert started_idx < completed_idx


# ---- Crash recovery tests (US-004) ----


class TestIteratorCrashRecovery:
    """Crash recovery: resume from first incomplete item using branch result presence."""

    async def test_fresh_execution_runs_all_items(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Without prior branch results, fresh execution processes every item."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        run_id, final_run = await _run_iterator_process(
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
        """After crash, iterator resumes from first incomplete item."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)

        run = await sqlite_storage.create_run(
            parent_proc.id, {"items": ["a", "b", "c"]}
        )
        # Simulate crash after item-0 completed: seed branch result
        envelope_0: dict[str, Any] = {
            "_item_index": 0,
            "_status": "completed",
            "_item_value": "a",
            "output": {"item": "a"},
        }
        await sqlite_storage.save_branch_result(
            run.id, "iter", "item-0", "completed", envelope_0
        )
        # Simulate run was already in RUNNING state when crash occurred
        await sqlite_storage.update_run_status(run.id, RunStatus.RUNNING, "iter")

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        # Only 2 child runs created (item-0 was skipped via crash recovery)
        child_runs = await sqlite_storage.list_runs(process_id="child")
        assert len(child_runs) == 2

        # Final results include all 3 items in order
        final_run = await sqlite_storage.get_run(run.id)
        assert final_run is not None
        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert results is not None
        assert len(results) == 3
        assert results[0]["_item_value"] == "a"
        assert results[0]["_status"] == "completed"

    async def test_failure_preserves_branch_results_for_recovery(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """On STOP mode failure, branch results are NOT cleared (preserved for recovery)."""
        parent_proc = _make_iterator_process(
            "parent", "fail-child",
            on_item_failure=ItemFailureMode.STOP,
        )
        child_proc = _make_failing_child_process("fail-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(
            parent_proc.id, {"items": ["a", "b"]}
        )
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        try:
            await runner.run_to_completion()
        except OrchestrationError:
            pass

        branches = await sqlite_storage.get_branch_results(run.id, "iter")
        assert len(branches) >= 1
        assert branches[0].status == "failed"

    async def test_success_clears_branch_results(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results cleared after successful completion."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        run_id, _ = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b"]},
        )

        branches = await sqlite_storage.get_branch_results(run_id, "iter")
        assert len(branches) == 0


# ---- Pause cascade tests (US-004) ----


class TestIteratorPauseCascade:
    """Child pause cascades to parent via SUBPROCESS_PAUSED escalation."""

    async def test_child_pause_cascades_to_parent(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """When child pauses at checkpoint, parent iterator pauses too."""
        parent_proc = _make_iterator_process("parent", "cp-child")
        child_proc = _make_checkpoint_child_process("cp-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        # Parent should be paused
        parent_run = await sqlite_storage.get_run(run.id)
        assert parent_run is not None
        assert parent_run.status == RunStatus.PAUSED

        # State should track the paused child
        assert "_iterator_paused_child_run_id" in parent_run.work_item_state
        assert parent_run.work_item_state["_iterator_paused_item_index"] == 0

    async def test_pause_cascade_sets_subprocess_paused_trigger(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """SUBPROCESS_PAUSED escalation is triggered when child pauses."""
        parent_proc = _make_iterator_process("parent", "cp-child")
        child_proc = _make_checkpoint_child_process("cp-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await asyncio.sleep(0)

        # Escalation events emitted (child checkpoint + parent subprocess_paused)
        esc_events = [e for e in sink.events if e.event == EventType.ESCALATION_TRIGGERED]
        assert len(esc_events) >= 1
        trigger_types = {e.metadata.get("trigger_type") for e in esc_events}
        assert EscalationTrigger.SUBPROCESS_PAUSED in trigger_types

    async def test_pause_resume_completes_iteration(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """After pause cascade, resolving child and resuming parent completes iteration."""
        parent_proc = _make_iterator_process("parent", "cp-child")
        child_proc = _make_checkpoint_child_process("cp-child")

        await sqlite_storage.save_process(parent_proc)
        await sqlite_storage.save_process(child_proc)
        # Use single item so after resume, iteration completes without another pause
        run = await sqlite_storage.create_run(parent_proc.id, {"items": ["a"]})
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        # First run: child pauses at checkpoint → parent pauses
        await runner.run_to_completion()

        parent_run = await sqlite_storage.get_run(run.id)
        assert parent_run is not None
        assert parent_run.status == RunStatus.PAUSED

        # Resolve child's checkpoint: advance child past "cp" to "done"
        paused_child_id = parent_run.work_item_state["_iterator_paused_child_run_id"]
        await sqlite_storage.update_run_status(
            paused_child_id, RunStatus.RUNNING, "done"
        )

        # Resume parent
        await sqlite_storage.update_run_status(run.id, RunStatus.RUNNING, "iter")

        # Resume execution — paused child resumes and completes, iterator finishes
        resumed_runner = _make_runner(
            run.id, sqlite_storage, invoker, decision_engine, emitter
        )
        await resumed_runner.run_to_completion()

        final_run = await sqlite_storage.get_run(run.id)
        assert final_run is not None
        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert results is not None
        assert len(results) == 1


# ---- Lock renewal tests (US-004) ----


class TestIteratorLockRenewal:
    """Lock renewal task runs in background during child execution."""

    async def test_lock_renewal_does_not_break_normal_execution(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Lock renewal task is created and cancelled without affecting correctness."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["a", "b", "c"]},
        )

        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3

    async def test_multiple_items_complete_with_renewal_running(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Renewal task runs quietly in background; all items complete correctly."""
        parent_proc = _make_iterator_process("parent", "child")
        child_proc = _make_child_process("child")

        _, final_run = await _run_iterator_process(
            sqlite_storage, invoker, decision_engine, emitter,
            parent_proc, child_proc,
            work_item={"items": ["x", "y", "z"]},
        )

        # All items processed correctly with renewal task running
        assert final_run.status == RunStatus.COMPLETED
        results = final_run.work_item_state.get("results")
        assert isinstance(results, list)
        assert len(results) == 3
        assert [r["_item_value"] for r in results] == ["x", "y", "z"]
