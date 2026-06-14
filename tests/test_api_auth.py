"""Tests for optional API-key authentication on the HTTP API."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from roots import Roots, SqliteBackend
from roots.api.app import create_app


async def _make_roots() -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    return Roots(storage=backend)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_no_api_key_means_open_api() -> None:
    """With no api_key configured, data routes are reachable without a key."""
    roots = await _make_roots()
    app = create_app(roots, api_key=None)
    async with _client(app) as c:
        resp = await c.get("/processes")
        assert resp.status_code == 200
    await roots.close()


@pytest.mark.asyncio
async def test_missing_key_rejected_when_configured() -> None:
    roots = await _make_roots()
    app = create_app(roots, api_key="s3cret")
    async with _client(app) as c:
        resp = await c.get("/processes")
        assert resp.status_code == 401
    await roots.close()


@pytest.mark.asyncio
async def test_wrong_key_rejected() -> None:
    roots = await _make_roots()
    app = create_app(roots, api_key="s3cret")
    async with _client(app) as c:
        resp = await c.get("/processes", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401
    await roots.close()


@pytest.mark.asyncio
async def test_correct_key_accepted() -> None:
    roots = await _make_roots()
    app = create_app(roots, api_key="s3cret")
    async with _client(app) as c:
        resp = await c.get("/processes", headers={"X-API-Key": "s3cret"})
        assert resp.status_code == 200
    await roots.close()


@pytest.mark.asyncio
async def test_health_and_root_open_even_with_key() -> None:
    """/ and /health stay reachable without a key (for liveness probes)."""
    roots = await _make_roots()
    app = create_app(roots, api_key="s3cret")
    async with _client(app) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/")).status_code == 200
    await roots.close()


@pytest.mark.asyncio
async def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app falls back to the ROOTS_API_KEY environment variable."""
    monkeypatch.setenv("ROOTS_API_KEY", "from-env")
    roots = await _make_roots()
    app = create_app(roots)  # no explicit api_key
    async with _client(app) as c:
        assert (await c.get("/processes")).status_code == 401
        assert (
            await c.get("/processes", headers={"X-API-Key": "from-env"})
        ).status_code == 200
    await roots.close()
