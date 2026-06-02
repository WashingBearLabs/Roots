"""Event emitter with bounded buffer for Roots event system."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from roots.events.sinks import EventSink
from roots.events.subscriptions import SubscriptionManager
from roots.events.types import EventEnvelope

logger = logging.getLogger(__name__)


class EventEmitter:
    """Dispatches events to sinks asynchronously with a bounded buffer.

    Slow sinks don't consume unbounded memory or block execution.
    When the pending task limit is reached, the oldest task is cancelled.
    Subscriptions use a separate buffer from sinks to prevent sink pressure
    from shedding subscription dispatch tasks.
    """

    def __init__(
        self,
        sinks: list[EventSink] | None = None,
        max_pending: int = 100,
        subscriptions: SubscriptionManager | None = None,
    ) -> None:
        self._sinks: list[EventSink] = sinks or []
        self._max_pending = max_pending
        self._pending: OrderedDict[int, asyncio.Task[None]] = OrderedDict()
        self._pending_subscriptions: OrderedDict[int, asyncio.Task[None]] = OrderedDict()
        self._task_counter = 0
        self._subscriptions = subscriptions

    async def _dispatch_to_sink(
        self, sink: EventSink, event: EventEnvelope
    ) -> None:
        """Dispatch a single event to a single sink, catching exceptions."""
        try:
            await sink.emit(event)
        except Exception:
            logger.warning(
                "Sink %r raised an exception for event %s",
                sink,
                event.event,
                exc_info=True,
            )

    async def _dispatch_subscriptions(self, event: EventEnvelope) -> None:
        """Dispatch a single event to all subscriptions, catching exceptions."""
        if self._subscriptions is None:
            return
        try:
            await self._subscriptions.dispatch(event)
        except Exception:
            logger.warning(
                "Subscription dispatch raised an exception for event %s",
                event.event,
                exc_info=True,
            )

    def _cleanup_completed(self) -> None:
        """Remove completed tasks from the pending sets."""
        done_keys = [k for k, t in self._pending.items() if t.done()]
        for k in done_keys:
            del self._pending[k]
        done_keys = [k for k, t in self._pending_subscriptions.items() if t.done()]
        for k in done_keys:
            del self._pending_subscriptions[k]

    def emit(self, event: EventEnvelope) -> None:
        """Fire-and-forget dispatch of an event to all sinks and subscriptions.

        If the sink buffer is full, the oldest pending sink task is cancelled
        to make room. Subscriptions use a separate buffer.
        """
        if not self._sinks and not self._subscriptions:
            return

        self._cleanup_completed()

        for sink in self._sinks:
            # Shed oldest if at capacity
            if len(self._pending) >= self._max_pending:
                oldest_key, oldest_task = next(iter(self._pending.items()))
                oldest_task.cancel()
                del self._pending[oldest_key]
                logger.warning(
                    "Buffer full (%d pending), shedding oldest task",
                    self._max_pending,
                )

            task = asyncio.create_task(self._dispatch_to_sink(sink, event))
            self._task_counter += 1
            self._pending[self._task_counter] = task

        if self._subscriptions is not None:
            if len(self._pending_subscriptions) >= self._max_pending:
                oldest_key, oldest_task = next(iter(self._pending_subscriptions.items()))
                oldest_task.cancel()
                del self._pending_subscriptions[oldest_key]
                logger.warning(
                    "Subscription buffer full (%d pending), shedding oldest task",
                    self._max_pending,
                )

            task = asyncio.create_task(self._dispatch_subscriptions(event))
            self._task_counter += 1
            self._pending_subscriptions[self._task_counter] = task

    async def close(self, timeout: float = 5.0) -> None:
        """Wait for pending tasks to complete (with timeout).

        Args:
            timeout: Maximum seconds to wait for pending tasks.
        """
        self._cleanup_completed()
        all_tasks = list(self._pending.values()) + list(self._pending_subscriptions.values())
        if not all_tasks:
            return

        await asyncio.wait(all_tasks, timeout=timeout)
        self._pending.clear()
        self._pending_subscriptions.clear()
