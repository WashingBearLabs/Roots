"""Run lifecycle state machine with validated status transitions."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[RunStatus, list[RunStatus]] = {
    RunStatus.PENDING: [RunStatus.RUNNING, RunStatus.CANCELLED],
    RunStatus.RUNNING: [
        RunStatus.PAUSED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
    RunStatus.PAUSED: [RunStatus.RUNNING, RunStatus.CANCELLED],
    RunStatus.COMPLETED: [],
    RunStatus.FAILED: [],
    RunStatus.CANCELLED: [],
}


class InvalidTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(
        self, current: RunStatus, target: RunStatus, valid_targets: list[RunStatus]
    ) -> None:
        self.current = current
        self.target = target
        self.valid_targets = valid_targets
        super().__init__(
            f"Invalid transition from '{current}' to '{target}'. "
            f"Valid targets: {[str(t) for t in valid_targets]}"
        )


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """Check whether a transition from current to target is valid."""
    return target in VALID_TRANSITIONS[current]


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    """Perform a state transition, raising InvalidTransitionError if invalid."""
    if not can_transition(current, target):
        raise InvalidTransitionError(
            current, target, VALID_TRANSITIONS[current]
        )
    return target
