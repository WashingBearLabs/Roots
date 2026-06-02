"""Tests for Roots subscription API (US-004)."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from roots import Roots
from roots.events.types import EventEnvelope, EventType, create_event
from roots.storage.base import RunRecord
from roots.storage.sqlite import SqliteBackend


ECHO_PROCESS_YAML = """\
id: echo-proc-004
name: Echo Process 004
version: "1.0.0"
description: Simple echo process for US-004 tests
nodes:
  - id: start
    type: agent
    label: Start
    config:
      agent: echo
      output_key: result
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: start
    to: end
entry_point: start
"""

FAILING_END_PROCESS_YAML = """\
id: fail-end-proc-004
name: Failing End Process 004
version: "1.0.0"
description: Process with failing end node for US-004 tests
nodes:
  - id: end
    type: end
    label: End
    config:
      status: failed
edges: []
entry_point: end
"""


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


@pytest.fixture
async def roots_with_echo() -> AsyncIterator[Roots]:
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    await roots.register_agent("echo", echo_agent)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(ECHO_PROCESS_YAML)
        f.flush()
        echo_yaml_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(FAILING_END_PROCESS_YAML)
        f.flush()
        fail_yaml_path = f.name

    await roots.load_process(echo_yaml_path)
    await roots.load_process(fail_yaml_path)
    Path(echo_yaml_path).unlink()
    Path(fail_yaml_path).unlink()

    yield roots
    await roots.close()


# --- Delegation tests ---


class TestDelegation:
    async def test_on_registers_subscription(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            async def cb(event: EventEnvelope) -> None:
                pass

            sub_id = roots.on(EventType.RUN_STARTED, cb)
            assert isinstance(sub_id, str)
            assert sub_id in roots._subscription_manager._subscriptions

    async def test_on_persistent_fires_multiple_times(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            received: list[EventEnvelope] = []

            async def cb(event: EventEnvelope) -> None:
                received.append(event)

            roots.on(EventType.RUN_STARTED, cb)
            event = create_event(EventType.RUN_STARTED, run_id="r1", process_id="p1")
            await roots._subscription_manager.dispatch(event)
            await roots._subscription_manager.dispatch(event)
            assert len(received) == 2

    async def test_once_registers_once_flag(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            async def cb(event: EventEnvelope) -> None:
                pass

            sub_id = roots.once(EventType.RUN_STARTED, cb)
            assert roots._subscription_manager._subscriptions[sub_id].once is True

    async def test_once_fires_once_then_auto_removed(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            count = 0

            async def cb(event: EventEnvelope) -> None:
                nonlocal count
                count += 1

            roots.once(EventType.RUN_STARTED, cb)
            event = create_event(EventType.RUN_STARTED, run_id="r1", process_id="p1")
            await roots._subscription_manager.dispatch(event)
            await roots._subscription_manager.dispatch(event)
            assert count == 1

    async def test_off_removes_subscription(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            async def cb(event: EventEnvelope) -> None:
                pass

            sub_id = roots.on(EventType.RUN_STARTED, cb)
            result = roots.off(sub_id)
            assert result is True
            assert sub_id not in roots._subscription_manager._subscriptions

    async def test_off_returns_false_for_unknown(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            assert roots.off("nonexistent-sub-id") is False

    async def test_wait_for_returns_matching_event(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            event = create_event(EventType.RUN_STARTED, run_id="r1", process_id="p1")

            task = asyncio.create_task(
                roots.wait_for(EventType.RUN_STARTED, timeout=1.0)
            )
            await asyncio.sleep(0)  # let task register its subscription
            await roots._subscription_manager.dispatch(event)
            result = await task

            assert result is event

    async def test_wait_for_timeout_raises(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            with pytest.raises(asyncio.TimeoutError):
                await roots.wait_for(EventType.RUN_STARTED, timeout=0.01)

    async def test_subscription_manager_stored_on_roots(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            # SubscriptionManager is reachable directly on Roots, not via EventEmitter internals
            from roots.events.subscriptions import SubscriptionManager
            assert isinstance(roots._subscription_manager, SubscriptionManager)


# --- start_and_wait tests ---


class TestStartAndWait:
    async def test_success_returns_run_record_and_envelope(
        self, roots_with_echo: Roots
    ) -> None:
        run, event = await roots_with_echo.start_and_wait(
            "echo-proc-004", {"value": 1}, timeout=5.0
        )
        assert isinstance(run, RunRecord)
        assert isinstance(event, EventEnvelope)

    async def test_success_run_id_matches_event(
        self, roots_with_echo: Roots
    ) -> None:
        run, event = await roots_with_echo.start_and_wait(
            "echo-proc-004", {"x": 42}, timeout=5.0
        )
        assert run.id == event.run_id

    async def test_success_event_type_is_run_completed(
        self, roots_with_echo: Roots
    ) -> None:
        run, event = await roots_with_echo.start_and_wait(
            "echo-proc-004", {}, timeout=5.0
        )
        assert event.event == EventType.RUN_COMPLETED

    async def test_failed_run_returns_run_failed_event(
        self, roots_with_echo: Roots
    ) -> None:
        run, event = await roots_with_echo.start_and_wait(
            "fail-end-proc-004", {}, timeout=5.0
        )
        assert isinstance(run, RunRecord)
        assert event.event == EventType.RUN_FAILED
        assert run.id == event.run_id

    async def test_timeout_raises_timeout_error(
        self, roots_with_echo: Roots
    ) -> None:
        # RUN_PAUSED never fires for a normal echo process
        with pytest.raises(asyncio.TimeoutError):
            await roots_with_echo.start_and_wait(
                "echo-proc-004",
                {"x": 1},
                event_type=EventType.RUN_PAUSED,
                timeout=0.05,
            )

    async def test_timeout_cleans_up_subscription(
        self, roots_with_echo: Roots
    ) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await roots_with_echo.start_and_wait(
                "echo-proc-004",
                {"x": 1},
                event_type=EventType.RUN_PAUSED,
                timeout=0.05,
            )
        assert len(roots_with_echo._subscription_manager._subscriptions) == 0

    async def test_start_run_failure_cleans_up_subscription(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            with patch.object(
                roots, "start_run", side_effect=RuntimeError("storage error")
            ):
                with pytest.raises(RuntimeError, match="storage error"):
                    await roots.start_and_wait("any-proc", {}, timeout=1.0)

            assert len(roots._subscription_manager._subscriptions) == 0

    async def test_race_free_subscription_registered_before_start_run(self) -> None:
        """Subscription is registered before start_run is called."""
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            sub_counts_at_start_run: list[int] = []

            async def capturing_start_run(*args: Any, **kwargs: Any) -> RunRecord:
                sub_counts_at_start_run.append(
                    len(roots._subscription_manager._subscriptions)
                )
                raise RuntimeError("abort after capture")

            with patch.object(roots, "start_run", new=capturing_start_run):
                with pytest.raises(RuntimeError):
                    await roots.start_and_wait("any", {}, timeout=1.0)

            assert len(sub_counts_at_start_run) == 1
            assert sub_counts_at_start_run[0] == 1
