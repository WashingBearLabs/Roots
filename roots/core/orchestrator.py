"""ProcessRunner and Orchestrator for Roots orchestration."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from roots.agents.invoker import AgentInvoker, AgentSchemaValidationError
from roots.agents.types import AgentInput, AgentOutput
from roots.core.aggregation import AggregationError, aggregate_votes
from roots.core.decision import DecisionEngine
from roots.core.escalation import EscalationTrigger, create_escalation_from_error
from roots.core.schema import (
    VOTE_AGGREGATIONS,
    AgentNodeConfig,
    AgentPoolNodeConfig,
    CheckpointNodeConfig,
    DecisionNodeConfig,
    EmitNodeConfig,
    EndNodeConfig,
    ExecutionMode,
    ForkNodeConfig,
    JoinNodeConfig,
    MergeStrategy,
    NodeDefinition,
    NodeType,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.types import EventEnvelope, EventType, create_event
from roots.core.retry import RetryExhaustedError, RetryRoutedError, execute_with_retry
from roots.storage.base import BranchResult, RunRecord, StorageBackend, StorageError

logger = logging.getLogger(__name__)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge override into base. Returns a new dict.

    - Recursively merges nested dicts
    - Non-dict values: last writer wins (override replaces base)
    - Lists: override replaces (not concatenation)
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class OrchestrationError(Exception):
    """Raised when an orchestration operation fails."""


class ProcessRunner:
    """Executes one tick per call — load state, execute node, write state.

    Each tick is crash-safe: state is loaded fresh from storage, a single
    node is executed, and updated state is persisted atomically.
    """

    def __init__(
        self,
        run_id: str,
        storage: StorageBackend,
        agent_invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        event_emitter: EventEmitter,
        owner_id: str,
    ) -> None:
        self.run_id = run_id
        self._storage = storage
        self._agent_invoker = agent_invoker
        self._decision_engine = decision_engine
        self._event_emitter = event_emitter
        self._owner_id = owner_id
        self._process_id: str | None = None
        self._escalated: bool = False
        self._decision_next_node: str | None = None
        self._join_metadata: dict[str, Any] | None = None
        # Fork/join state (only valid within execute_run, not tick_all polling)
        self._fork_branches: list[dict[str, Any]] = []
        self._fork_join_node_id: str | None = None
        self._fork_branch_results: list[Any] | None = None
        self._in_fork_branch: bool = False

    async def tick(self) -> bool:
        """Execute one node and return True if the run should continue."""
        try:
            # Step 1: Acquire lock
            locked = await self._storage.acquire_run_lock(
                self.run_id, self._owner_id
            )
            if not locked:
                return False

            # Step 2: Load run from storage (fresh each tick)
            run = await self._storage.get_run(self.run_id)
            if run is None:
                await self._storage.release_run_lock(self.run_id, self._owner_id)
                return False

            self._process_id = run.process_id

            # Pending → Running transition on first tick
            if run.status == RunStatus.PENDING:
                process = await self._storage.get_process(run.process_id)
                if process is None:
                    await self._storage.release_run_lock(
                        self.run_id, self._owner_id
                    )
                    return False
                entry = run.current_node_id or process.entry_point
                await self._storage.update_run_status(
                    self.run_id, RunStatus.RUNNING, entry
                )
                self._event_emitter.emit(create_event(
                    EventType.RUN_STARTED,
                    run_id=self.run_id,
                    process_id=run.process_id,
                ))
                # Reload run after transition
                run = await self._storage.get_run(self.run_id)
                if run is None:
                    await self._storage.release_run_lock(
                        self.run_id, self._owner_id
                    )
                    return False

            if run.status != RunStatus.RUNNING:
                await self._storage.release_run_lock(self.run_id, self._owner_id)
                return False

            # Step 3: Load process definition
            if run.process_version is not None:
                process = await self._storage.get_process_version(
                    run.process_id, run.process_version
                )
                if process is None:
                    await self._storage.release_run_lock(
                        self.run_id, self._owner_id
                    )
                    raise OrchestrationError(
                        f"Pinned version '{run.process_version}' for process "
                        f"'{run.process_id}' not found — version may have been "
                        f"deleted after run creation"
                    )
            else:
                process = await self._storage.get_process(run.process_id)
            if process is None:
                await self._storage.release_run_lock(self.run_id, self._owner_id)
                return False

            # Step 4: Get current node
            node = process.get_node(run.current_node_id)  # type: ignore[arg-type]
            if node is None:
                await self._storage.release_run_lock(self.run_id, self._owner_id)
                raise OrchestrationError(
                    f"Node '{run.current_node_id}' not found in process "
                    f"'{process.id}'"
                )

            # Step 5: Emit node.entered + history
            start_time = time.monotonic()
            self._event_emitter.emit(
                create_event(
                    EventType.NODE_ENTERED,
                    run_id=self.run_id,
                    process_id=run.process_id,
                    node_id=node.id,
                    node_type=node.type.value,
                )
            )
            await self._storage.append_history_event(
                self.run_id, "entered", node.id, {}
            )

            # Step 6: Call appropriate handler
            state = dict(run.work_item_state)
            self._escalated = False
            self._decision_next_node = None

            try:
                output = await self._dispatch_node(node, state)
            except RetryRoutedError as exc:
                # Retry exhaustion with on_exhaustion=route:
                # mark node failed, route to fallback edge, keep running
                await self._storage.append_history_event(
                    self.run_id, "failed", node.id,
                    {"error": exc.last_error, "attempts": exc.max_attempts},
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        metadata={
                            "error": exc.last_error,
                            "attempts": exc.max_attempts,
                            "fallback": True,
                            "fallback_edge": exc.fallback_edge,
                        },
                    )
                )
                await self._storage.update_run_atomically(
                    self.run_id,
                    work_item_state=state,
                    status=RunStatus.RUNNING,
                    current_node_id=exc.fallback_edge,
                )
                return True
            except RetryExhaustedError as exc:
                # Retry exhaustion with on_exhaustion=fail:
                # mark node failed, set run to failed, emit events
                await self._storage.append_history_event(
                    self.run_id, "failed", node.id,
                    {"error": exc.last_error, "attempts": exc.max_attempts},
                )
                await self._storage.update_run_atomically(
                    self.run_id,
                    work_item_state=state,
                    status=RunStatus.FAILED,
                    current_node_id=node.id,
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        metadata={
                            "error": exc.last_error,
                            "attempts": exc.max_attempts,
                        },
                    )
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.RUN_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        metadata={
                            "error": exc.last_error,
                            "reason": "retry_exhausted",
                        },
                    )
                )
                return False
            except AggregationError as exc:
                await self._storage.append_history_event(
                    self.run_id, "failed", node.id,
                    {"error": str(exc)},
                )
                await self._storage.update_run_atomically(
                    self.run_id,
                    work_item_state=state,
                    status=RunStatus.FAILED,
                    current_node_id=node.id,
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        metadata={"error": str(exc), "reason": "aggregation_failed"},
                    )
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.RUN_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        metadata={"error": str(exc), "reason": "aggregation_failed"},
                    )
                )
                return False

            # Step 7: Write output to state if applicable
            if output is not None:
                output_key: str | None = getattr(node.config, "output_key", None)
                if output_key is not None:
                    state[output_key] = output

            # Step 7b: Check error_key — agent returned OK but output
            # contains an application-level error the handler didn't raise
            error_key: str | None = getattr(node.config, "error_key", None)
            if (
                error_key is not None
                and output is not None
                and output.get(error_key)
            ):
                error_value = output[error_key]
                await self._storage.append_history_event(
                    self.run_id, "failed", node.id,
                    {"error_key": error_key, "error": str(error_value)},
                )
                await self._storage.update_run_atomically(
                    self.run_id,
                    work_item_state=state,
                    status=RunStatus.FAILED,
                    current_node_id=node.id,
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        metadata={
                            "error_key": error_key,
                            "error": str(error_value),
                            "reason": "error_key_detected",
                        },
                    )
                )
                self._event_emitter.emit(
                    create_event(
                        EventType.RUN_FAILED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                        metadata={
                            "error_key": error_key,
                            "reason": "error_key_detected",
                        },
                    )
                )
                return False

            # Step 8: Determine next node (or pause if escalated)
            if self._escalated:
                next_node_id = run.current_node_id
                status = RunStatus.PAUSED
            elif self._decision_next_node is not None:
                next_node_id = self._decision_next_node
                status = RunStatus.RUNNING
            else:
                next_node_id, status = self._resolve_next(node, output, process)

            # Step 9: Persist atomically
            await self._storage.update_run_atomically(
                self.run_id,
                work_item_state=state,
                status=status,
                current_node_id=next_node_id,
            )
            await self._storage.append_history_event(
                self.run_id, "completed", node.id, output or {}
            )

            # Emit RUN_PAUSED when run transitions to paused
            if status == RunStatus.PAUSED:
                self._event_emitter.emit(
                    create_event(
                        EventType.RUN_PAUSED,
                        run_id=self.run_id,
                        process_id=run.process_id,
                        node_id=node.id,
                    )
                )

            # Step 10: Emit node.completed with duration
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            node_completed_meta = self._join_metadata
            self._join_metadata = None
            completed_kwargs: dict[str, Any] = {
                "run_id": self.run_id,
                "process_id": run.process_id,
                "node_id": node.id,
                "node_type": node.type.value,
                "duration_ms": elapsed_ms,
            }
            if node_completed_meta is not None:
                completed_kwargs["metadata"] = node_completed_meta
            self._event_emitter.emit(
                create_event(EventType.NODE_COMPLETED, **completed_kwargs)
            )

            # Continue if still running
            return status == RunStatus.RUNNING

        finally:
            # Step 11: Always release lock
            try:
                await self._storage.release_run_lock(self.run_id, self._owner_id)
            except Exception:
                logger.warning(
                    "Failed to release lock for run %s", self.run_id,
                    exc_info=True,
                )

    async def run_to_completion(self) -> None:
        """Loop ticks until the run is done."""
        while await self.tick():
            pass

    async def _dispatch_node(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Dispatch to the appropriate handler based on node type."""
        handlers = {
            NodeType.AGENT: self._handle_agent,
            NodeType.AGENT_POOL: self._handle_agent_pool,
            NodeType.DECISION: self._handle_decision,
            NodeType.CHECKPOINT: self._handle_checkpoint,
            NodeType.EMIT: self._handle_emit,
            NodeType.END: self._handle_end,
            NodeType.FORK: self._handle_fork,
            NodeType.JOIN: self._handle_join,
        }
        handler = handlers.get(node.type)
        if handler is None:
            raise OrchestrationError(
                f"No handler for node type '{node.type}'"
            )
        return await handler(node, state)

    def _resolve_next(
        self,
        node: NodeDefinition,
        output: dict[str, Any] | None,
        process: Any,
    ) -> tuple[str | None, str]:
        """Determine next node ID and run status after node execution.

        Returns:
            Tuple of (next_node_id, status).
        """
        # End nodes: terminal status, no next node
        if node.type == NodeType.END:
            from roots.core.schema import EndNodeConfig
            assert isinstance(node.config, EndNodeConfig)
            terminal = (
                RunStatus.COMPLETED
                if node.config.status.value == "completed"
                else RunStatus.FAILED
            )
            return None, terminal

        # For non-terminal nodes: follow first outbound edge
        edges = process.get_outbound_edges(node.id)
        if not edges:
            raise OrchestrationError(
                f"Node '{node.id}' has no outbound edges"
            )
        # EdgeDefinition uses to_node, DecisionEdge uses target
        first_edge = edges[0]
        next_id = getattr(first_edge, "to_node", None) or getattr(
            first_edge, "target", None
        )
        return next_id, RunStatus.RUNNING

    # --- Node Handlers ---

    async def _handle_agent(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, AgentNodeConfig)
        config = node.config

        async def _invoke() -> AgentOutput:
            agent_input = AgentInput(
                work_item_state=state,
                node_config=config.model_dump(mode="json"),
                run_id=self.run_id,
            )
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_INVOKED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": config.agent},
                )
            )
            result = await self._agent_invoker.invoke(
                config.agent, agent_input
            )
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_RETURNED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": config.agent},
                )
            )
            return result

        try:
            result = await execute_with_retry(
                node=node,
                execute_fn=_invoke,
                storage=self._storage,
                run_id=self.run_id,
                emitter=self._event_emitter,
                process_id=self._process_id or "",
            )
        except AgentSchemaValidationError as e:
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_FAILED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": config.agent, "error": str(e)},
                )
            )
            await self._trigger_escalation(
                node, state, str(e),
                EscalationTrigger.SCHEMA_VALIDATION_FAILURE,
            )
            return None
        if result.escalate:
            await self._trigger_escalation(
                node, state,
                result.escalation_reason or "Agent requested escalation",
                EscalationTrigger.AGENT_EXPLICIT_SIGNAL,
            )
        return result.output

    async def _handle_agent_pool(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, AgentPoolNodeConfig)
        config = node.config
        agents = config.agents

        try:
            if config.execution_mode == ExecutionMode.PARALLEL:
                return await self._pool_parallel(node, agents, state)
            elif config.execution_mode == ExecutionMode.SEQUENTIAL:
                return await self._pool_sequential(node, agents, state)
            else:  # FIRST_PASS
                return await self._pool_first_pass(node, agents, state)
        except AgentSchemaValidationError as e:
            await self._trigger_escalation(
                node, state, str(e),
                EscalationTrigger.SCHEMA_VALIDATION_FAILURE,
            )
            return None

    async def _invoke_pool_agent(
        self, node: NodeDefinition, agent_name: str, state: dict[str, Any]
    ) -> AgentOutput:
        """Invoke a single agent within a pool, with retry support."""

        async def _invoke() -> AgentOutput:
            agent_input = AgentInput(
                work_item_state=state,
                node_config=node.config.model_dump(),  # type: ignore[union-attr]
                run_id=self.run_id,
            )
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_INVOKED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": agent_name},
                )
            )
            result = await self._agent_invoker.invoke(agent_name, agent_input)
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_RETURNED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": agent_name},
                )
            )
            return result

        result = await execute_with_retry(
            node=node,
            execute_fn=_invoke,
            storage=self._storage,
            run_id=self.run_id,
            emitter=self._event_emitter,
            process_id=self._process_id or "",
        )
        return result

    async def _pool_parallel(
        self,
        node: NodeDefinition,
        agents: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        assert isinstance(node.config, AgentPoolNodeConfig)
        config = node.config
        pool_node_id = node.id
        stale_timeout_seconds = 300

        # Crash recovery: check for already-completed agents in storage
        existing_results = await self._storage.get_branch_results(self.run_id, pool_node_id)
        completed_by_branch_id: dict[str, Any] = {}
        for br in existing_results:
            if br.status == "completed":
                completed_by_branch_id[br.branch_id] = br.result_json

        # Determine which agents still need execution (skip completed, retry failed)
        pending_agents: list[str] = [
            name for name in agents
            if f"agent:{name}" not in completed_by_branch_id
        ]

        async def _invoke_with_persistence(name: str) -> AgentOutput:
            storage_branch_id = f"agent:{name}"
            try:
                result = await self._invoke_pool_agent(node, name, copy.deepcopy(state))
            except Exception as exc:
                try:
                    await self._storage.save_branch_result(
                        self.run_id, pool_node_id, storage_branch_id,
                        "failed", str(exc),
                    )
                except Exception:
                    pass
                raise
            result_dict: dict[str, Any] = {
                "output": result.output,
                "escalate": result.escalate,
                "escalation_reason": result.escalation_reason,
            }
            await self._storage.save_branch_result(
                self.run_id, pool_node_id, storage_branch_id,
                "completed", result_dict,
            )
            return result

        lock_stolen = False
        agent_tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(_invoke_with_persistence(name))
            for name in pending_agents
        ]

        async def _renewal_loop() -> None:
            nonlocal lock_stolen
            interval = max(1, stale_timeout_seconds // 2)
            try:
                while True:
                    await asyncio.sleep(interval)
                    await self._storage.release_run_lock(self.run_id, self._owner_id)
                    acquired = await self._storage.acquire_run_lock(
                        self.run_id, self._owner_id,
                    )
                    if not acquired:
                        lock_stolen = True
                        for task in agent_tasks:
                            task.cancel()
                        return
            except asyncio.CancelledError:
                pass

        renewal_task = asyncio.create_task(_renewal_loop())
        try:
            fresh_results: list[Any] = await asyncio.gather(
                *agent_tasks, return_exceptions=True
            )
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass

        if lock_stolen:
            raise OrchestrationError("Lock lost during parallel execution")

        # Reconstruct named_successful in original agent order
        fresh_by_name: dict[str, Any] = dict(zip(pending_agents, fresh_results))
        named_successful: list[tuple[str, AgentOutput]] = []
        for name in agents:
            storage_branch_id = f"agent:{name}"
            if storage_branch_id in completed_by_branch_id:
                stored = completed_by_branch_id[storage_branch_id]
                recovered = AgentOutput(
                    output=stored["output"],
                    escalate=stored.get("escalate", False),
                    escalation_reason=stored.get("escalation_reason"),
                )
                named_successful.append((name, recovered))
            else:
                r = fresh_by_name[name]
                if isinstance(r, BaseException):
                    logger.warning("Agent failed in pool '%s': %s", node.id, r)
                else:
                    named_successful.append((name, r))

        if not named_successful:
            raise OrchestrationError(
                f"All {len(agents)} agents failed in pool '{node.id}'"
            )
        for _, r in named_successful:
            if r.escalate:
                await self._trigger_escalation(
                    node, state, r.escalation_reason or "Agent requested escalation"
                )
                break
        if config.aggregation in VOTE_AGGREGATIONS:
            assert config.vote_config is not None
            agents_outputs = [(name, r.output) for name, r in named_successful]
            final_result = aggregate_votes(agents_outputs, config.aggregation, config.vote_config)
        else:
            merged: dict[str, Any] = {}
            for _, r in named_successful:
                merged.update(r.output)
            final_result = merged

        # Clear branch results after successful pool completion only
        await self._storage.clear_branch_results(self.run_id, pool_node_id)
        return final_result

    async def _pool_sequential(
        self,
        node: NodeDefinition,
        agents: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        assert isinstance(node.config, AgentPoolNodeConfig)
        config = node.config
        is_vote = config.aggregation in VOTE_AGGREGATIONS

        current_state = dict(state)
        named_outputs: list[tuple[str, dict[str, Any]]] = []
        for name in agents:
            result = await self._invoke_pool_agent(node, name, current_state)
            named_outputs.append((name, result.output))
            if not is_vote:
                current_state.update(result.output)
            if result.escalate:
                await self._trigger_escalation(
                    node, state, result.escalation_reason or "Agent requested escalation"
                )
                break
        if is_vote:
            assert config.vote_config is not None
            return aggregate_votes(named_outputs, config.aggregation, config.vote_config)
        merged: dict[str, Any] = {}
        for _, output in named_outputs:
            merged.update(output)
        return merged

    async def _pool_first_pass(
        self,
        node: NodeDefinition,
        agents: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        last_error: BaseException | None = None
        for name in agents:
            try:
                result = await self._invoke_pool_agent(node, name, state)
            except Exception as exc:
                last_error = exc
                logger.warning("Agent '%s' failed in first_pass pool '%s': %s", name, node.id, exc)
                continue
            if result.escalate:
                last_error = OrchestrationError(
                    f"Agent '{name}' escalated in pool '{node.id}'"
                )
                continue
            return result.output
        raise OrchestrationError(
            f"All agents failed in first_pass pool '{node.id}': {last_error}"
        )

    async def _trigger_escalation(
        self,
        node: NodeDefinition,
        state: dict[str, Any],
        reason: str,
        trigger: EscalationTrigger = EscalationTrigger.AGENT_EXPLICIT_SIGNAL,
    ) -> None:
        """Flag escalation and create an escalation record.

        The tick loop reads ``_escalated`` and writes PAUSED status atomically.
        If a pending escalation already exists (StorageError on duplicate), the
        creation is skipped — the run was already escalated on the prior attempt.
        """
        self._escalated = True
        try:
            await create_escalation_from_error(
                storage=self._storage,
                run_id=self.run_id,
                node_id=node.id,
                trigger=trigger,
                reason=reason,
                work_item_state=state,
                emitter=self._event_emitter,
                process_id=self._process_id or "",
            )
        except StorageError:
            pass

    # --- Decision, Checkpoint, Emit, End Handlers (US-004) ---

    async def _handle_decision(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, DecisionNodeConfig)

        history: list[dict[str, Any]] | None = None
        if node.config.history_depth is not None:
            raw_records = await self._storage.list_decisions(
                process_id=self._process_id or "",
                node_id=node.id,
                limit=node.config.history_depth,
            )
            history = [
                {
                    "selected_edge": r.decision.get("selected_edge"),
                    "confidence": r.confidence,
                    "reasoning": r.decision.get("reasoning") or (
                        r.decision.get("ai_recommendation") or {}
                    ).get("reasoning"),
                    "mode": r.mode,
                }
                for r in raw_records
            ]

        result = await self._decision_engine.evaluate(node, state, history=history)

        # Record decision to storage
        record = result.to_decision_record(state)
        await self._storage.append_decision(
            run_id=self.run_id,
            process_id=self._process_id or "",
            node_id=node.id,
            mode=record["mode"],
            input_state=record["input_state_snapshot"],
            decision=record,
            confidence=record["confidence"],
        )

        # Emit decision.evaluated before escalated/taken branch
        self._event_emitter.emit(
            create_event(
                EventType.DECISION_EVALUATED,
                run_id=self.run_id,
                process_id=self._process_id or "",
                node_id=node.id,
                metadata={
                    "mode": record["mode"],
                    "selected_edge": result.selected_edge,
                    "confidence": result.confidence,
                    "escalated": result.escalated,
                },
            )
        )

        if result.escalated:
            # Create checkpoint record for escalation
            await self._storage.create_checkpoint(
                self.run_id,
                node.id,
                "escalation",
                node.config.checkpoint_prompt or "AI decision requires review",
                result.ai_recommendation.model_dump()
                if result.ai_recommendation
                else None,
            )
            # Create escalation record with confidence trigger
            await self._trigger_escalation(
                node, state,
                "AI confidence below threshold",
                EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD,
            )
            self._event_emitter.emit(
                create_event(
                    EventType.DECISION_ESCALATED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                )
            )
            self._event_emitter.emit(
                create_event(
                    EventType.CHECKPOINT_REACHED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                )
            )
            return None

        # Non-escalated: emit decision.taken and route to selected edge
        self._event_emitter.emit(
            create_event(
                EventType.DECISION_TAKEN,
                run_id=self.run_id,
                process_id=self._process_id or "",
                node_id=node.id,
                metadata={"selected_edge": result.selected_edge},
            )
        )
        self._decision_next_node = result.selected_edge
        return None

    async def _handle_checkpoint(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, CheckpointNodeConfig)
        await self._storage.create_checkpoint(
            self.run_id,
            node.id,
            "planned",
            node.config.prompt,
        )
        self._escalated = True
        self._event_emitter.emit(
            create_event(
                EventType.CHECKPOINT_REACHED,
                run_id=self.run_id,
                process_id=self._process_id or "",
                node_id=node.id,
            )
        )
        return None

    async def _handle_emit(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, EmitNodeConfig)
        payload_metadata: dict[str, Any] = {}
        for key in node.config.payload_keys:
            if key in state:
                payload_metadata[key] = state[key]
        self._event_emitter.emit(
            EventEnvelope(
                event=node.config.event_type,
                timestamp=datetime.now(timezone.utc),
                run_id=self.run_id,
                process_id=self._process_id or "",
                node_id=node.id,
                metadata=payload_metadata,
            )
        )
        return None

    async def _handle_end(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, EndNodeConfig)
        if node.config.status.value == "completed":
            self._event_emitter.emit(
                create_event(
                    EventType.RUN_COMPLETED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                )
            )
        else:
            self._event_emitter.emit(
                create_event(
                    EventType.RUN_FAILED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                )
            )
        return None

    async def _handle_fork(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, ForkNodeConfig)

        # Runtime guard: defense in depth against programmatic process construction
        # that bypasses schema validation. The schema validator catches nested forks
        # in YAML/dict-defined processes, but a ProcessDefinition built in code can
        # reach _handle_fork with a nested fork without ever calling validate_structure.
        if self._in_fork_branch:
            raise OrchestrationError(
                f"Nested fork/join is not supported — fork node '{node.id}' "
                f"was encountered inside a fork branch"
            )

        # Load process to get outbound edges and fork_join_map
        process = await self._storage.get_process(self._process_id or "")
        if process is None:
            raise OrchestrationError(
                f"Process '{self._process_id}' not found"
            )

        edges = process.get_outbound_edges(node.id)
        if not edges:
            raise OrchestrationError(
                f"Fork node '{node.id}' has no outbound edges"
            )

        # Create branch contexts with deep copies of state
        branches: list[dict[str, Any]] = []
        target_node_ids: list[str | None] = []
        for i, edge in enumerate(edges):
            branch_state = copy.deepcopy(state)
            to_node = getattr(edge, "to_node", None)
            target_node_ids.append(to_node)
            branches.append({
                "branch_id": f"branch-{i}",
                "entry_node_id": to_node,
                "state": branch_state,
            })

        # Look up the matching join node
        join_node_id = process.fork_join_map.get(node.id)

        # Store branches and join node on the runner
        self._fork_branches = branches
        self._fork_join_node_id = join_node_id

        fork_node_id = node.id
        stale_timeout_seconds = 300

        # Crash recovery: check for already-completed branches in storage
        existing_results = await self._storage.get_branch_results(
            self.run_id, fork_node_id
        )
        completed_by_storage_id: dict[str, Any] = {}
        for br in existing_results:
            if br.status == "completed":
                completed_by_storage_id[br.branch_id] = br.result_json

        # Determine which branches still need execution
        # Completed branches are skipped; failed branches are re-executed
        pending_indices: list[int] = []
        pending_branches: list[dict[str, Any]] = []
        pending_targets: list[str | None] = []
        for i, (ctx, tgt) in enumerate(zip(branches, target_node_ids)):
            storage_branch_id = f"branch:{tgt}"
            if storage_branch_id not in completed_by_storage_id:
                pending_indices.append(i)
                pending_branches.append(ctx)
                pending_targets.append(tgt)

        async def _execute_with_persistence(
            ctx: dict[str, Any], target_node_id: str | None
        ) -> dict[str, Any]:
            storage_branch_id = f"branch:{target_node_id}"
            try:
                result = await self._execute_branch(ctx, join_node_id, process)
            except Exception as exc:
                try:
                    await self._storage.save_branch_result(
                        self.run_id, fork_node_id, storage_branch_id,
                        "failed", str(exc),
                    )
                except Exception:
                    pass
                raise
            await self._storage.save_branch_result(
                self.run_id, fork_node_id, storage_branch_id,
                "completed", result,
            )
            return result

        lock_stolen = False
        branch_tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(_execute_with_persistence(ctx, tgt))
            for ctx, tgt in zip(pending_branches, pending_targets)
        ]

        async def _renewal_loop() -> None:
            nonlocal lock_stolen
            interval = max(1, stale_timeout_seconds // 2)
            try:
                while True:
                    await asyncio.sleep(interval)
                    await self._storage.release_run_lock(
                        self.run_id, self._owner_id
                    )
                    acquired = await self._storage.acquire_run_lock(
                        self.run_id, self._owner_id,
                    )
                    if not acquired:
                        lock_stolen = True
                        for task in branch_tasks:
                            task.cancel()
                        return
            except asyncio.CancelledError:
                pass

        renewal_task = asyncio.create_task(_renewal_loop())
        try:
            fresh_results: list[Any] = await asyncio.gather(
                *branch_tasks, return_exceptions=True
            )
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass

        if lock_stolen:
            raise OrchestrationError("Lock lost during parallel execution")

        # Assemble final results in original branch order
        # (recovered completed results + fresh execution results)
        all_results: list[Any] = [None] * len(branches)
        for i, tgt in enumerate(target_node_ids):
            storage_branch_id = f"branch:{tgt}"
            if storage_branch_id in completed_by_storage_id:
                all_results[i] = completed_by_storage_id[storage_branch_id]
        for pos, original_i in enumerate(pending_indices):
            all_results[original_i] = fresh_results[pos]

        # Store branch results for the join node
        self._fork_branch_results = all_results

        # Route to join node so tick() advances past the fork
        self._decision_next_node = join_node_id

        return None

    async def _execute_branch(
        self,
        branch_context: dict[str, Any],
        join_node_id: str | None,
        process: Any,
    ) -> dict[str, Any]:
        """Execute a single branch's mini execution loop.

        Runs nodes sequentially from the branch's entry node until the
        join node is reached (join node is NOT executed). Returns the
        branch's final accumulated state dict.
        """
        branch_id = branch_context["branch_id"]
        current_node_id = branch_context["entry_node_id"]
        state = branch_context["state"]
        start_time = time.monotonic()

        if join_node_id is None:
            raise OrchestrationError(
                f"join_node_id is None for branch '{branch_id}' — "
                "fork/join pairing may be broken"
            )

        self._in_fork_branch = True
        try:
            visited: set[str] = set()
            iterations = 0
            max_iterations = 1000

            while current_node_id != join_node_id:
                if current_node_id in visited:
                    raise OrchestrationError(
                        f"Cycle detected in fork branch '{branch_id}' "
                        f"at node '{current_node_id}'"
                    )
                if iterations >= max_iterations:
                    raise OrchestrationError(
                        f"Branch '{branch_id}' exceeded {max_iterations} iterations"
                    )
                visited.add(current_node_id)
                iterations += 1
                node = process.get_node(current_node_id)
                if node is None:
                    raise OrchestrationError(
                        f"Node '{current_node_id}' not found in process "
                        f"'{process.id}' (branch {branch_id})"
                    )

                # Emit node.entered with branch_id
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_ENTERED,
                        run_id=self.run_id,
                        process_id=self._process_id or "",
                        node_id=node.id,
                        node_type=node.type.value,
                        metadata={"branch_id": branch_id},
                    )
                )

                # Execute the node handler
                node_start = time.monotonic()
                output = await self._dispatch_node(node, state)

                # Write output to state if applicable
                if output is not None and hasattr(node.config, "output_key"):
                    state[node.config.output_key] = output  # type: ignore[union-attr]

                # Emit node.completed with branch_id and duration
                elapsed_ms = int((time.monotonic() - node_start) * 1000)
                self._event_emitter.emit(
                    create_event(
                        EventType.NODE_COMPLETED,
                        run_id=self.run_id,
                        process_id=self._process_id or "",
                        node_id=node.id,
                        node_type=node.type.value,
                        duration_ms=elapsed_ms,
                        metadata={"branch_id": branch_id},
                    )
                )

                # Advance to next node via edge evaluation
                edges = process.get_outbound_edges(node.id)
                if not edges:
                    raise OrchestrationError(
                        f"Node '{node.id}' has no outbound edges "
                        f"(branch {branch_id})"
                    )
                next_id = getattr(edges[0], "to_node", None)
                current_node_id = next_id

            # Record branch timing
            branch_context["duration_ms"] = int(
                (time.monotonic() - start_time) * 1000
            )

            return state
        finally:
            self._in_fork_branch = False

    async def _handle_join(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, JoinNodeConfig)

        results = getattr(self, "_fork_branch_results", None)
        branches: list[dict[str, Any]] = getattr(self, "_fork_branches", [])

        # Load process to resolve fork node ID (needed for storage operations)
        process = await self._storage.get_process(self._process_id or "")
        if process is None:
            raise OrchestrationError(
                f"Process '{self._process_id}' not found"
            )
        fork_node_id: str | None = next(
            (fid for fid, jid in process.fork_join_map.items() if jid == node.id),
            None,
        )

        if results is None:
            # Crash recovery: _fork_branch_results not in memory.
            # Load branch results from storage (crash happened after fork, at join).
            if fork_node_id is None:
                raise OrchestrationError(
                    f"Join node '{node.id}' reached without branch results "
                    f"and has no matching fork in fork_join_map"
                )
            stored = await self._storage.get_branch_results(
                self.run_id, fork_node_id
            )
            if not stored:
                raise OrchestrationError(
                    f"Join node '{node.id}' reached without branch results"
                )
            # Reconstruct branches and results in original edge order
            fork_edges = process.get_outbound_edges(fork_node_id)
            stored_map: dict[str, BranchResult] = {
                br.branch_id: br for br in stored
            }
            recovered_branches: list[dict[str, Any]] = []
            recovered_results: list[Any] = []
            for i, edge in enumerate(fork_edges):
                to_node = getattr(edge, "to_node", None)
                storage_branch_id = f"branch:{to_node}"
                recovered_branches.append({
                    "branch_id": f"branch-{i}",
                    "entry_node_id": to_node,
                    "state": {},
                })
                br = stored_map.get(storage_branch_id)
                if br is None:
                    recovered_results.append(RuntimeError(
                        f"Branch '{to_node}' result not found in storage"
                    ))
                elif br.status == "completed":
                    recovered_results.append(br.result_json)
                else:
                    recovered_results.append(RuntimeError(str(br.result_json)))
            branches = recovered_branches
            results = recovered_results
            self._fork_branches = branches
            self._fork_branch_results = results

        # Classify results into successes and failures
        successful: list[tuple[int, dict[str, Any]]] = []
        failed: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            branch_meta: dict[str, Any] = branches[i] if i < len(branches) else {}
            branch_id: str = branch_meta.get("branch_id", f"branch-{i}")
            entry_node: str | None = branch_meta.get("entry_node_id")
            if isinstance(result, BaseException):
                failed.append({
                    "branch_id": branch_id,
                    "entry_node": entry_node,
                    "error_message": str(result),
                })
            else:
                successful.append((i, result))

        # All branches failed — always fail regardless of allow_partial
        if failed and not successful:
            self._event_emitter.emit(
                create_event(
                    EventType.RUN_FAILED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"failed_branches": failed},
                )
            )
            raise OrchestrationError(
                f"All branches failed in fork/join at '{node.id}'"
            )

        # Some branches failed
        if failed:
            if not node.config.allow_partial:
                # Fail the entire run
                self._event_emitter.emit(
                    create_event(
                        EventType.RUN_FAILED,
                        run_id=self.run_id,
                        process_id=self._process_id or "",
                        node_id=node.id,
                        metadata={"failed_branches": failed},
                    )
                )
                raise OrchestrationError(
                    f"Branch failure in fork/join at '{node.id}': "
                    f"{failed[0]['branch_id']} — {failed[0]['error_message']}"
                )

            # allow_partial=True: record failed branches in state
            state["_failed_branches"] = failed
            # Signal partial completion for node.completed event metadata
            self._join_metadata = {
                "partial_completion": True,
                "failed_branches": len(failed),
                "successful_branches": len(successful),
            }

        if node.config.merge_strategy == MergeStrategy.MERGE_ALL:
            merged: dict[str, Any] = {}
            for _, result in successful:
                merged = deep_merge(merged, result)
            state.update(merged)
        elif node.config.merge_strategy == MergeStrategy.COLLECT:
            collected: list[dict[str, Any]] = []
            for i, result in enumerate(results):
                branch_meta_c: dict[str, Any] = branches[i] if i < len(branches) else {}
                branch_id = branch_meta_c.get("branch_id", f"branch-{i}")
                entry_node = branch_meta_c.get("entry_node_id")
                if isinstance(result, BaseException):
                    if node.config.allow_partial:
                        collected.append({
                            "branch_id": branch_id,
                            "entry_node": entry_node,
                            "state": None,
                            "error": str(result),
                        })
                    # Skip failed branches when allow_partial is False
                else:
                    collected.append({
                        "branch_id": branch_id,
                        "entry_node": entry_node,
                        "state": result,
                    })
            state[node.config.collect_key] = collected  # type: ignore[index]

        # Successful join: clear branch results from storage so they do not
        # accumulate. Failed joins leave results intact for recovery.
        if fork_node_id is not None:
            await self._storage.clear_branch_results(self.run_id, fork_node_id)

        return None


