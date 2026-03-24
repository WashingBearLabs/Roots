"""Roots — A process orchestration framework."""

from __future__ import annotations

import re
from typing import Any, Callable

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.agents.mcp_gateway import MCPGateway  # noqa: F401
from roots.agents.types import AgentRegistration, AgentType
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import Orchestrator, OrchestrationError
from roots.core.validator import load_process_yaml
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink, StdoutSink, FileSink, HttpSink
from roots.events.types import EventType, create_event
from roots.storage.base import RunRecord, StorageBackend
from roots.storage.postgres import PostgresBackend
from roots.storage.sqlite import SqliteBackend

__version__ = "0.1.0"

__all__ = [
    "Roots",
    "SqliteBackend",
    "PostgresBackend",
    "StdoutSink",
    "FileSink",
    "HttpSink",
]


class Roots:
    """Main entry point for the Roots orchestration framework.

    Wires together storage, agents, decisions, and events into a single
    object with a minimal public API.
    """

    def __init__(
        self,
        storage: StorageBackend,
        event_sinks: list[EventSink] | None = None,
        default_model: str = "openai/gpt-4o-mini",
    ) -> None:
        self.storage = storage
        self._agent_registry = AgentRegistry()
        self._mcp_gateway = MCPGateway()
        self._agent_invoker = AgentInvoker(
            self._agent_registry, mcp_gateway=self._mcp_gateway
        )
        self._decision_engine = DecisionEngine(default_model=default_model)
        self._event_emitter = EventEmitter(sinks=event_sinks or [])
        self._orchestrator = Orchestrator(
            storage,
            self._agent_registry,
            self._decision_engine,
            self._event_emitter,
        )

    async def load_process(self, path: str) -> None:
        """Parse a YAML process definition and save it to storage."""
        process = load_process_yaml(path)
        await self.storage.save_process(process)

    async def register_agent(
        self,
        name: str,
        callable: Callable[..., Any],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a local agent callable."""
        self._agent_registry.register_local(
            name, callable, input_schema=input_schema, output_schema=output_schema
        )

    async def register_mcp_server(
        self,
        url: str | None = None,
        command: list[str] | None = None,
        tool_filter: list[str] | None = None,
        name_prefix: str = "mcp",
    ) -> list[str]:
        """Register an MCP server and auto-discover its tools as agents.

        Connects to the server, discovers tools, and registers each as an
        MCP agent in the registry.

        Args:
            url: URL for SSE/HTTP-based MCP server.
            command: Command for stdio-based MCP server.
            tool_filter: If provided, only register tools whose names are in this list.
            name_prefix: Prefix for generated agent names (default: "mcp").

        Returns:
            List of registered agent names.
        """
        if url is not None and command is not None:
            raise ValueError(
                "Provide exactly one of url or command, not both"
            )
        if url is None and command is None:
            raise ValueError(
                "Provide exactly one of url or command"
            )

        if url is not None:
            connection = await self._mcp_gateway.connect_url(url)
        else:
            assert command is not None
            connection = await self._mcp_gateway.connect_command(command)

        tools = await self._mcp_gateway.discover_tools(connection)

        registered_names: list[str] = []
        for tool in tools:
            tool_name = tool["name"]

            if tool_filter is not None and tool_name not in tool_filter:
                continue

            sanitized = re.sub(r"[^a-zA-Z0-9]", "_", tool_name)
            agent_name = f"{name_prefix}_{sanitized}"

            registration = AgentRegistration(
                name=agent_name,
                agent_type=AgentType.MCP,
                mcp_tool_name=tool_name,
                mcp_server_url=url,
                mcp_server_command=command,
                input_schema=tool.get("input_schema"),
            )
            self._agent_registry.register(registration)
            registered_names.append(agent_name)

        return registered_names

    async def start_run(
        self, process_id: str, work_item: dict[str, Any]
    ) -> RunRecord:
        """Create a new run for a process."""
        return await self._orchestrator.start_run(process_id, work_item)

    async def execute_run(self, run_id: str) -> None:
        """Execute a run to completion."""
        await self._orchestrator.execute_run(run_id)

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Get a run by ID."""
        return await self.storage.get_run(run_id)

    async def get_run_graph(self, run_id: str) -> dict[str, Any]:
        """Build a headless graph JSON structure for a run.

        Loads process + run in 2 queries, then history events in a 3rd.
        Node positions default to {"x": 0, "y": 0} until a visual editor
        writes them via the mutation API.
        """
        # Query 1: load run
        run = await self.storage.get_run(run_id)
        if run is None:
            raise OrchestrationError(f"Run '{run_id}' not found")

        # Query 2: load process
        process = await self.storage.get_process(run.process_id)
        if process is None:
            raise OrchestrationError(
                f"Process '{run.process_id}' not found"
            )

        # Query 3: load history events
        history = await self.storage.list_history_events(run_id)

        # Build lookup structures from history
        node_events: dict[str, list[dict[str, Any]]] = {}
        for evt in history:
            if evt.node_id is not None:
                node_events.setdefault(evt.node_id, []).append(
                    {"event_type": evt.event_type, "created_at": evt.created_at.isoformat()}
                )

        # Derive node statuses
        nodes = []
        for node_def in process.nodes:
            evts = node_events.get(node_def.id, [])
            evt_types = {e["event_type"] for e in evts}

            # Current node + paused/running takes priority over history
            if run.current_node_id == node_def.id and run.status == "paused":
                status = "paused"
            elif run.current_node_id == node_def.id and run.status == "running":
                status = "running"
            elif "completed" in evt_types:
                status = "completed"
            elif "failed" in evt_types:
                status = "failed"
            elif not evts:
                status = "pending"
            else:
                status = "running"

            started_at = None
            completed_at = None
            for e in evts:
                if e["event_type"] == "entered" and started_at is None:
                    started_at = e["created_at"]
                if e["event_type"] == "completed":
                    completed_at = e["created_at"]

            metadata = node_def.metadata or {}
            position = metadata.get("position", {"x": 0, "y": 0})

            nodes.append({
                "id": node_def.id,
                "type": node_def.type.value,
                "label": node_def.label,
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
                "position": position,
                "metadata": metadata,
            })

        # Derive edge statuses
        edges = []
        nodes_with_events = set(node_events.keys())
        for edge_def in process.edges:
            traversed = (
                edge_def.from_node in nodes_with_events
                and edge_def.to_node in nodes_with_events
            )
            edges.append({
                "id": edge_def.id,
                "from": edge_def.from_node,
                "to": edge_def.to_node,
                "condition": edge_def.condition,
                "status": "traversed" if traversed else "pending",
                "label": edge_def.label,
            })

        return {
            "process_id": run.process_id,
            "run_id": run_id,
            "run_status": run.status,
            "nodes": nodes,
            "edges": edges,
        }

    async def resolve_checkpoint(
        self,
        run_id: str,
        decision: str,
        notes: str | None = None,
        redirect_to: str | None = None,
    ) -> None:
        """Resolve a pending checkpoint or escalation.

        Args:
            run_id: The run with a pending checkpoint/escalation.
            decision: One of "approve", "reject", or "redirect".
            notes: Optional resolution notes.
            redirect_to: Required node ID when decision is "redirect".
        """
        run = await self.storage.get_run(run_id)
        if run is None:
            raise OrchestrationError(f"Run '{run_id}' not found")

        process = await self.storage.get_process(run.process_id)
        if process is None:
            raise OrchestrationError(
                f"Process '{run.process_id}' not found"
            )

        # Find pending checkpoint or escalation
        checkpoint = await self.storage.get_pending_checkpoint(run_id)
        escalation = await self.storage.get_pending_escalation(run_id)

        if checkpoint is None and escalation is None:
            raise OrchestrationError(
                f"No pending checkpoint or escalation for run '{run_id}'"
            )

        resolution = {"decision": decision, "notes": notes}

        if decision == "approve":
            # Resolve the record
            if checkpoint:
                await self.storage.resolve_checkpoint(checkpoint.id, resolution)
                node_id = checkpoint.node_id
            else:
                assert escalation is not None
                await self.storage.resolve_escalation(escalation.id, resolution)
                node_id = escalation.node_id

            # Determine next node
            node = process.get_node(node_id)
            if node is None:
                raise OrchestrationError(
                    f"Node '{node_id}' not found in process"
                )
            outbound = process.get_outbound_edges(node_id)
            if not outbound:
                raise OrchestrationError(
                    f"No outbound edges from node '{node_id}'"
                )
            first_edge = outbound[0]
            next_node = getattr(first_edge, "to_node", None) or getattr(
                first_edge, "target", None
            )

            await self.storage.update_run_status(
                run_id, "running", next_node
            )
            self._event_emitter.emit(
                create_event(
                    EventType.CHECKPOINT_RESOLVED,
                    run_id=run_id,
                    process_id=run.process_id,
                    node_id=node_id,
                    metadata={"decision": "approve"},
                )
            )

        elif decision == "reject":
            if checkpoint:
                await self.storage.resolve_checkpoint(checkpoint.id, resolution)
                node_id = checkpoint.node_id
            else:
                assert escalation is not None
                await self.storage.resolve_escalation(escalation.id, resolution)
                node_id = escalation.node_id

            await self.storage.update_run_status(run_id, "failed")
            self._event_emitter.emit(
                create_event(
                    EventType.CHECKPOINT_RESOLVED,
                    run_id=run_id,
                    process_id=run.process_id,
                    node_id=node_id,
                    metadata={"decision": "reject"},
                )
            )

        elif decision == "redirect":
            if redirect_to is None:
                raise OrchestrationError(
                    "redirect_to is required when decision is 'redirect'"
                )
            target_node = process.get_node(redirect_to)
            if target_node is None:
                raise OrchestrationError(
                    f"Redirect target '{redirect_to}' is not a valid node"
                )

            if checkpoint:
                await self.storage.resolve_checkpoint(checkpoint.id, resolution)
                node_id = checkpoint.node_id
            else:
                assert escalation is not None
                await self.storage.resolve_escalation(escalation.id, resolution)
                node_id = escalation.node_id

            await self.storage.update_run_status(
                run_id, "running", redirect_to
            )
            self._event_emitter.emit(
                create_event(
                    EventType.CHECKPOINT_RESOLVED,
                    run_id=run_id,
                    process_id=run.process_id,
                    node_id=node_id,
                    metadata={"decision": "redirect", "redirect_to": redirect_to},
                )
            )

        else:
            raise OrchestrationError(
                f"Invalid decision '{decision}'. Must be 'approve', 'reject', or 'redirect'."
            )

    async def close(self) -> None:
        """Drain pending events, close MCP connections, and close storage."""
        await self._event_emitter.close()
        await self._mcp_gateway.close()
        await self.storage.close()

    async def __aenter__(self) -> Roots:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
