"""Webhook dispatcher implementing the EventSink interface."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging

import httpx

from roots.core.url_validator import SSRFError, validate_url
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope
from roots.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class WebhookDispatcher(EventSink):
    """Dispatches events to registered webhooks via HTTP POST.

    Implements EventSink so it can be added to the emitter's sinks list
    like any other sink.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def _deliver(self, url: str, body_bytes: bytes, secret: str | None) -> None:
        """POST event payload to a single webhook URL."""
        try:
            validate_url(url)
        except SSRFError:
            logger.warning("WebhookDispatcher skipping SSRF-blocked URL: %s", url)
            return
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if secret:
                signature = hmac.new(
                    secret.encode(), body_bytes, hashlib.sha256
                ).hexdigest()
                headers["X-Roots-Signature"] = signature
            await self._get_client().post(url, content=body_bytes, headers=headers)
        except Exception:
            logger.warning(
                "WebhookDispatcher delivery failed for %s",
                url,
                exc_info=True,
            )

    async def emit(self, event: EventEnvelope) -> None:
        """Dispatch event to all matching webhooks (fire-and-forget)."""
        webhooks = await self._storage.list_webhooks_by_pattern(event.event)
        if not webhooks:
            return

        body_bytes = event.model_dump_json().encode()

        for webhook in webhooks:
            asyncio.create_task(
                self._deliver(webhook.url, body_bytes, webhook.secret)
            )
