"""Event sink protocol and concrete sinks for Roots event system."""

from __future__ import annotations

import abc
import asyncio
import logging
import sys
from pathlib import Path

import httpx

from roots.events.types import EventEnvelope

logger = logging.getLogger(__name__)


class EventSink(abc.ABC):
    """Abstract base class for event sinks.

    Sinks receive events asynchronously from the EventEmitter.
    """

    @abc.abstractmethod
    async def emit(self, event: EventEnvelope) -> None:
        """Emit an event to this sink.

        Args:
            event: The event envelope to emit.
        """


class StdoutSink(EventSink):
    """Prints events as JSON to stdout."""

    def __init__(self, compact: bool = False) -> None:
        self._compact = compact

    async def emit(self, event: EventEnvelope) -> None:
        try:
            if self._compact:
                line = event.model_dump_json()
            else:
                line = event.model_dump_json(indent=2)
            print(line, file=sys.stdout)
        except Exception:
            logger.warning(
                "StdoutSink serialization error for event %s",
                getattr(event, "event", "unknown"),
                exc_info=True,
            )


class FileSink(EventSink):
    """Appends events as JSON lines to a file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _write(self, data: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(data)

    async def emit(self, event: EventEnvelope) -> None:
        try:
            line = event.model_dump_json() + "\n"
            await asyncio.to_thread(self._write, line)
        except Exception:
            logger.warning(
                "FileSink serialization/write error for event %s",
                getattr(event, "event", "unknown"),
                exc_info=True,
            )


class HttpSink(EventSink):
    """POSTs events as JSON to a remote HTTP endpoint."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def emit(self, event: EventEnvelope) -> None:
        try:
            body = event.model_dump_json()
            merged_headers = {**self._headers, "Content-Type": "application/json"}
            response = await self._get_client().post(
                self._url, content=body, headers=merged_headers
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "HttpSink HTTP error %s for event %s",
                exc.response.status_code,
                getattr(event, "event", "unknown"),
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(
                "HttpSink connection/timeout error for event %s: %s",
                getattr(event, "event", "unknown"),
                exc,
            )