class Orchestrator:
    """Manages multiple ProcessRunners for concurrent run handling."""

    def __init__(
        self,
        storage: StorageBackend,
        agent_invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        event_emitter: EventEmitter,
        poll_interval: float = 1.0,
    ) -> None:
        self._storage = storage
        self._agent_invoker = agent_invoker
        self._decision_engine = decision_engine
        self._event_emitter = event_emitter
        self._poll_interval = poll_interval
        self.owner_id = f"orchestrator-{uuid4()}"

    async def start_run(
        self,
        process_id: str,
        work_item: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Create a new run for a process with pending status."""
        process = await self._storage.get_process(process_id)
        if process is None:
            raise OrchestrationError(
                f"Process '{process_id}' not found"
            )
        run = await self._storage.create_run(
            process_id, work_item, process.version, metadata=metadata
        )
        await self._storage.update_run_status(
            run.id, RunStatus.PENDING, process.entry_point
        )
        # Reload to get updated current_node_id
        updated = await self._storage.get_run(run.id)
        if updated is None:
            raise OrchestrationError(
                f"Run '{run.id}' not found after creation"
            )
        return updated

    async def tick_all(self) -> None:
        """Tick all pending and running runs concurrently."""
        pending = await self._storage.list_runs(status=RunStatus.PENDING)
        running = await self._storage.list_runs(status=RunStatus.RUNNING)
        active_runs = pending + running

        async def _tick_run(run: RunRecord) -> None:
            runner = ProcessRunner(
                run_id=run.id,
                storage=self._storage,
                agent_invoker=self._agent_invoker,
                decision_engine=self._decision_engine,
                event_emitter=self._event_emitter,
                owner_id=self.owner_id,
            )
            await runner.tick()

        await asyncio.gather(
            *[_tick_run(r) for r in active_runs],
            return_exceptions=True,
        )

    async def run_loop(self) -> None:
        """Poll continuously, ticking all runs. Handles CancelledError for graceful shutdown."""
        try:
            while True:
                await self.tick_all()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            return

    async def execute_run(self, run_id: str) -> None:
        """Run a single run to completion (embedded/synchronous mode)."""
        runner = ProcessRunner(
            run_id=run_id,
            storage=self._storage,
            agent_invoker=self._agent_invoker,
            decision_engine=self._decision_engine,
            event_emitter=self._event_emitter,
            owner_id=self.owner_id,
        )
        await runner.run_to_completion()
