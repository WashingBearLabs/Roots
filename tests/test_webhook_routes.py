"""Tests for Webhook Routes (US-007)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
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


WEBHOOK_BODY = {
    "url": "https://example.com/hook",
    "events": ["roots.run.*"],
    "secret": "s3cret",
}


# --- Create ---


@pytest.mark.asyncio
async def test_create_webhook(client):
    """POST /webhooks creates a webhook and returns 201."""
    resp = await client.post("/webhooks", json=WEBHOOK_BODY)
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/hook"
    assert data["events"] == ["roots.run.*"]
    assert data["secret"] == "****"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_webhook_defaults(client):
    """POST /webhooks with minimal body uses defaults."""
    resp = await client.post("/webhooks", json={"url": "https://example.com/hook2"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["events"] == ["roots.run.*"]
    assert data["secret"] is None


# --- List ---


@pytest.mark.asyncio
async def test_list_webhooks_empty(client):
    """GET /webhooks returns empty list when no webhooks."""
    resp = await client.get("/webhooks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_webhooks(client):
    """GET /webhooks returns all webhooks."""
    await client.post("/webhooks", json=WEBHOOK_BODY)
    await client.post("/webhooks", json={"url": "https://example.com/hook2"})

    resp = await client.get("/webhooks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


# --- Delete ---


@pytest.mark.asyncio
async def test_delete_webhook(client):
    """DELETE /webhooks/{id} removes the webhook."""
    create_resp = await client.post("/webhooks", json=WEBHOOK_BODY)
    webhook_id = create_resp.json()["id"]

    resp = await client.delete(f"/webhooks/{webhook_id}")
    assert resp.status_code == 204

    # Verify it's gone
    list_resp = await client.get("/webhooks")
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_webhook_not_found(client):
    """DELETE /webhooks/{id} returns 404 for non-existent webhook."""
    resp = await client.delete("/webhooks/wh-nonexistent")
    assert resp.status_code == 404


# --- Test ping ---


@pytest.mark.asyncio
async def test_webhook_test_ping_delivered(client):
    """POST /webhooks/{id}/test delivers event and reports result."""
    create_resp = await client.post("/webhooks", json=WEBHOOK_BODY)
    webhook_id = create_resp.json()["id"]

    # Mock httpx to simulate successful delivery
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("roots.api.routers.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = await client.post(f"/webhooks/{webhook_id}/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "delivered"
    assert data["response_code"] == 200
    assert data["error"] is None

    # Verify the payload sent
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://example.com/hook"
    payload = call_args[1]["json"]
    assert payload["event"] == "roots.webhook.test"
    assert payload["metadata"]["test"] is True


@pytest.mark.asyncio
async def test_webhook_test_ping_failed(client):
    """POST /webhooks/{id}/test reports failure on connection error."""
    create_resp = await client.post("/webhooks", json=WEBHOOK_BODY)
    webhook_id = create_resp.json()["id"]

    with patch("roots.api.routers.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = await client.post(f"/webhooks/{webhook_id}/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "connection refused" in data["error"]
    assert data["response_code"] is None


@pytest.mark.asyncio
async def test_webhook_test_ping_not_found(client):
    """POST /webhooks/{id}/test returns 404 for non-existent webhook."""
    resp = await client.post("/webhooks/wh-nonexistent/test")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_persisted_to_storage(fastapi_app):
    """Created webhooks are persisted to storage."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/webhooks", json=WEBHOOK_BODY)

    roots: Roots = fastapi_app.state.roots
    webhooks = await roots.storage.list_webhooks()
    assert len(webhooks) == 1
    assert webhooks[0].url == "https://example.com/hook"
    assert webhooks[0].events == ["roots.run.*"]
