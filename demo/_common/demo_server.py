"""Shared demo server infrastructure.

Creates a FastAPI app from a Roots instance with API routers,
static file serving, and browser auto-open.
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from roots.api.routers.agents import router as agents_router
from roots.api.routers.checkpoints import router as checkpoints_router
from roots.api.routers.graph import router as graph_router
from roots.api.routers.processes import router as processes_router
from roots.api.routers.runs import router as runs_router
from roots.api.routers.webhooks import router as webhooks_router

if TYPE_CHECKING:
    from roots import Roots

_COMMON_DIR = str(Path(__file__).resolve().parent)


def create_demo_app(
    roots: Roots,
    demo_name: str,
    static_dir: str,
    common_dir: str | None = None,
) -> FastAPI:
    """Create a FastAPI app wired to a Roots instance for demo use.

    Args:
        roots: The Roots framework instance.
        demo_name: Human-readable name for this demo.
        static_dir: Path to the demo-specific static files directory.
        common_dir: Path to shared assets (defaults to ``demo/_common/``).

    Returns:
        A configured FastAPI application.
    """
    app = FastAPI(title=f"Roots Demo — {demo_name}")
    app.state.roots = roots

    # Mount Roots API routers under /api/
    for router in (
        processes_router,
        runs_router,
        checkpoints_router,
        agents_router,
        graph_router,
        webhooks_router,
    ):
        app.include_router(router, prefix="/api")

    @app.get("/api/demo-info")
    async def demo_info() -> dict[str, str]:
        return {"name": demo_name, "status": "ready"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(Path(static_dir) / "index.html")

    # Static file mounts (after explicit routes so they don't shadow them)
    resolved_common = common_dir if common_dir is not None else _COMMON_DIR
    app.mount("/common", StaticFiles(directory=resolved_common), name="common")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def open_browser(port: int) -> None:
    """Open the demo URL in the default browser after a short delay."""

    def _open() -> None:
        import time

        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    threading.Thread(target=_open, daemon=True).start()


def run_demo(
    roots: Roots,
    demo_name: str,
    static_dir: str,
    port: int = 8200,
) -> None:
    """Create the demo app, open a browser, and run the server."""
    app = create_demo_app(roots, demo_name, static_dir)
    open_browser(port)
    uvicorn.run(app, host="127.0.0.1", port=port)
