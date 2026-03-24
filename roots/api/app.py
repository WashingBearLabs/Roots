"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from roots.api.deps import get_roots  # noqa: F401 — re-export for backwards compat
from roots.api.routers.agents import router as agents_router
from roots.api.routers.checkpoints import router as checkpoints_router
from roots.api.routers.processes import router as processes_router
from roots.api.routers.runs import router as runs_router
from roots.api.routers.graph import router as graph_router
from roots.api.routers.webhooks import router as webhooks_router

if TYPE_CHECKING:
    from roots import Roots


def create_app(roots: "Roots") -> FastAPI:
    """Create a configured FastAPI application.

    Args:
        roots: The Roots framework instance to expose via the API.

    Returns:
        A FastAPI application with all routers registered.
    """
    app = FastAPI(title="roots", version="0.1.0")

    app.state.roots = roots

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(processes_router)
    app.include_router(runs_router)
    app.include_router(checkpoints_router)
    app.include_router(agents_router)
    app.include_router(webhooks_router)
    app.include_router(graph_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "roots", "version": "0.1.0"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
