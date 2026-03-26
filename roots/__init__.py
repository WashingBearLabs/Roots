"""Roots — A process orchestration framework."""

from __future__ import annotations

import re
from typing import Any, Callable

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.agents.mcp_gateway import MCPGateway  # noqa: F401
from roots.agents.types import AgentRegistration, AgentType
from roots.core.checkpoint import ResolutionDecision, ResolutionError, resolve_pending
from roots.core.decision import DecisionEngine
from roots.core.llm import LLMCompletionFunc, LLMConfig, LLMResponse
from roots.core.orchestrator import Orchestrator, OrchestrationError
from roots.core.validator import load_process_yaml
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink, StdoutSink, FileSink, HttpSink
from roots.events.types import EventType, create_event  # noqa: F401
from roots.storage.base import RunRecord, StorageBackend
from roots.storage.postgres import PostgresBackend
from roots.storage.sqlite import SqliteBackend

__version__ = "0.1.0"

__all__ = [
    "Roots",
    "LLMConfig",
    "LLMCompletionFunc",
    "LLMResponse",
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
        default_model: str = "gpt-4o-mini",
        llm_callable: LLMCompletionFunc | None = None,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.storage = storage
        self._agent_registry = AgentRegistry()
        self._mcp_gateway = MCPGateway()
        self._agent_invoker = AgentInvoker(
            self._agent_registry, mcp_gateway=self._mcp_gateway
        )
        self._decision_engine = DecisionEngine(
            default_model=default_model,
            llm_callable=llm_callable,
            llm_config=llm_config,
        )
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

        Delegates to ``checkpoint.resolve_pending`` which handles all
        resolution logic (approve / reject / redirect).

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

        try:
            resolution_decision = ResolutionDecision(decision)
        except ValueError:
            raise OrchestrationError(
                f"Invalid decision '{decision}'. Must be 'approve', 'reject', or 'redirect'."
            )

        # When approving an escalation without an AI recommendation and no
        # explicit redirect_to, derive the target from the first outbound edge
        # so that callers don't need to supply redirect_to for simple cases.
        effective_redirect = redirect_to
        if resolution_decision == ResolutionDecision.APPROVE and effective_redirect is None:
            escalation = await self.storage.get_pending_escalation(run_id)
            checkpoint = await self.storage.get_pending_checkpoint(run_id)
            has_ai_rec = (
                checkpoint is not None
                and checkpoint.ai_recommendation is not None
            )
            if escalation is not None and not has_ai_rec:
                node_id = escalation.node_id
                outbound = process.get_outbound_edges(node_id)
                if outbound:
                    first_edge = outbound[0]
                    effective_redirect = getattr(first_edge, "to_node", None) or getattr(
                        first_edge, "target", None
                    )

        try:
            await resolve_pending(
                storage=self.storage,
                run_id=run_id,
                decision=resolution_decision,
                process=process,
                emitter=self._event_emitter,
                notes=notes,
                redirect_to=effective_redirect,
            )
        except ResolutionError as exc:
            raise OrchestrationError(str(exc)) from exc

    async def list_installed_packages(self) -> list[Any]:
        """List all installed packages with wiring status.

        Returns a list of InstalledPackage objects.
        """
        from roots.packaging.tracker import list_installed_packages as _list

        return await _list(self.storage, self._agent_registry)

    async def get_package_status(self, package_id: str) -> Any:
        """Get detailed status for a specific installed package.

        Returns a PackageStatus object, or None if not found.
        """
        from roots.packaging.tracker import get_package_status as _status

        return await _status(package_id, self.storage, self._agent_registry)

    async def uninstall_package(
        self,
        package_id: str,
        force: bool = False,
    ) -> bool:
        """Uninstall a package by removing its process from storage.

        Returns True if found and removed. Raises ValueError if active runs exist.
        """
        from roots.packaging.tracker import uninstall_package as _uninstall

        return await _uninstall(package_id, self.storage, force=force)

    async def install_package(
        self,
        archive_path: str | Any,
        force: bool = False,
        apply_defaults: bool = False,
    ) -> Any:
        """Install a .root package: load, save process, and validate contracts.

        Returns a ContractReport indicating which agents are satisfied/missing.
        """
        from pathlib import Path as _Path

        from roots.packaging.installer import install_package as _install
        from roots.packaging.installer import ContractReport, load_package
        from roots.packaging.defaults import load_defaults

        path = _Path(archive_path)
        _manifest, _process, report = await _install(
            archive_path=path,
            storage=self.storage,
            registry=self._agent_registry,
            force=force,
        )

        if apply_defaults:
            _, _, contents = load_package(path)
            load_defaults(contents, _manifest, self)

            # Re-validate contracts after loading defaults
            from roots.packaging.installer import validate_contracts

            report = validate_contracts(_manifest, self._agent_registry)

        return report

    def pack_process(
        self,
        process_path: str,
        output_path: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Pack a process YAML into a distributable .root package.

        Delegates to ``packaging.pack.pack_process``. If this Roots instance
        has registered agents, their schemas are used to enrich contracts.
        """
        from roots.packaging.pack import pack_process as _pack

        result = _pack(
            process_path=process_path,
            output_path=output_path,
            **kwargs,
        )
        return result

    async def close(self) -> None:
        """Drain pending events, close MCP connections, and close storage."""
        await self._event_emitter.close()
        await self._mcp_gateway.close()
        await self._agent_invoker.close()
        await self.storage.close()

    async def __aenter__(self) -> Roots:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
