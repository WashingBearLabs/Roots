"""Tests for demo server infrastructure (US-001)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.storage.sqlite import SqliteBackend


@pytest.fixture
def static_dir(tmp_path: Path) -> str:
    index = tmp_path / "index.html"
    index.write_text("<html><body>demo</body></html>")
    return str(tmp_path)


@pytest.fixture
async def demo_app(static_dir: str):
    from demo._common.demo_server import create_demo_app

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    app = create_demo_app(roots, demo_name="test-demo", static_dir=static_dir)
    yield app
    await roots.close()


@pytest.fixture
async def client(demo_app):
    transport = ASGITransport(app=demo_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_demo_app_returns_fastapi(demo_app):
    from fastapi import FastAPI

    assert isinstance(demo_app, FastAPI)


@pytest.mark.asyncio
async def test_roots_on_app_state(demo_app):
    assert hasattr(demo_app.state, "roots")
    assert isinstance(demo_app.state.roots, Roots)


@pytest.mark.asyncio
async def test_index_serves_html(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "demo" in resp.text


@pytest.mark.asyncio
async def test_demo_info_endpoint(client):
    resp = await client.get("/api/demo-info")
    assert resp.status_code == 200
    assert resp.json() == {"name": "test-demo", "status": "ready"}


@pytest.mark.asyncio
async def test_api_routers_mounted(demo_app):
    """API routers are mounted under /api/ prefix."""
    paths = {r.path for r in demo_app.routes if hasattr(r, "path")}
    # Processes router exposes /api/processes
    assert "/api/processes" in paths or any(
        "/api/processes" in getattr(r, "path", "") for r in demo_app.routes
    )


@pytest.mark.asyncio
async def test_static_files_mounted(client, static_dir: str):
    # Write a test file to the static dir
    Path(static_dir, "test.txt").write_text("hello")
    resp = await client.get("/static/test.txt")
    assert resp.status_code == 200
    assert resp.text == "hello"


@pytest.mark.asyncio
async def test_common_files_mounted(client):
    # The common dir defaults to demo/_common/ which contains demo_server.py
    resp = await client.get("/common/demo_server.py")
    assert resp.status_code == 200


def test_open_browser():
    """open_browser spawns a thread that calls webbrowser.open after a delay."""
    from demo._common.demo_server import open_browser

    with patch("webbrowser.open") as mock_open:
        open_browser(8200)
        # The daemon thread sleeps 1.5s; wait a bit longer for it to fire
        import time

        time.sleep(2.0)
        mock_open.assert_called_once_with("http://localhost:8200")
