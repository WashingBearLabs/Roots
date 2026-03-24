"""Tests for retry execution with backoff (US-001)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from roots.agents.invoker import (
    AgentInvocationError,
    AgentNotFoundError,
    AgentSchemaValidationError,
)
from roots.core.retry import (
    RetryExhaustedError,
    compute_backoff,
    execute_with_retry,
    is_retryable,
)
from roots.core.schema import (
    AgentNodeConfig,
    BackoffStrategy,
    NodeDefinition,
    NodeType,
    OnExhaustion,
    RetryConfig,
)
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope
from roots.storage.base import RetryState


# --- Helpers ---


class CollectorSink(EventSink):
    """Collects emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


def make_node(
    retry_config: RetryConfig | None = None,
    node_id: str = "agent-1",
) -> NodeDefinition:
    return NodeDefinition(
        id=node_id,
        type=NodeType.AGENT,
        label="Test Agent",
        config=AgentNodeConfig(agent="test", output_key="result"),
        retry=retry_config,
    )


def make_storage() -> AsyncMock:
    storage = AsyncMock()
    storage.get_retry_state = AsyncMock(return_value=None)
    storage.increment_retry = AsyncMock()
    storage.clear_retry = AsyncMock()
    return storage


def make_emitter() -> tuple[EventEmitter, CollectorSink]:
    sink = CollectorSink()
    emitter = EventEmitter(sinks=[sink])
    return emitter, sink


# --- US-002: Retry Exhaustion — Fail Mode ---


class TestRetryExhaustionFailMode:
    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhaustion_raises_retry_exhausted_error(
        self, mock_sleep: AsyncMock
    ) -> None:
        """When on_exhaustion=fail and retries exhaust, RetryExhaustedError is raised."""
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=0.01,
                on_exhaustion=OnExhaustion.FAIL,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(side_effect=RuntimeError("always fails"))

        with pytest.raises(RetryExhaustedError) as exc_info:
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        assert exc_info.value.node_id == "agent-1"
        assert exc_info.value.max_attempts == 3
        assert exc_info.value.last_error == "always fails"
        assert fn.await_count == 3

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_last_error_recorded_in_storage(
        self, mock_sleep: AsyncMock
    ) -> None:
        """Verify that last error is persisted to storage on each attempt."""
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=2,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=0.01,
                on_exhaustion=OnExhaustion.FAIL,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"error-{call_count}")

        with pytest.raises(RetryExhaustedError) as exc_info:
            await execute_with_retry(
                node, flaky_fn, storage, "run-1", emitter, "proc-1"
            )

        assert exc_info.value.last_error == "error-2"
        # increment_retry called: before attempt 1 (no error), before attempt 2 (error-1),
        # and final persist (error-2)
        increment_calls = storage.increment_retry.await_args_list
        assert len(increment_calls) == 3
        # First call: before attempt 1, no prior error
        assert increment_calls[0].args[2] == ""
        # Second call: before attempt 2, last error from attempt 1
        assert increment_calls[1].args[2] == "error-1"
        # Third call: final persist with last error
        assert increment_calls[2].args[2] == "error-2"

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_all_attempts_executed_before_exhaustion(
        self, mock_sleep: AsyncMock
    ) -> None:
        """All max_attempts are executed before raising RetryExhaustedError."""
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=5,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=0.01,
                on_exhaustion=OnExhaustion.FAIL,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(RetryExhaustedError):
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        assert fn.await_count == 5

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retrying_events_emitted_before_exhaustion(
        self, mock_sleep: AsyncMock
    ) -> None:
        """Retrying events should be emitted for each retry before exhaustion."""
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=1.0,
                on_exhaustion=OnExhaustion.FAIL,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(RetryExhaustedError):
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        await emitter.close()

        retrying_events = [
            e for e in sink.events if e.event == "roots.node.retrying"
        ]
        # 3 attempts, 2 retries (between attempts 1->2 and 2->3)
        assert len(retrying_events) == 2


# --- is_retryable tests ---


