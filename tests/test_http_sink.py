"""Tests for HttpSink (US-004)."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from roots.events.sinks import HttpSink
from roots.events.types import EventEnvelope


@pytest.fixture
def sample_event() -> EventEnvelope:
    return EventEnvelope(
        event="roots.run.started",
        timestamp=datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc),
        run_id="run-1",
        process_id="proc-1",
        metadata={"key": "value"},
    )


def _mock_transport(
    status_code: int = 200,
    handler: httpx.MockTransport | None = None,
) -> httpx.MockTransport:
    """Create a mock transport that returns the given status code."""
    if handler:
        return handler

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    return httpx.MockTransport(_handler)


class TestHttpSink:
    async def test_posts_event_as_json(self, sample_event: EventEnvelope) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        sink = HttpSink(url="https://example.com/events")
        sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await sink.emit(sample_event)

        assert len(requests_made) == 1
        req = requests_made[0]
        assert req.method == "POST"
        assert str(req.url) == "https://example.com/events"
        body = json.loads(req.content)
        assert body["event"] == "roots.run.started"
        assert body["run_id"] == "run-1"

    async def test_includes_custom_headers(self, sample_event: EventEnvelope) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        sink = HttpSink(
            url="https://example.com/events",
            headers={"Authorization": "Bearer tok123", "X-Custom": "foo"},
        )
        sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await sink.emit(sample_event)

        req = requests_made[0]
        assert req.headers["authorization"] == "Bearer tok123"
        assert req.headers["x-custom"] == "foo"
        assert req.headers["content-type"] == "application/json"

    async def test_content_type_set_automatically(
        self, sample_event: EventEnvelope
    ) -> None:
        requests_made: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        sink = HttpSink(url="https://example.com/events")
        sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await sink.emit(sample_event)

        assert requests_made[0].headers["content-type"] == "application/json"

    async def test_http_error_logged_not_raised(
        self, sample_event: EventEnvelope, caplog: pytest.LogCaptureFixture
    ) -> None:
        sink = HttpSink(url="https://example.com/events")
        sink._client = httpx.AsyncClient(transport=_mock_transport(status_code=500))

        await sink.emit(sample_event)  # Should not raise

        assert any("HTTP error" in r.message and "500" in r.message for r in caplog.records)

    async def test_timeout_logged_not_raised(
        self, sample_event: EventEnvelope, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        sink = HttpSink(url="https://example.com/events", timeout_seconds=1)
        sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await sink.emit(sample_event)  # Should not raise

        assert any("connection/timeout error" in r.message for r in caplog.records)

    async def test_connection_error_logged_not_raised(
        self, sample_event: EventEnvelope, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        sink = HttpSink(url="https://example.com/events")
        sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        await sink.emit(sample_event)  # Should not raise

        assert any("connection/timeout error" in r.message for r in caplog.records)

    async def test_timeout_enforced(self, sample_event: EventEnvelope) -> None:
        sink = HttpSink(url="https://example.com/events", timeout_seconds=5)
        client = sink._get_client()
        assert client.timeout.read == 5

    async def test_default_timeout(self, sample_event: EventEnvelope) -> None:
        sink = HttpSink(url="https://example.com/events")
        client = sink._get_client()
        assert client.timeout.read == 10

    async def test_client_reused(self, sample_event: EventEnvelope) -> None:
        sink = HttpSink(url="https://example.com/events")
        client1 = sink._get_client()
        client2 = sink._get_client()
        assert client1 is client2
