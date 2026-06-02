"""Tests for iterator node validation and orchestrator wiring (US-002)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from roots.core.schema import (
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
    IteratorNodeConfig,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)
from roots.core.validator import validate_structure, validate_subprocess_references
from roots.events.types import EventType


# --- Helpers ---


def _make_iterator_process(
    proc_id: str,
    ref_id: str,
    extra_nodes: list[NodeDefinition] | None = None,
    extra_edges: list[EdgeDefinition] | None = None,
) -> ProcessDefinition:
    """Minimal process with one iterator node pointing to ref_id."""
    nodes: list[Any] = [
        NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id=ref_id,
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        ),
        NodeDefinition(
            id="done",
            type=NodeType.END,
            label="Done",
            config=EndNodeConfig(status=EndStatus.COMPLETED),
        ),
    ]
    edges: list[Any] = [EdgeDefinition(from_node="iter", to_node="done")]
    if extra_nodes:
        nodes.extend(extra_nodes)
    if extra_edges:
        edges.extend(extra_edges)
    return ProcessDefinition(
        id=proc_id,
        name="Test Process",
        version="1.0.0",
        entry_point="iter",
        nodes=nodes,
        edges=edges,
    )


# --- Iterator event types ---


class TestIteratorEventTypes:
    def test_iterator_started_exists(self) -> None:
        assert EventType.ITERATOR_STARTED == "roots.iterator.started"

    def test_iterator_item_completed_exists(self) -> None:
        assert EventType.ITERATOR_ITEM_COMPLETED == "roots.iterator.item.completed"

    def test_iterator_item_failed_exists(self) -> None:
        assert EventType.ITERATOR_ITEM_FAILED == "roots.iterator.item.failed"

    def test_iterator_completed_exists(self) -> None:
        assert EventType.ITERATOR_COMPLETED == "roots.iterator.completed"

    def test_iterator_failed_exists(self) -> None:
        assert EventType.ITERATOR_FAILED == "roots.iterator.failed"

    def test_all_five_iterator_event_types_present(self) -> None:
        iterator_events = [
            e for e in EventType if e.value.startswith("roots.iterator.")
        ]
        assert len(iterator_events) == 5


# --- Static self-reference check ---


class TestIteratorSelfReferenceCheck:
    def test_self_reference_raises_error(self) -> None:
        process = _make_iterator_process("proc-1", "proc-1")
        errors = validate_structure(process)
        assert any("circular" in e.lower() for e in errors)

    def test_self_reference_error_names_process(self) -> None:
        process = _make_iterator_process("proc-1", "proc-1")
        errors = validate_structure(process)
        matching = [e for e in errors if "proc-1" in e and "circular" in e.lower()]
        assert matching, f"Expected a circular reference error mentioning proc-1, got: {errors}"

    def test_external_reference_no_circular_error(self) -> None:
        process = _make_iterator_process("proc-1", "other-proc")
        errors = validate_structure(process)
        assert not any("circular" in e.lower() for e in errors)

    def test_non_iterator_nodes_not_checked(self) -> None:
        from roots.core.schema import AgentNodeConfig
        process = ProcessDefinition(
            id="proc-1",
            name="Test",
            version="1.0.0",
            entry_point="agent1",
            nodes=[
                NodeDefinition(
                    id="agent1",
                    type=NodeType.AGENT,
                    label="Agent",
                    config=AgentNodeConfig(agent="my-agent", output_key="out"),
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
        errors = validate_structure(process)
        assert not any("circular" in e.lower() for e in errors)


# --- Transitive cycle detection ---


@pytest.mark.asyncio
class TestValidateSubprocessReferences:
    async def test_no_iterators_returns_empty(self) -> None:
        process = ProcessDefinition(
            id="proc-1",
            name="Test",
            version="1.0.0",
            entry_point="done",
            nodes=[
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                )
            ],
            edges=[],
        )
        storage = AsyncMock()
        errors = await validate_subprocess_references(process, storage)
        assert errors == []

    async def test_iterator_to_nonexistent_process_no_error(self) -> None:
        process = _make_iterator_process("proc-a", "nonexistent")
        storage = AsyncMock()
        storage.get_process.return_value = None
        errors = await validate_subprocess_references(process, storage)
        assert errors == []

    async def test_direct_cycle_iterator_to_itself(self) -> None:
        # proc-a → proc-b → proc-a (cycle)
        proc_a = _make_iterator_process("proc-a", "proc-b")
        proc_b = _make_iterator_process("proc-b", "proc-a")

        async def _get_process(id: str) -> ProcessDefinition | None:
            if id == "proc-b":
                return proc_b
            return None

        storage = AsyncMock()
        storage.get_process.side_effect = _get_process

        errors = await validate_subprocess_references(proc_a, storage)
        assert len(errors) >= 1
        assert any("cycle" in e.lower() for e in errors)

    async def test_cycle_error_includes_chain(self) -> None:
        proc_a = _make_iterator_process("proc-a", "proc-b")
        proc_b = _make_iterator_process("proc-b", "proc-a")

        async def _get_process(id: str) -> ProcessDefinition | None:
            if id == "proc-b":
                return proc_b
            return None

        storage = AsyncMock()
        storage.get_process.side_effect = _get_process

        errors = await validate_subprocess_references(proc_a, storage)
        assert any("proc-a" in e and "proc-b" in e for e in errors)

    async def test_acyclic_chain_no_error(self) -> None:
        # proc-a → proc-b → proc-c (no cycle)
        proc_a = _make_iterator_process("proc-a", "proc-b")
        proc_b = _make_iterator_process("proc-b", "proc-c")
        proc_c = ProcessDefinition(
            id="proc-c",
            name="C",
            version="1.0.0",
            entry_point="done",
            nodes=[
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                )
            ],
            edges=[],
        )

        async def _get_process(id: str) -> ProcessDefinition | None:
            return {"proc-b": proc_b, "proc-c": proc_c}.get(id)

        storage = AsyncMock()
        storage.get_process.side_effect = _get_process

        errors = await validate_subprocess_references(proc_a, storage)
        assert errors == []

    async def test_three_node_cycle_detected(self) -> None:
        # proc-a → proc-b → proc-c → proc-a
        proc_a = _make_iterator_process("proc-a", "proc-b")
        proc_b = _make_iterator_process("proc-b", "proc-c")
        proc_c = _make_iterator_process("proc-c", "proc-a")

        async def _get_process(id: str) -> ProcessDefinition | None:
            return {"proc-b": proc_b, "proc-c": proc_c}.get(id)

        storage = AsyncMock()
        storage.get_process.side_effect = _get_process

        errors = await validate_subprocess_references(proc_a, storage)
        assert len(errors) >= 1
        assert any("cycle" in e.lower() for e in errors)


# --- Orchestrator dispatch ---


@pytest.mark.asyncio
class TestIteratorDispatch:
    async def test_iterator_in_dispatch_not_no_handler_error(self) -> None:
        """Iterator is wired in dispatch dict — raises not-implemented, not no-handler."""
        from roots.agents.invoker import AgentInvoker
        from roots.agents.registry import AgentRegistry
        from roots.core.decision import DecisionEngine
        from roots.core.orchestrator import OrchestrationError, ProcessRunner
        from roots.events.emitter import EventEmitter

        runner = ProcessRunner(
            run_id="test-run",
            storage=MagicMock(),
            agent_invoker=AgentInvoker(AgentRegistry()),
            decision_engine=DecisionEngine(default_model="gpt-4o-mini"),
            event_emitter=EventEmitter(),
            owner_id="test-owner",
        )

        node = NodeDefinition(
            id="iter",
            type=NodeType.ITERATOR,
            label="Iterator",
            config=IteratorNodeConfig(
                items_key="items",
                process_id="sub-proc",
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="results",
                item_key="item",
            ),
        )

        with pytest.raises(OrchestrationError) as exc_info:
            await runner._dispatch_node(node, {})

        assert "No handler" not in str(exc_info.value)
        assert "iter" in str(exc_info.value)


# --- start_run cycle validation ---


@pytest.mark.asyncio
class TestStartRunCycleValidation:
    async def test_start_run_raises_on_cycle(self, sqlite_storage: Any) -> None:
        from roots.agents.invoker import AgentInvoker
        from roots.agents.registry import AgentRegistry
        from roots.core.decision import DecisionEngine
        from roots.core.orchestrator import Orchestrator, OrchestrationError
        from roots.events.emitter import EventEmitter

        proc_a = _make_iterator_process("proc-a", "proc-b")
        proc_b = _make_iterator_process("proc-b", "proc-a")

        await sqlite_storage.save_process(proc_a)
        await sqlite_storage.save_process(proc_b)

        orchestrator = Orchestrator(
            storage=sqlite_storage,
            agent_invoker=AgentInvoker(AgentRegistry()),
            decision_engine=DecisionEngine(default_model="gpt-4o-mini"),
            event_emitter=EventEmitter(),
        )

        with pytest.raises(OrchestrationError, match="invalid subprocess references"):
            await orchestrator.start_run("proc-a", {"items": []})

    async def test_start_run_succeeds_without_cycle(self, sqlite_storage: Any) -> None:
        from roots.agents.invoker import AgentInvoker
        from roots.agents.registry import AgentRegistry
        from roots.core.decision import DecisionEngine
        from roots.core.orchestrator import Orchestrator
        from roots.events.emitter import EventEmitter

        proc_a = _make_iterator_process("proc-a", "proc-b")
        await sqlite_storage.save_process(proc_a)
        # proc-b is not saved (not found → no cycle error)

        orchestrator = Orchestrator(
            storage=sqlite_storage,
            agent_invoker=AgentInvoker(AgentRegistry()),
            decision_engine=DecisionEngine(default_model="gpt-4o-mini"),
            event_emitter=EventEmitter(),
        )

        run = await orchestrator.start_run("proc-a", {"items": []})
        assert run.process_id == "proc-a"


@pytest.fixture
async def sqlite_storage() -> Any:
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()