class TestIsRetryable:
    def test_agent_invocation_error_is_retryable(self) -> None:
        err = AgentInvocationError("test", "timeout", ValueError("timeout"))
        assert is_retryable(err) is True

    def test_schema_validation_error_not_retryable(self) -> None:
        err = AgentSchemaValidationError(
            "test", "input", [{"message": "bad schema"}]
        )
        assert is_retryable(err) is False

    def test_agent_not_found_error_not_retryable(self) -> None:
        err = AgentNotFoundError("test")
        assert is_retryable(err) is False

    def test_generic_exception_is_retryable(self) -> None:
        err = RuntimeError("transient failure")
        assert is_retryable(err) is True

    def test_value_error_is_retryable(self) -> None:
        err = ValueError("something went wrong")
        assert is_retryable(err) is True


# --- compute_backoff tests ---


class TestComputeBackoff:
    def test_fixed_backoff(self) -> None:
        assert compute_backoff(BackoffStrategy.FIXED, 5.0, 1) == 5.0
        assert compute_backoff(BackoffStrategy.FIXED, 5.0, 2) == 5.0
        assert compute_backoff(BackoffStrategy.FIXED, 5.0, 3) == 5.0

    def test_linear_backoff(self) -> None:
        assert compute_backoff(BackoffStrategy.LINEAR, 5.0, 1) == 5.0
        assert compute_backoff(BackoffStrategy.LINEAR, 5.0, 2) == 10.0
        assert compute_backoff(BackoffStrategy.LINEAR, 5.0, 3) == 15.0

    def test_exponential_backoff(self) -> None:
        assert compute_backoff(BackoffStrategy.EXPONENTIAL, 5.0, 1) == 5.0
        assert compute_backoff(BackoffStrategy.EXPONENTIAL, 5.0, 2) == 10.0
        assert compute_backoff(BackoffStrategy.EXPONENTIAL, 5.0, 3) == 20.0
        assert compute_backoff(BackoffStrategy.EXPONENTIAL, 5.0, 4) == 40.0


# --- execute_with_retry tests ---


