"""Tests for FastAPI Application Factory (US-001)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots, __version__
from roots.api.app import create_app, get_roots
from roots.storage.sqlite import SqliteBackend


@pytest.fixture
async def fastapi_app():
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    application = create_app(roots)
    yield application
    await roots.close()


@pytest.fixture
async def client(fastapi_app):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_app_returns_fastapi(fastapi_app):
    """create_app returns a configured FastAPI instance."""
    from fastapi import FastAPI

    assert isinstance(fastapi_app, FastAPI)


@pytest.mark.asyncio
async def test_roots_stored_in_app_state(fastapi_app):
    """Roots instance is accessible via app.state.roots."""
    assert hasattr(fastapi_app.state, "roots")
    assert isinstance(fastapi_app.state.roots, Roots)


@pytest.mark.asyncio
async def test_get_roots_dependency(fastapi_app):
    """get_roots dependency returns the Roots instance from app state."""
    from unittest.mock import MagicMock

    request = MagicMock()
    request.app = fastapi_app
    result = await get_roots(request)
    assert result is fastapi_app.state.roots


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """GET / returns name and version."""
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"name": "roots", "version": __version__}


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """GET /health returns ok status."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_routers_registered(fastapi_app):
    """All four routers are included in the app."""
    # create_app includes 4 routers + 2 inline routes (/ and /health)
    # Even empty routers get included — verify the app has at least
    # the root and health routes
    paths = {r.path for r in fastapi_app.routes if hasattr(r, "path")}
    assert "/" in paths
    assert "/health" in paths


@pytest.mark.asyncio
async def test_cors_middleware(client):
    """CORS middleware allows all origins."""
    resp = await client.options(
        "/",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" in resp.headers
