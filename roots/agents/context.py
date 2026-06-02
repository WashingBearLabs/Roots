"""Agent context — controlled access to Roots operations for local agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roots.storage.base import RunRecord

if TYPE_CHECKING:
    from roots import Roots


class AgentContext:
    """Provides agents with controlled access to Roots orchestration operations.

    Exposes a limited subset of the Roots API — run lifecycle operations only.
    Admin/mutation methods (load_process, register_agent, etc.) are not exposed.

    The ``run_id`` is the current run this agent is executing in; it is
    available for future lock management (US-004) without polluting the
    constructor signature later.
    """

    def __init__(self, roots: Roots, run_id: str) -> None:
        self._roots = roots
        self._run_id = run_id

    async def start_run(
        self,
        process_id: str,
        work_item: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Create and return a new run for a process."""
        return await self._roots.start_run(process_id, work_item, metadata=metadata)

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Get a run by ID."""
        return await self._roots.get_run(run_id)

    async def execute_run(self, run_id: str) -> RunRecord:
        """Execute a run to completion or pause.

        Blocks until the child run reaches a terminal or paused state.

        Returns the final ``RunRecord`` if the run completed or paused.
        Raises ``OrchestrationError`` if the child run fails, including the
        run ID and final status in the message.
        """
        from roots.core.orchestrator import OrchestrationError

        await self._roots.execute_run(run_id)
        run = await self._roots.get_run(run_id)
        if run is None:
            raise OrchestrationError(f"Run '{run_id}' not found after execution")
        if run.status == "failed":
            raise OrchestrationError(
                f"Child run '{run_id}' failed (status={run.status!r})"
            )
        return run

    async def resolve_checkpoint(
        self,
        run_id: str,
        decision: str,
        notes: str | None = None,
    ) -> None:
        """Resolve a pending checkpoint or escalation."""
        await self._roots.resolve_checkpoint(run_id, decision, notes=notes)
