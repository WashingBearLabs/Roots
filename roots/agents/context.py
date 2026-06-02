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

    Parameters
    ----------
    roots:
        The Roots instance to delegate to.
    run_id:
        The current run this agent is executing in.
    owner_id:
        Lock owner identifier for the current run. Used by execute_run to
        release the parent run lock before child execution and reacquire it
        after. Pass the orchestrator's owner_id when invoking via ProcessRunner.
        Empty string means no lock management (standalone / testing).
    max_depth:
        Maximum nesting depth for execute_run chains. Defaults to 5.
    """

    def __init__(
        self,
        roots: Roots,
        run_id: str,
        *,
        owner_id: str = "",
        max_depth: int = 5,
    ) -> None:
        self._roots = roots
        self._run_id = run_id
        self._owner_id = owner_id
        self._max_depth = max_depth

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

        Enforces a depth limit on nested calls. When an owner_id is set,
        releases the parent run lock before child execution and reacquires it
        after — matching the subprocess handler lock pattern.

        Raises ``OrchestrationError`` if:
        - The nesting depth limit is reached.
        - The child run fails.
        - The parent run lock cannot be reacquired after child execution
          (lock was stolen while child was running).
        """
        from roots.core.orchestrator import OrchestrationError

        # Read current depth from parent run's persisted state
        parent_state = await self._roots.storage.get_work_item_state(self._run_id)
        current_depth = int(parent_state.get("_subprocess_depth", 0))

        if current_depth >= self._max_depth:
            raise OrchestrationError(
                f"Subprocess depth limit reached: current depth {current_depth} "
                f">= max {self._max_depth}"
            )

        # Propagate depth into child run's state
        child_state = await self._roots.storage.get_work_item_state(run_id)
        child_state["_subprocess_depth"] = current_depth + 1
        await self._roots.storage.update_work_item_state(run_id, child_state)

        # Release parent run lock before child execution (when lock owner is known)
        if self._owner_id:
            await self._roots.storage.release_run_lock(self._run_id, self._owner_id)

        try:
            await self._roots.execute_run(run_id)
        finally:
            # Always attempt to reacquire parent lock when lock owner is known
            if self._owner_id:
                acquired = await self._roots.storage.acquire_run_lock(
                    self._run_id, self._owner_id
                )
                if not acquired:
                    raise OrchestrationError(
                        f"Failed to reacquire parent run lock '{self._run_id}' "
                        f"after child execution — lock was stolen"
                    )

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
