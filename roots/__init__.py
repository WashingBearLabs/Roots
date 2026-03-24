"""Roots — A process orchestration framework."""

from __future__ import annotations

from typing import Any, Callable

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import Orchestrator
from roots.core.validator import load_process_yaml
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink, StdoutSink, FileSink, HttpSink
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
        self._agent_invoker = AgentInvoker(self._agent_registry)
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

    async def close(self) -> None:
        """Drain pending events and close storage."""
        await self._event_emitter.close()
        await self.storage.close()

    async def __aenter__(self) -> Roots:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
