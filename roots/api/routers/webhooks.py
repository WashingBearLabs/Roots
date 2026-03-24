"""Webhook management routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import WebhookCreateRequest, WebhookResponse, WebhookTestResult
from roots.storage.base import WebhookRecord

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _record_to_response(record: WebhookRecord) -> WebhookResponse:
    return WebhookResponse(
        id=record.id,
        url=record.url,
        events=record.events,
        secret="****" if record.secret else None,
        created_at=record.created_at,
    )


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    roots: Roots = Depends(get_roots),
) -> list[WebhookResponse]:
    """List all registered webhooks."""
    records = await roots.storage.list_webhooks()
    return [_record_to_response(r) for r in records]


@router.post(
    "",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(
    body: WebhookCreateRequest,
    roots: Roots = Depends(get_roots),
) -> WebhookResponse:
    """Register a new webhook."""
    record = await roots.storage.create_webhook(
        url=body.url,
        events=body.events,
        secret=body.secret,
    )
    return _record_to_response(record)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Remove a webhook."""
    removed = await roots.storage.delete_webhook(webhook_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook '{webhook_id}' not found",
        )


@router.post("/{webhook_id}/test", response_model=WebhookTestResult)
async def test_webhook(
    webhook_id: str,
    roots: Roots = Depends(get_roots),
) -> WebhookTestResult:
    """Send a test event to a webhook and report the result."""
    # Find the webhook
    webhooks = await roots.storage.list_webhooks()
    webhook = next((w for w in webhooks if w.id == webhook_id), None)
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook '{webhook_id}' not found",
        )

    # Send test event
    payload = {
        "event": "roots.webhook.test",
        "metadata": {"test": True},
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook.url, json=payload)
        return WebhookTestResult(
            status="delivered",
            response_code=resp.status_code,
        )
    except Exception as exc:
        return WebhookTestResult(
            status="failed",
            error=str(exc),
        )
