"""Unit tests for Call Job state machine transition validation."""

import pytest

from app.agent.orchestrator import validate_job_status_transition
from app.core.exceptions import BusinessRuleError


def test_valid_state_transitions() -> None:
    """Verify all valid state transitions pass validation without error."""
    valid_pairs = [
        ("queued", "preparing"),
        ("queued", "cancelled"),
        ("scheduled", "preparing"),
        ("scheduled", "cancelled"),
        ("preparing", "ready"),
        ("preparing", "retry_pending"),
        ("preparing", "failed"),
        ("ready", "calling"),
        ("ready", "cancelled"),
        ("calling", "completed"),
        ("calling", "retry_pending"),
        ("calling", "failed"),
        ("retry_pending", "preparing"),
        ("retry_pending", "cancelled"),
    ]
    for current, next_st in valid_pairs:
        # Should not raise exception
        validate_job_status_transition(current, next_st)


def test_invalid_state_transitions_raise_error() -> None:
    """Verify invalid state transitions raise BusinessRuleError with code INVALID_STATE_TRANSITION."""
    invalid_pairs = [
        ("completed", "calling"),
        ("failed", "ready"),
        ("cancelled", "preparing"),
        ("queued", "completed"),
        ("ready", "completed"),
        ("calling", "preparing"),
    ]
    for current, next_st in invalid_pairs:
        with pytest.raises(BusinessRuleError) as exc_info:
            validate_job_status_transition(current, next_st)
        assert exc_info.value.code == "INVALID_STATE_TRANSITION"