class TestExecuteWithRetry:
    @pytest.mark.asyncio
    async def test_no_retry_config_executes_once(self) -> None:
        node = make_node(retry_config=None)
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(return_value={"data": "ok"})

        result = await execute_with_retry(
            node, fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        fn.assert_awaited_once()
        storage.get_retry_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_attempts_1_executes_once(self) -> None:
        node = make_node(retry_config=RetryConfig(max_attempts=1))
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(return_value={"data": "ok"})

        result = await execute_with_retry(
            node, fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_clears_state(self) -> None:
        node = make_node(
            retry_config=RetryConfig(max_attempts=3, backoff_seconds=0.01)
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(return_value={"data": "ok"})

        result = await execute_with_retry(
            node, fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        storage.increment_retry.assert_awaited_once()
        storage.clear_retry.assert_awaited_once_with("run-1", "agent-1")

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_failure_then_success(
        self, mock_sleep: AsyncMock
    ) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=2.0,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient")
            return {"data": "ok"}

        result = await execute_with_retry(
            node, flaky_fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        assert call_count == 2
        # Sleep called with fixed backoff
        mock_sleep.assert_awaited_once_with(2.0)
        # Retry state persisted before each attempt
        assert storage.increment_retry.await_count == 2
        # State cleared on success
        storage.clear_retry.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retrying_event_emitted(self, mock_sleep: AsyncMock) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=1.0,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient")
            return {"data": "ok"}

        await execute_with_retry(
            node, flaky_fn, storage, "run-1", emitter, "proc-1"
        )

        # Flush async event dispatch tasks
        await emitter.close()

        retrying_events = [
            e for e in sink.events if e.event == "roots.node.retrying"
        ]
        assert len(retrying_events) == 1
        assert retrying_events[0].node_id == "agent-1"
        assert retrying_events[0].metadata["attempt"] == 1
        assert retrying_events[0].metadata["max_attempts"] == 3

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausted_retries_raises(
        self, mock_sleep: AsyncMock
    ) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=0.01,
                on_exhaustion=OnExhaustion.FAIL,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(side_effect=RuntimeError("always fails"))

        with pytest.raises(RetryExhaustedError):
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        assert fn.await_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_schema_error_raises_immediately(self) -> None:
        node = make_node(
            retry_config=RetryConfig(max_attempts=3, backoff_seconds=0.01)
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        err = AgentSchemaValidationError(
            "test", "input", [{"message": "bad"}]
        )
        fn = AsyncMock(side_effect=err)

        with pytest.raises(AgentSchemaValidationError):
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        fn.assert_awaited_once()
        storage.clear_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_retryable_not_found_raises_immediately(self) -> None:
        node = make_node(
            retry_config=RetryConfig(max_attempts=3, backoff_seconds=0.01)
        )
        storage = make_storage()
        emitter, sink = make_emitter()
        fn = AsyncMock(side_effect=AgentNotFoundError("missing"))

        with pytest.raises(AgentNotFoundError):
            await execute_with_retry(
                node, fn, storage, "run-1", emitter, "proc-1"
            )

        fn.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_linear_backoff_delays(
        self, mock_sleep: AsyncMock
    ) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=4,
                backoff=BackoffStrategy.LINEAR,
                backoff_seconds=2.0,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise RuntimeError("transient")
            return {"data": "ok"}

        result = await execute_with_retry(
            node, flaky_fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        sleep_calls = [c.args[0] for c in mock_sleep.await_args_list]
        assert sleep_calls == [2.0, 4.0, 6.0]  # linear: 2*1, 2*2, 2*3

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_exponential_backoff_delays(
        self, mock_sleep: AsyncMock
    ) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=4,
                backoff=BackoffStrategy.EXPONENTIAL,
                backoff_seconds=1.0,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise RuntimeError("transient")
            return {"data": "ok"}

        result = await execute_with_retry(
            node, flaky_fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        sleep_calls = [c.args[0] for c in mock_sleep.await_args_list]
        assert sleep_calls == [1.0, 2.0, 4.0]  # exp: 1*2^0, 1*2^1, 1*2^2

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_state_persisted_before_each_attempt(
        self, mock_sleep: AsyncMock
    ) -> None:
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=3,
                backoff_seconds=0.01,
            )
        )
        storage = make_storage()
        emitter, sink = make_emitter()

        call_order: list[str] = []

        original_increment = storage.increment_retry

        async def track_increment(*args: Any) -> None:
            call_order.append("increment")
            await original_increment(*args)

        storage.increment_retry = AsyncMock(side_effect=track_increment)

        call_count = 0

        async def flaky_fn() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            call_order.append(f"execute-{call_count}")
            if call_count < 2:
                raise RuntimeError("transient")
            return {"data": "ok"}

        await execute_with_retry(
            node, flaky_fn, storage, "run-1", emitter, "proc-1"
        )

        assert call_order == [
            "increment",
            "execute-1",
            "increment",
            "execute-2",
        ]

    @pytest.mark.asyncio
    @patch("roots.core.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_resumes_from_stored_retry_state(
        self, mock_sleep: AsyncMock
    ) -> None:
        """Verify that existing retry state is loaded and continued."""
        node = make_node(
            retry_config=RetryConfig(
                max_attempts=5,
                backoff=BackoffStrategy.FIXED,
                backoff_seconds=1.0,
            )
        )
        storage = make_storage()
        # Simulate existing state: 2 previous attempts
        storage.get_retry_state = AsyncMock(
            return_value=RetryState(
                run_id="run-1",
                node_id="agent-1",
                attempt_count=2,
                last_error="previous error",
            )
        )
        emitter, sink = make_emitter()
        fn = AsyncMock(return_value={"data": "ok"})

        result = await execute_with_retry(
            node, fn, storage, "run-1", emitter, "proc-1"
        )

        assert result == {"data": "ok"}
        fn.assert_awaited_once()
