"""SubscriptionManager for Roots event subscription system."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from roots.events.types import EventEnvelope, EventType

logger = logging.getLogger(__name__)

AsyncCallback = Callable[[EventEnvelope], Coroutine[Any, Any, None]]


@dataclass
class Subscription:
    id: str
    event_types: list[EventType]
    callback: AsyncCallback
    run_id: str | None
    once: bool


class SubscriptionManager:
    """Manages event subscriptions and dispatches events to matching callbacks."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._pending_wait_for: dict[str, asyncio.Future[EventEnvelope]] = {}

    def _register(
        self,
        event_type: EventType | list[EventType],
        callback: AsyncCallback,
        run_id: str | None = None,
        once: bool = False,
    ) -> str:
        sub_id = str(uuid.uuid4())
        event_types = event_type if isinstance(event_type, list) else [event_type]
        self._subscriptions[sub_id] = Subscription(
            id=sub_id,
            event_types=event_types,
            callback=callback,
            run_id=run_id,
            once=once,
        )
        return sub_id

    def on(
        self,
        event_type: EventType | list[EventType],
        callback: AsyncCallback,
        run_id: str | None = None,
    ) -> str:
        """Register a persistent callback; returns subscription_id."""
        return self._register(event_type, callback, run_id=run_id, once=False)

    def once(
        self,
        event_type: EventType | list[EventType],
        callback: AsyncCallback,
        run_id: str | None = None,
    ) -> str:
        """Register a one-shot callback; auto-removes after first fire."""
        return self._register(event_type, callback, run_id=run_id, once=True)

    def off(self, subscription_id: str) -> bool:
        """Remove subscription; returns True if found, False if not."""
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            return True
        return False

    async def wait_for(
        self,
        event_type: EventType | list[EventType],
        run_id: str | None = None,
        *,
        timeout: float,
    ) -> EventEnvelope:
        """Await the next matching event and return it.

        Args:
            event_type: Event type(s) to wait for.
            run_id: Optional run ID filter.
            timeout: Maximum seconds to wait; raises asyncio.TimeoutError on expiry.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[EventEnvelope] = loop.create_future()

        async def _resolve(event: EventEnvelope) -> None:
            if not future.done():
                future.set_result(event)

        sub_id = self.once(event_type, _resolve, run_id=run_id)
        self._pending_wait_for[sub_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.off(sub_id)
            self._pending_wait_for.pop(sub_id, None)

    async def close(self) -> None:
        """Cancel all pending wait_for futures and clean up their subscriptions."""
        for sub_id, future in list(self._pending_wait_for.items()):
            self.off(sub_id)
            if not future.done():
                future.cancel()
        self._pending_wait_for.clear()

    async def dispatch(self, event: EventEnvelope) -> None:
        """Fire all matching callbacks for the given event."""
        snapshot = list(self._subscriptions.values())
        for sub in snapshot:
            type_match = not sub.event_types or event.event in sub.event_types
            run_id_match = sub.run_id is None or sub.run_id == event.run_id
            if not (type_match and run_id_match):
                continue
            # Remove before invoking to prevent double-fire race on concurrent dispatches
            if sub.once:
                self._subscriptions.pop(sub.id, None)
            try:
                await sub.callback(event)
            except Exception:
                logger.warning(
                    "Subscription %s callback raised an exception for event %s",
                    sub.id,
                    event.event,
                    exc_info=True,
                )
