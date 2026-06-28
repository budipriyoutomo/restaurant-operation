"""Unit tests — Work Order state machine & cost logic.

Purely in-memory: no database, no HTTP client.
These tests define the contract that work_order_service.py must satisfy.

State machine rules (from Todo-CMMS.md §1.1):
  scheduled  → in-progress | cancelled | on-hold
  on-hold    → in-progress | cancelled
  in-progress → completed  | on-hold   | cancelled
  Any other transition → InvalidTransitionError
  Terminal states (completed, cancelled) → no outgoing transitions.
"""

import pytest
from datetime import datetime, timezone

from app.services.work_order_service import (
    can_transition,
    compute_total_cost,
    compute_downtime_hours,
    InvalidTransitionError,
)
from app.models.enums import WorkOrderStatusEnum as S


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def st(val: str) -> S:
    return S(val)


# ---------------------------------------------------------------------------
# Legal transitions
# ---------------------------------------------------------------------------

class TestLegalTransitions:
    @pytest.mark.parametrize("src, dst", [
        ("scheduled",   "in-progress"),
        ("scheduled",   "cancelled"),
        ("scheduled",   "on-hold"),
        ("on-hold",     "in-progress"),
        ("on-hold",     "cancelled"),
        ("in-progress", "completed"),
        ("in-progress", "on-hold"),
        ("in-progress", "cancelled"),
    ])
    def test_legal(self, src, dst):
        assert can_transition(st(src), st(dst)) is True


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------

class TestIllegalTransitions:
    @pytest.mark.parametrize("src, dst", [
        # terminal states have no outgoing edges
        ("completed",   "in-progress"),
        ("completed",   "scheduled"),
        ("completed",   "on-hold"),
        ("cancelled",   "scheduled"),
        ("cancelled",   "in-progress"),
        ("cancelled",   "on-hold"),
        # skipping states
        ("scheduled",   "completed"),
        ("on-hold",     "completed"),
        # self-loops are not transitions
        ("scheduled",   "scheduled"),
        ("in-progress", "in-progress"),
        ("on-hold",     "on-hold"),
    ])
    def test_illegal_raises(self, src, dst):
        with pytest.raises(InvalidTransitionError):
            can_transition(st(src), st(dst))


# ---------------------------------------------------------------------------
# compute_total_cost
# ---------------------------------------------------------------------------

class TestComputeTotalCost:
    def test_sum_of_labor_and_parts(self):
        assert compute_total_cost(labor_cost=300_000, parts_cost=150_000) == 450_000

    def test_zero_values(self):
        assert compute_total_cost(labor_cost=0, parts_cost=0) == 0

    def test_only_labor(self):
        assert compute_total_cost(labor_cost=500_000, parts_cost=0) == 500_000

    def test_only_parts(self):
        assert compute_total_cost(labor_cost=0, parts_cost=200_000) == 200_000

    def test_decimal_precision(self):
        result = compute_total_cost(labor_cost=100_000.50, parts_cost=99_999.50)
        assert result == pytest.approx(200_000.00)


# ---------------------------------------------------------------------------
# compute_downtime_hours
# ---------------------------------------------------------------------------

class TestComputeDowntimeHours:
    def test_exactly_two_hours(self):
        start = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc)
        end   = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
        assert compute_downtime_hours(start, end) == pytest.approx(2.0)

    def test_partial_hour(self):
        start = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc)
        end   = datetime(2026, 6, 28, 8, 30, tzinfo=timezone.utc)
        assert compute_downtime_hours(start, end) == pytest.approx(0.5)

    def test_no_downtime_when_start_is_none(self):
        assert compute_downtime_hours(None, datetime.now(timezone.utc)) == 0.0

    def test_no_downtime_when_end_is_none(self):
        assert compute_downtime_hours(datetime.now(timezone.utc), None) == 0.0

    def test_no_downtime_when_both_none(self):
        assert compute_downtime_hours(None, None) == 0.0
