"""ProcessRunner — tick-based execution loop for Roots orchestration."""

from __future__ import annotations

import logging
import time
from typing import Any

from roots.agents.invoker import AgentInvoker
from roots.agents.types import AgentInput
from roots.core.decision import DecisionEngine
from roots.core.schema import (
    AgentNodeConfig,
    AgentPoolNodeConfig,
    NodeDefinition,
    NodeType,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.types import EventType, create_event
from roots.storage.base import StorageBackend

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
            output = await self._dispatch_node(node, state)

            # Step 7: Write output to state if applicable
            if output is not None and hasattr(node.config, "output_key"):
                state[node.config.output_key] = output

            # Step 8: Determine next node
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

    # --- Node Handlers (stubs for US-003/US-004, minimal for tick tests) ---

    async def _handle_agent(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, AgentNodeConfig)
        agent_input = AgentInput(
            work_item_state=state,
            node_config=node.config.model_dump(),
            run_id=self.run_id,
        )
        result = await self._agent_invoker.invoke(
            node.config.agent, agent_input
        )
        return result.output

    async def _handle_agent_pool(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert isinstance(node.config, AgentPoolNodeConfig)
        raise NotImplementedError("Agent pool handler in US-003")

    async def _handle_decision(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Decision handler in US-004")

    async def _handle_checkpoint(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Checkpoint handler in US-004")

    async def _handle_emit(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Emit handler in US-004")

    async def _handle_end(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        return None

    async def _handle_fork(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Fork/join execution in T2.2")

    async def _handle_join(
        self, node: NodeDefinition, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Fork/join execution in T2.2")
