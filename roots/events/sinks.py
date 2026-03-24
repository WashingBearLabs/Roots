"""Event sink protocol for Roots event system."""

from __future__ import annotations

import abc

from roots.events.types import EventEnvelope


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
