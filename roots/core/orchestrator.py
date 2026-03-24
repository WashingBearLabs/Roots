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
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentOutput
from roots.core.decision import DecisionEngine
from roots.core.escalation import EscalationTrigger, create_escalation_from_error
from roots.core.schema import (
    AgentNodeConfig,
    AgentPoolNodeConfig,
    CheckpointNodeConfig,
    DecisionNodeConfig,
    EmitNodeConfig,
    EndNodeConfig,
    ExecutionMode,
    ForkNodeConfig,
    NodeDefinition,
    NodeType,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.types import EventEnvelope, EventType, create_event
from roots.core.retry import RetryExhaustedError, RetryRoutedError, execute_with_retry
from roots.storage.base import RunRecord, StorageBackend

logger = logging.getLogger(__name__)


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

            # Step 7: Write output to state if applicable
            if output is not None and hasattr(node.config, "output_key"):
                state[node.config.output_key] = output

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

            # Step 10: Emit node.completed with duration
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._event_emitter.emit(
                create_event(
                    EventType.NODE_COMPLETED,
                    run_id=self.run_id,
                    process_id=run.process_id,
                    node_id=node.id,
                    node_type=node.type.value,
                    duration_ms=elapsed_ms,
                )
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

        async def _invoke() -> AgentOutput:
            agent_input = AgentInput(
                work_item_state=state,
                node_config=node.config.model_dump(),
                run_id=self.run_id,
            )
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_INVOKED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": node.config.agent},
                )
            )
            result = await self._agent_invoker.invoke(
                node.config.agent, agent_input
            )
            self._event_emitter.emit(
                create_event(
                    EventType.AGENT_RETURNED,
                    run_id=self.run_id,
                    process_id=self._process_id or "",
                    node_id=node.id,
                    metadata={"agent": node.config.agent},
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
        results = await asyncio.gather(
            *[self._invoke_pool_agent(node, name, state) for name in agents],
            return_exceptions=True,
        )
        successful: list[AgentOutput] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Agent failed in pool '%s': %s", node.id, r)
            else:
                successful.append(r)
        if not successful:
            raise OrchestrationError(
                f"All {len(agents)} agents failed in pool '{node.id}'"
            )
        merged: dict[str, Any] = {}
        for r in successful:
            merged.update(r.output)
        # Check escalation on any successful result
        for r in successful:
            if r.escalate:
                await self._trigger_escalation(
                    node, state, r.escalation_reason or "Agent requested escalation"
                )
                break
        return merged

    async def _pool_sequential(
        self,
        node: NodeDefinition,
        agents: list[str],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        current_state = dict(state)
        output: dict[str, Any] = {}
        for name in agents:
            result = await self._invoke_pool_agent(node, name, current_state)
            output.update(result.output)
            current_state.update(result.output)
            if result.escalate:
                await self._trigger_escalation(
                    node, state, result.escalation_reason or "Agent requested escalation"
                )
                break
        return output

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
        """
        self._escalated = True
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

    # --- Decision, Checkpoint, Emit, End Handlers (US-004) ---

    async def _handle_decision(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, DecisionNodeConfig)
        result = await self._decision_engine.evaluate(node, state)

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
        for i, edge in enumerate(edges):
            branch_state = copy.deepcopy(state)
            to_node = getattr(edge, "to_node", None)
            branches.append({
                "branch_id": f"branch-{i}",
                "entry_node_id": to_node,
                "state": branch_state,
            })

        # Look up the matching join node
        join_node_id = process.fork_join_map.get(node.id)

        # Store branches on the runner for later execution (US-002)
        self._fork_branches = branches
        self._fork_join_node_id = join_node_id

        return None

    async def _handle_join(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Fork/join execution in T2.2")


class Orchestrator:
    """Manages multiple ProcessRunners for concurrent run handling."""

    def __init__(
        self,
        storage: StorageBackend,
        agent_registry: AgentRegistry,
        decision_engine: DecisionEngine,
        event_emitter: EventEmitter,
        poll_interval: float = 1.0,
    ) -> None:
        self._storage = storage
        self._agent_registry = agent_registry
        self._agent_invoker = AgentInvoker(agent_registry)
        self._decision_engine = decision_engine
        self._event_emitter = event_emitter
        self._poll_interval = poll_interval
        self.owner_id = f"orchestrator-{uuid4()}"

    async def start_run(
        self, process_id: str, work_item: dict[str, Any]
    ) -> RunRecord:
        """Create a new run for a process with pending status."""
        process = await self._storage.get_process(process_id)
        if process is None:
            raise OrchestrationError(
                f"Process '{process_id}' not found"
            )
        run = await self._storage.create_run(process_id, work_item)
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
