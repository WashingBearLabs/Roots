"""Retry execution with configurable backoff for Roots orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from roots.agents.invoker import (
    AgentNotFoundError,
    AgentSchemaValidationError,
)
from roots.core.schema import BackoffStrategy, NodeDefinition, OnExhaustion
from roots.events.emitter import EventEmitter
from roots.events.types import EventType, create_event
from roots.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted with on_exhaustion=fail."""

    def __init__(self, node_id: str, max_attempts: int, last_error: str) -> None:
        self.node_id = node_id
        self.max_attempts = max_attempts
        self.last_error = last_error
        super().__init__(
            f"Node '{node_id}' exhausted {max_attempts} retry attempts: {last_error}"
        )


class RetryRoutedError(Exception):
    """Raised when all retry attempts are exhausted with on_exhaustion=route."""

    def __init__(
        self, node_id: str, max_attempts: int, last_error: str, fallback_edge: str
    ) -> None:
        self.node_id = node_id
        self.max_attempts = max_attempts
        self.last_error = last_error
        self.fallback_edge = fallback_edge
        super().__init__(
            f"Node '{node_id}' exhausted {max_attempts} retry attempts, "
            f"routing to fallback '{fallback_edge}': {last_error}"
        )


def is_retryable(error: Exception) -> bool:
    """Check whether an error is retryable.

    Retryable: AgentInvocationError (except AgentSchemaValidationError),
    generic Exception from callables.
    NOT retryable: AgentSchemaValidationError, AgentNotFoundError.
    """
    if isinstance(error, AgentSchemaValidationError):
        return False
    if isinstance(error, AgentNotFoundError):
        return False
    return True


def compute_backoff(
    strategy: BackoffStrategy,
    backoff_seconds: float,
    attempt_number: int,
) -> float:
    """Compute the delay for a given backoff strategy and attempt number."""
    if strategy == BackoffStrategy.FIXED:
        return backoff_seconds
    elif strategy == BackoffStrategy.LINEAR:
        return backoff_seconds * attempt_number
    else:  # EXPONENTIAL
        return backoff_seconds * (2 ** (attempt_number - 1))


async def execute_with_retry(
    node: NodeDefinition,
    execute_fn: Callable[[], Awaitable[Any]],
    storage: StorageBackend,
    run_id: str,
    emitter: EventEmitter,
    process_id: str = "",
) -> Any:
    """Execute a function with retry logic based on node retry config.

    If no retry config or max_attempts == 1, executes once without retry.
    On retryable failure, persists retry state and retries with backoff.
    Non-retryable errors are re-raised immediately.
    """
    retry_config = node.retry
    if retry_config is None or retry_config.max_attempts <= 1:
        return await execute_fn()

    max_attempts = retry_config.max_attempts
    retry_state = await storage.get_retry_state(run_id, node.id)
    current_attempt = (retry_state.attempt_count if retry_state else 0) + 1
    last_error_msg = retry_state.last_error if retry_state else ""

    while current_attempt <= max_attempts:
        # Persist attempt count BEFORE executing
        await storage.increment_retry(run_id, node.id, last_error_msg)

        try:
            result = await execute_fn()
            # Success: clear retry state
            await storage.clear_retry(run_id, node.id)
            return result
        except Exception as exc:
            last_error_msg = str(exc)

            if not is_retryable(exc):
                # Clear retry state and re-raise immediately
                await storage.clear_retry(run_id, node.id)
                raise

            if current_attempt >= max_attempts:
                # Persist final error before raising
                await storage.increment_retry(
                    run_id, node.id, last_error_msg
                )
                # Check on_exhaustion mode
                if retry_config.on_exhaustion == OnExhaustion.ROUTE:
                    raise RetryRoutedError(
                        node_id=node.id,
                        max_attempts=max_attempts,
                        last_error=last_error_msg,
                        fallback_edge=retry_config.fallback_edge or "",
                    )
                raise RetryExhaustedError(
                    node_id=node.id,
                    max_attempts=max_attempts,
                    last_error=last_error_msg,
                )

            # Compute backoff and wait
            delay = compute_backoff(
                retry_config.backoff,
                retry_config.backoff_seconds,
                current_attempt,
            )
            await asyncio.sleep(delay)

            # Emit retrying event
            emitter.emit(
                create_event(
                    EventType.NODE_RETRYING,
                    run_id=run_id,
                    process_id=process_id,
                    node_id=node.id,
                    node_type=node.type.value,
                    metadata={
                        "attempt": current_attempt,
                        "max_attempts": max_attempts,
                        "backoff_seconds": delay,
                    },
                )
            )

            current_attempt += 1

    # Should not be reached, but just in case
    raise RuntimeError("Retry loop exited unexpectedly")  # pragma: no cover
