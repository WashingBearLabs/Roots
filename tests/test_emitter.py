"""Tests for EventEmitter with bounded buffer (US-002)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.subscriptions import SubscriptionManager
from roots.events.types import EventEnvelope, EventType, create_event


# --- Test helpers ---


def _make_event(event_type: EventType = EventType.RUN_STARTED) -> EventEnvelope:
    return create_event(event_type, run_id="run-1", process_id="proc-1")


class RecordingSink(EventSink):
    """Sink that records all received events."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


class SlowSink(EventSink):
    """Sink that takes a long time to process."""

    def __init__(self, delay: float = 1.0) -> None:
        self.events: list[EventEnvelope] = []
        self._delay = delay

    async def emit(self, event: EventEnvelope) -> None:
        await asyncio.sleep(self._delay)
        self.events.append(event)


class FailingSink(EventSink):
    """Sink that always raises an exception."""

    async def emit(self, event: EventEnvelope) -> None:
        raise RuntimeError("Sink failure")


# --- Async dispatch tests ---


class TestEventEmitterDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_all_sinks(self) -> None:
        sink1 = RecordingSink()
        sink2 = RecordingSink()
        emitter = EventEmitter(sinks=[sink1, sink2])

        event = _make_event()
        emitter.emit(event)
        await emitter.close()

        assert len(sink1.events) == 1
        assert len(sink2.events) == 1
        assert sink1.events[0] is event
        assert sink2.events[0] is event

    @pytest.mark.asyncio
    async def test_fire_and_forget_does_not_block(self) -> None:
        slow_sink = SlowSink(delay=10.0)
        emitter = EventEmitter(sinks=[slow_sink])

        event = _make_event()
        # emit should return immediately, not block for 10s
        emitter.emit(event)

        # No events received yet since sink is slow
        assert len(slow_sink.events) == 0

        # Close with short timeout — won't wait for the slow sink
        await emitter.close(timeout=0.01)

    @pytest.mark.asyncio
    async def test_multiple_events_dispatched(self) -> None:
        sink = RecordingSink()
        emitter = EventEmitter(sinks=[sink])

        events = [_make_event(EventType.RUN_STARTED),
                  _make_event(EventType.RUN_COMPLETED)]
        for e in events:
            emitter.emit(e)
        await emitter.close()

        assert len(sink.events) == 2


# --- Exception isolation tests ---


class TestExceptionIsolation:
    @pytest.mark.asyncio
    async def test_sink_exception_does_not_propagate(self) -> None:
        failing = FailingSink()
        recording = RecordingSink()
        emitter = EventEmitter(sinks=[failing, recording])

        event = _make_event()
        emitter.emit(event)
        await emitter.close()

        # Recording sink still receives the event despite failing sink
        assert len(recording.events) == 1

    @pytest.mark.asyncio
    async def test_sink_exception_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        failing = FailingSink()
        emitter = EventEmitter(sinks=[failing])

        event = _make_event()
        with caplog.at_level(logging.WARNING):
            emitter.emit(event)
            await emitter.close()

        assert any("raised an exception" in r.message for r in caplog.records)


# --- Buffer shedding tests ---


class TestBufferShedding:
    @pytest.mark.asyncio
    async def test_oldest_task_shed_when_buffer_full(self) -> None:
        slow_sink = SlowSink(delay=10.0)
        emitter = EventEmitter(sinks=[slow_sink], max_pending=3)

        # Emit 4 events — should shed the oldest when the 4th is emitted
        for _ in range(4):
            emitter.emit(_make_event())

        # Should have max 3 pending tasks
        assert len(emitter._pending) <= 3

        await emitter.close(timeout=0.01)

    @pytest.mark.asyncio
    async def test_buffer_shedding_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        slow_sink = SlowSink(delay=10.0)
        emitter = EventEmitter(sinks=[slow_sink], max_pending=2)

        with caplog.at_level(logging.WARNING):
            for _ in range(4):
                emitter.emit(_make_event())

        assert any("Buffer full" in r.message for r in caplog.records)
        await emitter.close(timeout=0.01)

    @pytest.mark.asyncio
    async def test_completed_tasks_cleaned_up(self) -> None:
        sink = RecordingSink()
        emitter = EventEmitter(sinks=[sink], max_pending=5)

        # Emit and let tasks complete
        emitter.emit(_make_event())
        await emitter.close()

        # After close, pending should be clear
        assert len(emitter._pending) == 0


# --- No sinks tests ---


class TestNoSinks:
    @pytest.mark.asyncio
    async def test_no_sinks_is_noop(self) -> None:
        emitter = EventEmitter(sinks=[])
        # Should not raise
        emitter.emit(_make_event())
        await emitter.close()

    @pytest.mark.asyncio
    async def test_default_sinks_is_empty(self) -> None:
        emitter = EventEmitter()
        emitter.emit(_make_event())
        await emitter.close()


# --- Close / drain tests ---


class TestClose:
    @pytest.mark.asyncio
    async def test_close_drains_pending_events(self) -> None:
        sink = RecordingSink()
        emitter = EventEmitter(sinks=[sink])

        for _ in range(5):
            emitter.emit(_make_event())

        await emitter.close()

        assert len(sink.events) == 5

    @pytest.mark.asyncio
    async def test_close_respects_timeout(self) -> None:
        slow_sink = SlowSink(delay=10.0)
        emitter = EventEmitter(sinks=[slow_sink])

        emitter.emit(_make_event())
        # Should not hang — timeout enforced
        await asyncio.wait_for(emitter.close(timeout=0.1), timeout=2.0)

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        emitter = EventEmitter(sinks=[RecordingSink()])
        emitter.emit(_make_event())
        await emitter.close()
        # Second close should not raise
        await emitter.close()


# --- Subscription integration tests ---


class TestSubscriptionIntegration:
    @pytest.mark.asyncio
    async def test_callback_fires_after_event(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, cb)
        emitter = EventEmitter(subscriptions=manager)

        event = _make_event()
        emitter.emit(event)
        await emitter.close()

        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_subscriptions_fire_without_sinks(self) -> None:
        """Early-return guard allows subscriptions even when no sinks configured."""
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, cb)
        emitter = EventEmitter(subscriptions=manager)  # no sinks

        emitter.emit(_make_event())
        await emitter.close()

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_callback_error_isolation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exception in a callback doesn't break sink delivery or other callbacks."""
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def failing_cb(event: EventEnvelope) -> None:
            raise RuntimeError("boom")

        async def good_cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, failing_cb)
        manager.on(EventType.RUN_STARTED, good_cb)

        sink = RecordingSink()
        emitter = EventEmitter(sinks=[sink], subscriptions=manager)

        with caplog.at_level(logging.WARNING):
            emitter.emit(_make_event())
            await emitter.close()

        assert len(received) == 1
        assert len(sink.events) == 1

    @pytest.mark.asyncio
    async def test_callback_emitting_does_not_recurse(self) -> None:
        """A callback that calls emit() schedules a task — no RecursionError."""
        manager = SubscriptionManager()
        emitter_ref: list[EventEmitter] = []

        async def reemitting_cb(event: EventEnvelope) -> None:
            # emit from inside a callback — task-scheduled, not inline
            emitter_ref[0].emit(_make_event(EventType.RUN_COMPLETED))

        manager.once(EventType.RUN_STARTED, reemitting_cb)
        emitter = EventEmitter(subscriptions=manager)
        emitter_ref.append(emitter)

        # Should not raise RecursionError
        emitter.emit(_make_event(EventType.RUN_STARTED))
        await emitter.close()  # no crash = success
