"""Tests for SubscriptionManager (US-001)."""

from __future__ import annotations

import logging

import pytest

from roots.events.subscriptions import SubscriptionManager
from roots.events.types import EventEnvelope, EventType, create_event


def _make_event(
    event_type: EventType = EventType.RUN_STARTED,
    run_id: str = "run-1",
) -> EventEnvelope:
    return create_event(event_type, run_id=run_id, process_id="proc-1")


class TestOnSubscription:
    async def test_register_and_fire(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        sub_id = manager.on(EventType.RUN_STARTED, cb)
        assert isinstance(sub_id, str)

        event = _make_event()
        await manager.dispatch(event)

        assert len(received) == 1
        assert received[0] is event

    async def test_persistent_fires_multiple_times(self) -> None:
        manager = SubscriptionManager()
        count = 0

        async def cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1

        manager.on(EventType.RUN_STARTED, cb)
        for _ in range(3):
            await manager.dispatch(_make_event())

        assert count == 3

    async def test_type_filtering_excludes_non_matching(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_COMPLETED, cb)
        await manager.dispatch(_make_event(EventType.RUN_STARTED))
        assert len(received) == 0

        await manager.dispatch(_make_event(EventType.RUN_COMPLETED))
        assert len(received) == 1

    async def test_multi_event_type_list(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on([EventType.RUN_STARTED, EventType.RUN_COMPLETED], cb)
        await manager.dispatch(_make_event(EventType.RUN_STARTED))
        await manager.dispatch(_make_event(EventType.RUN_COMPLETED))
        await manager.dispatch(_make_event(EventType.RUN_FAILED))

        assert len(received) == 2

    async def test_empty_event_types_matches_all(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        event_types: list[EventType] = []
        manager.on(event_types, cb)
        await manager.dispatch(_make_event(EventType.RUN_STARTED))
        await manager.dispatch(_make_event(EventType.RUN_COMPLETED))

        assert len(received) == 2


class TestOnceSubscription:
    async def test_once_fires_then_auto_removes(self) -> None:
        manager = SubscriptionManager()
        count = 0

        async def cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1

        manager.once(EventType.RUN_STARTED, cb)
        await manager.dispatch(_make_event())
        await manager.dispatch(_make_event())

        assert count == 1

    async def test_once_returns_sub_id(self) -> None:
        manager = SubscriptionManager()

        async def cb(event: EventEnvelope) -> None:
            pass

        sub_id = manager.once(EventType.RUN_STARTED, cb)
        assert isinstance(sub_id, str)

    async def test_once_single_event_type_wrapped(self) -> None:
        manager = SubscriptionManager()
        count = 0

        async def cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1

        manager.once(EventType.RUN_STARTED, cb)
        await manager.dispatch(_make_event(EventType.RUN_STARTED))
        assert count == 1
        # Second dispatch should not fire (already removed)
        await manager.dispatch(_make_event(EventType.RUN_STARTED))
        assert count == 1


class TestOff:
    async def test_off_removes_subscription(self) -> None:
        manager = SubscriptionManager()
        count = 0

        async def cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1

        sub_id = manager.on(EventType.RUN_STARTED, cb)
        result = manager.off(sub_id)
        assert result is True

        await manager.dispatch(_make_event())
        assert count == 0

    async def test_off_returns_false_for_unknown(self) -> None:
        manager = SubscriptionManager()
        assert manager.off("nonexistent-id") is False

    async def test_off_idempotent(self) -> None:
        manager = SubscriptionManager()

        async def cb(event: EventEnvelope) -> None:
            pass

        sub_id = manager.on(EventType.RUN_STARTED, cb)
        assert manager.off(sub_id) is True
        assert manager.off(sub_id) is False


class TestRunIdFilter:
    async def test_run_id_filter_includes_matching(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, cb, run_id="run-A")

        await manager.dispatch(_make_event(run_id="run-A"))
        await manager.dispatch(_make_event(run_id="run-B"))

        assert len(received) == 1
        assert received[0].run_id == "run-A"

    async def test_no_run_id_matches_all_runs(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, cb)

        await manager.dispatch(_make_event(run_id="run-A"))
        await manager.dispatch(_make_event(run_id="run-B"))

        assert len(received) == 2

    async def test_run_id_with_once(self) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.once(EventType.RUN_STARTED, cb, run_id="run-A")

        await manager.dispatch(_make_event(run_id="run-B"))
        assert len(received) == 0  # filtered out by run_id

        await manager.dispatch(_make_event(run_id="run-A"))
        assert len(received) == 1  # matched

        await manager.dispatch(_make_event(run_id="run-A"))
        assert len(received) == 1  # once — already removed


class TestErrorIsolation:
    async def test_error_in_callback_does_not_affect_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = SubscriptionManager()
        received: list[EventEnvelope] = []

        async def failing_cb(event: EventEnvelope) -> None:
            raise RuntimeError("Callback error")

        async def good_cb(event: EventEnvelope) -> None:
            received.append(event)

        manager.on(EventType.RUN_STARTED, failing_cb)
        manager.on(EventType.RUN_STARTED, good_cb)

        with caplog.at_level(logging.WARNING):
            await manager.dispatch(_make_event())

        assert len(received) == 1
        assert any("callback raised" in r.message for r in caplog.records)

    async def test_error_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        manager = SubscriptionManager()

        async def failing_cb(event: EventEnvelope) -> None:
            raise ValueError("Test error")

        manager.on(EventType.RUN_STARTED, failing_cb)

        with caplog.at_level(logging.WARNING):
            await manager.dispatch(_make_event())

        assert any("callback raised" in r.message for r in caplog.records)


class TestDispatchSafety:
    async def test_dispatch_snapshots_before_iterating(self) -> None:
        """Calling off() inside a callback must not cause RuntimeError."""
        manager = SubscriptionManager()
        count = 0
        sub_ids: list[str] = []

        async def self_removing_cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1
            manager.off(sub_ids[0])

        sub_id = manager.on(EventType.RUN_STARTED, self_removing_cb)
        sub_ids.append(sub_id)

        await manager.dispatch(_make_event())
        assert count == 1

    async def test_once_removed_before_callback_invocation(self) -> None:
        """Once subscription removed before callback — prevents double-fire on re-entrant dispatch."""
        manager = SubscriptionManager()
        count = 0

        async def cb(event: EventEnvelope) -> None:
            nonlocal count
            count += 1
            if count == 1:
                # Re-entrant dispatch — sub already removed, should not fire again
                await manager.dispatch(_make_event())

        manager.once(EventType.RUN_STARTED, cb)
        await manager.dispatch(_make_event())

        assert count == 1
