"""Tests for WebhookDispatcher (US-005)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from roots.events.emitter import EventEmitter
from roots.events.sinks import StdoutSink
from roots.events.types import EventEnvelope
from roots.events.webhooks import WebhookDispatcher
from roots.storage.base import WebhookRecord


@pytest.fixture
def sample_event() -> EventEnvelope:
    return EventEnvelope(
        event="roots.run.started",
        timestamp=datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc),
        run_id="run-1",
        process_id="proc-1",
        metadata={"key": "value"},
    )


class FakeStorage:
    """Minimal fake storage that only implements list_webhooks_by_pattern."""

    def __init__(self, webhooks: list[WebhookRecord] | None = None) -> None:
        self._webhooks = webhooks or []

    async def list_webhooks_by_pattern(self, event_type: str) -> list[WebhookRecord]:
        return [
            w for w in self._webhooks
            if event_type in w.events or "*" in w.events
        ]


class TestWebhookDispatcher:
    async def test_implements_event_sink(self) -> None:
        from roots.events.sinks import EventSink

        storage = FakeStorage()
        dispatcher = WebhookDispatcher(storage)
        assert isinstance(dispatcher, EventSink)

    async def test_dispatches_to_matching_webhooks(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)
        await asyncio.sleep(0.05)  # let fire-and-forget tasks complete

        assert len(requests_made) == 1
        body = json.loads(requests_made[0].content)
        assert body["event"] == "roots.run.started"
        assert body["run_id"] == "run-1"

    async def test_no_dispatch_for_non_matching_events(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.completed"],  # doesn't match
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)
        await asyncio.sleep(0.05)

        assert len(requests_made) == 0

    async def test_hmac_signature_when_secret_present(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        secret = "my-webhook-secret"
        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=secret,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)
        await asyncio.sleep(0.05)

        assert len(requests_made) == 1
        req = requests_made[0]

        # Verify HMAC matches
        body_bytes = sample_event.model_dump_json().encode()
        expected_sig = hmac.new(
            secret.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        assert req.headers["x-roots-signature"] == expected_sig

    async def test_no_signature_when_no_secret(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)
        await asyncio.sleep(0.05)

        assert "x-roots-signature" not in requests_made[0].headers

    async def test_delivery_failure_logged_not_raised(
        self, sample_event: EventEnvelope, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)  # should not raise
        await asyncio.sleep(0.05)

        assert any("delivery failed" in r.message for r in caplog.records)

    async def test_multiple_webhooks_dispatched(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            WebhookRecord(
                id="wh-2",
                url="https://example.com/hook2",
                events=["roots.run.started"],
                secret="secret-2",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await dispatcher.emit(sample_event)
        await asyncio.sleep(0.05)

        assert len(requests_made) == 2

    async def test_emitter_integration(self, sample_event: EventEnvelope) -> None:
        """WebhookDispatcher works as a regular sink in EventEmitter."""
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        webhooks = [
            WebhookRecord(
                id="wh-1",
                url="https://example.com/hook1",
                events=["roots.run.started"],
                secret=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        storage = FakeStorage(webhooks)
        dispatcher = WebhookDispatcher(storage)
        dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        emitter = EventEmitter(sinks=[dispatcher])
        emitter.emit(sample_event)
        await emitter.close(timeout=2.0)
        await asyncio.sleep(0.05)

        assert len(requests_made) == 1

    async def test_emitter_works_without_webhook_dispatcher(
        self, sample_event: EventEnvelope, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Emitter works fine with other sinks, no WebhookDispatcher needed."""
        emitter = EventEmitter(sinks=[StdoutSink(compact=True)])
        emitter.emit(sample_event)
        await emitter.close(timeout=2.0)

        captured = capsys.readouterr()
        body = json.loads(captured.out)
        assert body["event"] == "roots.run.started"
