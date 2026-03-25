"""Node Explorer Demo — custom API endpoints.

Adds step, reset, and tutorial endpoints to the demo FastAPI app
for the step-through execution experience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from roots import Roots
from roots.core.orchestrator import ProcessRunner

TUTORIAL_PATH = Path(__file__).resolve().parent / "tutorial_content.json"
PROCESS_ID = "node-explorer"
DEFAULT_WORK_ITEM: dict[str, Any] = {
    "title": "Sample Document",
    "type": "report",
    "content": "This is a sample work item for the Node Explorer tour.",
}


class StepRequest(BaseModel):
    run_id: str


class ResetRequest(BaseModel):
    process_id: str = PROCESS_ID


class StepResponse(BaseModel):
    run_id: str
    graph: dict[str, Any]


class ResetResponse(BaseModel):
    run_id: str


def _load_tutorial_content() -> dict[str, Any]:
    with open(TUTORIAL_PATH) as f:
        return json.load(f)


_tutorial_content: dict[str, Any] | None = None


def _get_tutorial() -> dict[str, Any]:
    global _tutorial_content  # noqa: PLW0603
    if _tutorial_content is None:
        _tutorial_content = _load_tutorial_content()
    return _tutorial_content


def add_node_explorer_routes(app: FastAPI) -> None:
    """Register step, reset, and tutorial routes on the app."""
    roots: Roots = app.state.roots

    @app.post("/api/step", response_model=StepResponse)
    async def step(body: StepRequest) -> StepResponse:
        """Advance one tick. Auto-resolves checkpoints before ticking."""
        run = await roots.storage.get_run(body.run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{body.run_id}' not found")

        # If paused at a checkpoint, auto-approve first
        if run.status == "paused":
            checkpoint = await roots.storage.get_pending_checkpoint(body.run_id)
            if checkpoint is not None:
                await roots.resolve_checkpoint(body.run_id, "approve")
                # Reload after resolution
                run = await roots.storage.get_run(body.run_id)
                if run is None:
                    raise HTTPException(status_code=404, detail="Run lost after checkpoint resolve")

        # Build a ProcessRunner and tick once
        orch = roots._orchestrator
        runner = ProcessRunner(
            run_id=body.run_id,
            storage=orch._storage,
            agent_invoker=orch._agent_invoker,
            decision_engine=orch._decision_engine,
            event_emitter=orch._event_emitter,
            owner_id=orch.owner_id,
        )
        await runner.tick()

        # Return updated graph
        graph = await roots.get_run_graph(body.run_id)
        return StepResponse(run_id=body.run_id, graph=graph)

    @app.post("/api/reset", response_model=ResetResponse)
    async def reset(body: ResetRequest) -> ResetResponse:
        """Create a fresh run with a default work item."""
        run = await roots.start_run(body.process_id, DEFAULT_WORK_ITEM)
        return ResetResponse(run_id=run.id)

    @app.get("/api/tutorial/{node_type}")
    async def tutorial(node_type: str) -> dict[str, Any]:
        """Return tutorial content for a node type."""
        content = _get_tutorial()
        if node_type not in content:
            raise HTTPException(
                status_code=404,
                detail=f"No tutorial content for '{node_type}'",
            )
        return content[node_type]
