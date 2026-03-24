"""Tests for run lifecycle state machine (US-001)."""

import pytest

from roots.core.state_machine import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    RunStatus,
    can_transition,
    transition,
)


class TestRunStatus:
    def test_all_six_statuses_defined(self) -> None:
        assert len(RunStatus) == 6
        expected = {"pending", "running", "paused", "completed", "failed", "cancelled"}
        assert {s.value for s in RunStatus} == expected

    def test_str_enum_values(self) -> None:
        assert str(RunStatus.PENDING) == "pending"
        assert str(RunStatus.RUNNING) == "running"


class TestValidTransitions:
    @pytest.mark.parametrize(
        "current, target",
        [
            (RunStatus.PENDING, RunStatus.RUNNING),
            (RunStatus.PENDING, RunStatus.CANCELLED),
            (RunStatus.RUNNING, RunStatus.PAUSED),
            (RunStatus.RUNNING, RunStatus.COMPLETED),
            (RunStatus.RUNNING, RunStatus.FAILED),
            (RunStatus.RUNNING, RunStatus.CANCELLED),
            (RunStatus.PAUSED, RunStatus.RUNNING),
            (RunStatus.PAUSED, RunStatus.FAILED),
            (RunStatus.PAUSED, RunStatus.CANCELLED),
        ],
    )
    def test_valid_transition_accepted(self, current: RunStatus, target: RunStatus) -> None:
        assert can_transition(current, target) is True
        result = transition(current, target)
        assert result == target

    @pytest.mark.parametrize(
        "current, target",
        [
            (RunStatus.PENDING, RunStatus.RUNNING),
            (RunStatus.PENDING, RunStatus.CANCELLED),
            (RunStatus.RUNNING, RunStatus.PAUSED),
            (RunStatus.RUNNING, RunStatus.COMPLETED),
            (RunStatus.RUNNING, RunStatus.FAILED),
            (RunStatus.RUNNING, RunStatus.CANCELLED),
            (RunStatus.PAUSED, RunStatus.RUNNING),
            (RunStatus.PAUSED, RunStatus.FAILED),
            (RunStatus.PAUSED, RunStatus.CANCELLED),
        ],
    )
    def test_transition_returns_target_status(self, current: RunStatus, target: RunStatus) -> None:
        assert transition(current, target) is target


class TestTerminalStates:
    @pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED])
    def test_terminal_states_have_no_valid_transitions(self, terminal: RunStatus) -> None:
        assert VALID_TRANSITIONS[terminal] == []

    @pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED])
    def test_terminal_cannot_transition_to_any_status(self, terminal: RunStatus) -> None:
        for target in RunStatus:
            assert can_transition(terminal, target) is False


class TestInvalidTransitions:
    @pytest.mark.parametrize(
        "current, target",
        [
            (RunStatus.COMPLETED, RunStatus.RUNNING),
            (RunStatus.FAILED, RunStatus.RUNNING),
            (RunStatus.CANCELLED, RunStatus.RUNNING),
            (RunStatus.PENDING, RunStatus.COMPLETED),
            (RunStatus.PENDING, RunStatus.PAUSED),
            (RunStatus.PAUSED, RunStatus.COMPLETED),
        ],
    )
    def test_invalid_transition_raises(self, current: RunStatus, target: RunStatus) -> None:
        assert can_transition(current, target) is False
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition(current, target)
        err = exc_info.value
        assert err.current == current
        assert err.target == target
        assert err.valid_targets == VALID_TRANSITIONS[current]

    def test_completed_to_running_error_message(self) -> None:
        with pytest.raises(InvalidTransitionError, match="Invalid transition from 'completed' to 'running'"):
            transition(RunStatus.COMPLETED, RunStatus.RUNNING)

    def test_error_includes_valid_targets(self) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition(RunStatus.PENDING, RunStatus.FAILED)
        err = exc_info.value
        assert RunStatus.RUNNING in err.valid_targets
        assert RunStatus.CANCELLED in err.valid_targets
        assert len(err.valid_targets) == 2
