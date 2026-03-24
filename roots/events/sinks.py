"""Event sink protocol and concrete sinks for Roots event system."""

from __future__ import annotations

import abc
import asyncio
import logging
import sys
from pathlib import Path

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
