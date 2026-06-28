"""Unit tests — Approval multi-step decision logic.

Purely in-memory: no database, no HTTP client.
These tests define the contract that approval_service.decide_current_step() must satisfy.

Rules (from Todo-CMMS.md §1.4):
  1. actor_role must match approver_role of the active step → else ForbiddenStepError.
  2. decision='approved' & more steps remain → step approved, current_step_order += 1,
     overall request status stays 'pending'.
  3. decision='approved' & this is the last step → request status = 'approved'.
  4. decision='rejected' at any step → request status = 'rejected', step = 'rejected'.
"""

import pytest
from dataclasses import dataclass, field
from typing import List

from app.services.approval_service import decide_current_step, ForbiddenStepError


# ---------------------------------------------------------------------------
# Minimal data structures for pure-function testing (no ORM objects needed)
# ---------------------------------------------------------------------------

@dataclass
class StepData:
    step_order: int
    approver_role: str
    status: str = "pending"


@dataclass
class RequestData:
    current_step_order: int
    status: str = "pending"
    steps: List[StepData] = field(default_factory=list)


def two_step_request(current_step: int = 1) -> RequestData:
    """Helper: 2-step request [manager → admin], current at given step."""
    return RequestData(
        current_step_order=current_step,
        status="pending",
        steps=[
            StepData(step_order=1, approver_role="manager"),
            StepData(step_order=2, approver_role="admin"),
        ],
    )


def one_step_request() -> RequestData:
    return RequestData(
        current_step_order=1,
        status="pending",
        steps=[StepData(step_order=1, approver_role="manager")],
    )


# ---------------------------------------------------------------------------
# ForbiddenStepError — wrong role
# ---------------------------------------------------------------------------

class TestForbiddenStep:
    def test_wrong_role_raises(self):
        req = two_step_request(current_step=1)
        with pytest.raises(ForbiddenStepError):
            decide_current_step(req, actor_role="admin", decision="approved")

    def test_staff_cannot_decide_manager_step(self):
        req = two_step_request(current_step=1)
        with pytest.raises(ForbiddenStepError):
            decide_current_step(req, actor_role="staff", decision="approved")

    def test_correct_role_does_not_raise(self):
        req = two_step_request(current_step=1)
        # should not raise
        decide_current_step(req, actor_role="manager", decision="approved")

    def test_wrong_role_on_second_step(self):
        req = two_step_request(current_step=2)
        with pytest.raises(ForbiddenStepError):
            decide_current_step(req, actor_role="manager", decision="approved")


# ---------------------------------------------------------------------------
# Approve intermediate step (not the last)
# ---------------------------------------------------------------------------

class TestApproveIntermediateStep:
    def test_request_stays_pending(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="approved")
        assert result.request_status == "pending"

    def test_current_step_order_advances(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="approved")
        assert result.next_step_order == 2

    def test_decided_step_marked_approved(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="approved")
        assert result.decided_step_index == 0   # 0-based index into steps list
        assert result.decided_step_status == "approved"


# ---------------------------------------------------------------------------
# Approve final step
# ---------------------------------------------------------------------------

class TestApproveFinalStep:
    def test_request_becomes_approved(self):
        req = two_step_request(current_step=2)
        result = decide_current_step(req, actor_role="admin", decision="approved")
        assert result.request_status == "approved"

    def test_step_order_does_not_advance_past_last(self):
        req = two_step_request(current_step=2)
        result = decide_current_step(req, actor_role="admin", decision="approved")
        # current_step_order should remain at 2 (or any sentinel); request is done
        assert result.next_step_order == 2

    def test_final_step_marked_approved(self):
        req = two_step_request(current_step=2)
        result = decide_current_step(req, actor_role="admin", decision="approved")
        assert result.decided_step_status == "approved"

    def test_single_step_approve_closes_request(self):
        req = one_step_request()
        result = decide_current_step(req, actor_role="manager", decision="approved")
        assert result.request_status == "approved"


# ---------------------------------------------------------------------------
# Reject at any step
# ---------------------------------------------------------------------------

class TestRejectStep:
    def test_reject_step_1_closes_request(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="rejected")
        assert result.request_status == "rejected"

    def test_reject_step_2_closes_request(self):
        req = two_step_request(current_step=2)
        result = decide_current_step(req, actor_role="admin", decision="rejected")
        assert result.request_status == "rejected"

    def test_rejected_step_marked_rejected(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="rejected")
        assert result.decided_step_status == "rejected"

    def test_step_order_does_not_advance_on_reject(self):
        req = two_step_request(current_step=1)
        result = decide_current_step(req, actor_role="manager", decision="rejected")
        assert result.next_step_order == 1   # unchanged


# ---------------------------------------------------------------------------
# Invalid decision value
# ---------------------------------------------------------------------------

class TestInvalidDecision:
    def test_unknown_decision_raises_value_error(self):
        req = two_step_request(current_step=1)
        with pytest.raises(ValueError):
            decide_current_step(req, actor_role="manager", decision="maybe")
