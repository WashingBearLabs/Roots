"""FastAPI application factory."""

from __future__ import annotations

import os
import secrets
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from roots.api.deps import get_roots  # noqa: F401 — re-export for backwards compat
from roots.api.routers.agents import router as agents_router
from roots.api.routers.checkpoints import router as checkpoints_router
from roots.api.routers.decisions import router as decisions_router
from roots.api.routers.processes import router as processes_router
from roots.api.routers.runs import router as runs_router
from roots.api.routers.graph import router as graph_router
from roots.api.routers.webhooks import router as webhooks_router

if TYPE_CHECKING:
    from roots import Roots


def create_app(
    roots: "Roots",
    cors_origins: list[str] | None = None,
    api_key: str | None = None,
) -> FastAPI:
    """Create a configured FastAPI application.

    Args:
        roots: The Roots framework instance to expose via the API.
        cors_origins: Allowed CORS origins. Defaults to ["*"].
        api_key: Optional API key required on all data routes. Defaults to the
            ``ROOTS_API_KEY`` environment variable. When set, every request to a
            data route must send a matching ``X-API-Key`` header; ``/`` and
            ``/health`` remain open. When unset, the API is unauthenticated.

    Returns:
        A FastAPI application with all routers registered.
    """
    app = FastAPI(title="roots", version="0.1.0")

    app.state.roots = roots

    if api_key is None:
        api_key = os.environ.get("ROOTS_API_KEY") or None

    async def require_api_key(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        """Reject requests without a valid API key (no-op when auth is disabled)."""
        if api_key is None:
            return
        if x_api_key is None or not secrets.compare_digest(x_api_key, api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "X-API-Key"},
            )

    auth = [Depends(require_api_key)] if api_key is not None else []

    # CORS: credentials are disabled, so wildcard origins cannot leak cookies.
    # Authentication (when enabled) is via the X-API-Key header, not cookies.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(processes_router, dependencies=auth)
    app.include_router(runs_router, dependencies=auth)
    app.include_router(checkpoints_router, dependencies=auth)
    app.include_router(agents_router, dependencies=auth)
    app.include_router(webhooks_router, dependencies=auth)
    app.include_router(graph_router, dependencies=auth)
    app.include_router(decisions_router, dependencies=auth)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "roots", "version": "0.1.0"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
