"""Tests for agent and agent_pool node handlers (US-003, US-004)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import OrchestrationError, ProcessRunner
from roots.core.schema import (
    Aggregation,
    AgentNodeConfig,
    AgentPoolNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
    VoteConfig,
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


async def slow_pool_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that sleeps 0.1s — used for lock renewal timing tests."""
    await asyncio.sleep(0.1)
    return {"output": {"slow": True}, "escalate": False}


async def vote_yes_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"decision": "yes"}, "escalate": False}


async def vote_no_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"decision": "no"}, "escalate": False}


async def abstain_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {}, "escalate": False}


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
    reg.register_local("slow_pool", slow_pool_agent)
    reg.register_local("vote_yes", vote_yes_agent)
    reg.register_local("vote_no", vote_no_agent)
    reg.register_local("abstain", abstain_agent)
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


# --- Vote Aggregation Tests ---


def make_vote_pool_process(
    agents: list[str],
    execution_mode: ExecutionMode,
    aggregation: Aggregation,
    vote_config: VoteConfig,
) -> ProcessDefinition:
    return ProcessDefinition(
        id="vote-pool-proc",
        name="Vote Pool Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="pool1",
                type=NodeType.AGENT_POOL,
                label="Pool 1",
                config=AgentPoolNodeConfig(
                    agents=agents,
                    execution_mode=execution_mode,
                    aggregation=aggregation,
                    output_key="pool_result",
                    vote_config=vote_config,
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


class TestAgentPoolVoteAggregation:
    async def test_parallel_majority_vote_wins(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["vote_yes", "vote_yes", "vote_no"],
            ExecutionMode.PARALLEL,
            Aggregation.MAJORITY_VOTE,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        result = run.work_item_state["pool_result"]
        assert result["winning_value"] == "yes"
        assert result["strategy"] == "majority_vote"
        assert result["participating_agents"] == 3

    async def test_sequential_majority_vote_wins(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["vote_yes", "vote_yes", "vote_no"],
            ExecutionMode.SEQUENTIAL,
            Aggregation.MAJORITY_VOTE,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        result = run.work_item_state["pool_result"]
        assert result["winning_value"] == "yes"
        assert result["strategy"] == "majority_vote"

    async def test_parallel_unanimous_vote_wins(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["vote_yes", "vote_yes"],
            ExecutionMode.PARALLEL,
            Aggregation.UNANIMOUS,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.work_item_state["pool_result"]["winning_value"] == "yes"

    async def test_parallel_weighted_vote_wins(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["vote_yes", "vote_no"],
            ExecutionMode.PARALLEL,
            Aggregation.WEIGHTED_VOTE,
            VoteConfig(vote_key="decision", weights={"vote_yes": 1.0, "vote_no": 3.0}),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        result = run.work_item_state["pool_result"]
        assert result["winning_value"] == "no"
        assert result["strategy"] == "weighted_vote"

    async def test_aggregation_error_fails_run_all_abstain_parallel(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["abstain", "abstain"],
            ExecutionMode.PARALLEL,
            Aggregation.MAJORITY_VOTE,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        result = await runner.tick()

        assert result is False
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "failed"

    async def test_aggregation_error_fails_run_unanimous_disagreement(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["vote_yes", "vote_no"],
            ExecutionMode.PARALLEL,
            Aggregation.UNANIMOUS,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        result = await runner.tick()

        assert result is False
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "failed"

    async def test_aggregation_error_fails_run_all_abstain_sequential(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_vote_pool_process(
            ["abstain", "abstain"],
            ExecutionMode.SEQUENTIAL,
            Aggregation.MAJORITY_VOTE,
            VoteConfig(vote_key="decision"),
        )
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        result = await runner.tick()

        assert result is False
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "failed"


# --- US-004 Parallel Agent Pool Persistence Tests ---


class TestAgentPoolParallelPersistence:
    """US-004: Crash-safe parallel agent_pool — persistence."""

    async def test_parallel_persists_each_agent_result(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each agent's result is saved to branch storage with stable agent: branch IDs."""
        proc = make_pool_process(["upper", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # Intercept clear so we can inspect results after tick completes
        saved: list[Any] = []
        orig_clear = sqlite_storage.clear_branch_results

        async def capturing_clear(rid: str, nid: str) -> None:
            results = await sqlite_storage.get_branch_results(rid, nid)
            saved.extend(results)
            return await orig_clear(rid, nid)

        sqlite_storage.clear_branch_results = capturing_clear  # type: ignore[method-assign]

        await runner.tick()  # pool node runs and clears on success

        branch_ids = {r.branch_id for r in saved}
        assert "agent:upper" in branch_ids
        assert "agent:reverse" in branch_ids
        assert all(r.status == "completed" for r in saved)

    async def test_result_round_trip_preserves_escalate_fields(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """BranchResult.result_json preserves escalate and escalation_reason fields."""
        proc = make_pool_process(["upper", "escalating"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # Capture results before clear (escalating agent triggers escalation,
        # but the pool still calls clear_branch_results after assembling results)
        saved: list[Any] = []
        orig_clear = sqlite_storage.clear_branch_results

        async def capturing_clear(rid: str, nid: str) -> None:
            results = await sqlite_storage.get_branch_results(rid, nid)
            saved.extend(results)
            return await orig_clear(rid, nid)

        sqlite_storage.clear_branch_results = capturing_clear  # type: ignore[method-assign]

        await runner.tick()

        by_branch = {r.branch_id: r for r in saved}

        upper_result = by_branch["agent:upper"]
        assert upper_result.status == "completed"
        assert upper_result.result_json["escalate"] is False
        assert upper_result.result_json["escalation_reason"] is None

        escalating_result = by_branch["agent:escalating"]
        assert escalating_result.status == "completed"
        assert escalating_result.result_json["escalate"] is True
        assert escalating_result.result_json["escalation_reason"] == "Agent needs human review"

    async def test_crash_recovery_skips_completed_agent(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Pre-seeded completed agent is skipped on re-entry."""
        call_log: list[str] = []

        async def tracking_upper(input: dict[str, Any]) -> dict[str, Any]:
            call_log.append("upper_called")
            text = input["work_item_state"].get("text", "")
            return {"output": {"upper": text.upper()}, "escalate": False}

        invoker._registry.register_local("tracking_upper", tracking_upper)
        invoker._registry.register_local("tracking_reverse", reverse_agent)

        proc = make_pool_process(["tracking_upper", "tracking_reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})

        # Pre-seed tracking_upper as already completed
        pre_seeded: dict[str, Any] = {
            "output": {"upper": "RECOVERED"},
            "escalate": False,
            "escalation_reason": None,
        }
        await sqlite_storage.save_branch_result(
            run_id, "pool1", "agent:tracking_upper", "completed", pre_seeded
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        # tracking_upper should NOT have been called (recovered from storage)
        assert "upper_called" not in call_log

        # Run should have completed using recovered result + fresh reverse result
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.work_item_state["pool_result"]["upper"] == "RECOVERED"

    async def test_clear_branch_results_after_successful_pool(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results are cleared from storage after successful pool completion."""
        proc = make_pool_process(["upper", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        after = await sqlite_storage.get_branch_results(run_id, "pool1")
        assert len(after) == 0

    async def test_branch_results_preserved_on_all_agents_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results are NOT cleared when the pool raises (all agents fail)."""
        proc = make_pool_process(["failing", "failing"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError):
            await runner.tick()

        stored = await sqlite_storage.get_branch_results(run_id, "pool1")
        assert len(stored) > 0
        assert all(r.status == "failed" for r in stored)

    async def test_lock_renewal_calls_release_and_acquire_during_gather(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Background renewal task calls release+acquire periodically during agent gather."""
        proc = make_pool_process(["slow_pool", "slow_pool"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        acquire_count = 0
        release_count = 0
        orig_acquire = sqlite_storage.acquire_run_lock
        orig_release = sqlite_storage.release_run_lock

        async def counting_acquire(rid: str, oid: str, stale_timeout: int = 300) -> bool:
            nonlocal acquire_count
            acquire_count += 1
            return await orig_acquire(rid, oid, stale_timeout)

        async def counting_release(rid: str, oid: str) -> None:
            nonlocal release_count
            release_count += 1
            return await orig_release(rid, oid)

        sqlite_storage.acquire_run_lock = counting_acquire  # type: ignore[method-assign]
        sqlite_storage.release_run_lock = counting_release  # type: ignore[method-assign]

        orig_sleep = asyncio.sleep

        async def fast_sleep(t: float) -> None:
            await orig_sleep(0 if t >= 100 else t)

        with patch("asyncio.sleep", fast_sleep):
            await runner.tick()

        # tick() acquires once; renewal fires and acquires again → at least 2 total
        assert acquire_count >= 2
        # renewal releases once; tick() finally releases once → at least 2 total
        assert release_count >= 2

    async def test_lock_stolen_raises_orchestration_error(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """When lock is stolen during pool gather, OrchestrationError is raised."""
        proc = make_pool_process(["slow_pool", "slow_pool"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        acquire_count = 0
        orig_acquire = sqlite_storage.acquire_run_lock

        async def stealing_acquire(rid: str, oid: str, stale_timeout: int = 300) -> bool:
            nonlocal acquire_count
            acquire_count += 1
            if acquire_count >= 2:
                return False
            return await orig_acquire(rid, oid, stale_timeout)

        sqlite_storage.acquire_run_lock = stealing_acquire  # type: ignore[method-assign]

        orig_sleep = asyncio.sleep

        async def fast_sleep(t: float) -> None:
            await orig_sleep(0 if t >= 100 else t)

        with patch("asyncio.sleep", fast_sleep):
            with pytest.raises(OrchestrationError, match="Lock lost during parallel execution"):
                await runner.tick()


# --- US-005 Crash Recovery Tests ---


class TestAgentPoolCrashRecovery:
    """US-005: Crash-safe parallel agent_pool — recovery."""

    async def test_failed_agent_retried_on_recovery(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed agents in storage are re-executed on recovery (not skipped like completed)."""
        call_log: list[str] = []

        async def tracking_upper(input: dict[str, Any]) -> dict[str, Any]:
            call_log.append("upper_called")
            text = input["work_item_state"].get("text", "")
            return {"output": {"upper": text.upper()}, "escalate": False}

        invoker._registry.register_local("tracking_upper_retry", tracking_upper)

        proc = make_pool_process(["tracking_upper_retry", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hello"})

        # Pre-seed tracking_upper_retry as FAILED (should be re-executed)
        await sqlite_storage.save_branch_result(
            run_id, "pool1", "agent:tracking_upper_retry", "failed", "previous transient error"
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        # Failed agent SHOULD have been called (re-executed, not skipped)
        assert "upper_called" in call_log

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.work_item_state["pool_result"]["upper"] == "HELLO"

    async def test_escalation_dedup_on_recovery(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """When escalating agent result is recovered from storage and a pending escalation
        already exists, StorageError from create_escalation is caught — run stays paused."""
        proc = make_pool_process(["escalating", "reverse"], ExecutionMode.PARALLEL)
        run_id = await _setup_run(sqlite_storage, proc, {"text": "hi"})

        # Pre-seed the escalating agent as already completed (from a prior run attempt)
        pre_seeded: dict[str, Any] = {
            "output": {"status": "needs_review"},
            "escalate": True,
            "escalation_reason": "Agent needs human review",
        }
        await sqlite_storage.save_branch_result(
            run_id, "pool1", "agent:escalating", "completed", pre_seeded
        )

        # Pre-create a pending escalation record (as if the prior run attempt already escalated)
        await sqlite_storage.create_escalation(
            run_id=run_id,
            node_id="pool1",
            trigger_type="agent_explicit_signal",
            reason="Agent needs human review",
            work_item_snapshot={},
        )

        # Now run (recovery): escalating agent comes from storage with escalate=True,
        # _trigger_escalation is called, StorageError should be caught not re-raised
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        # Should NOT raise StorageError or any other exception
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        # Run should be paused (escalated), not failed
        assert run.status == "paused"

    async def test_vote_aggregation_with_recovered_results(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Vote aggregation produces correct result when some votes are recovered from storage."""
        # Register uniquely-named vote agents so pre-seeding works with stable keys
        async def vote_yes_a(input: dict[str, Any]) -> dict[str, Any]:
            return {"output": {"decision": "yes"}, "escalate": False}

        async def vote_yes_b(input: dict[str, Any]) -> dict[str, Any]:
            return {"output": {"decision": "yes"}, "escalate": False}

        invoker._registry.register_local("vote_yes_a", vote_yes_a)
        invoker._registry.register_local("vote_yes_b", vote_yes_b)

        proc = ProcessDefinition(
            id="vote-recovery-proc",
            name="Vote Recovery Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="pool1",
                    type=NodeType.AGENT_POOL,
                    label="Pool 1",
                    config=AgentPoolNodeConfig(
                        agents=["vote_yes_a", "vote_yes_b", "vote_no"],
                        execution_mode=ExecutionMode.PARALLEL,
                        aggregation=Aggregation.MAJORITY_VOTE,
                        output_key="pool_result",
                        vote_config=VoteConfig(vote_key="decision"),
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
        run_id = await _setup_run(sqlite_storage, proc)

        # Pre-seed vote_yes_a as completed (recovered from storage)
        pre_seeded: dict[str, Any] = {
            "output": {"decision": "yes"},
            "escalate": False,
            "escalation_reason": None,
        }
        await sqlite_storage.save_branch_result(
            run_id, "pool1", "agent:vote_yes_a", "completed", pre_seeded
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        result = run.work_item_state["pool_result"]
        # 2 yes (vote_yes_a recovered + vote_yes_b fresh) vs 1 no → majority = "yes"
        assert result["winning_value"] == "yes"
        assert result["strategy"] == "majority_vote"
        assert result["participating_agents"] == 3
