"""Approval service — multi-step approval logic.

Pure functions (no DB):
  decide_current_step   — validates role & computes what changes to make

DB functions:
  create_approval_with_steps  — creates ApprovalRequest + default steps atomically
  apply_step_decision         — writes the decide_current_step result to the DB
  list_approvals, get_approval
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.approval import ApprovalNumberSequence, ApprovalRequest, ApprovalStep
from app.models.enums import (
    ApprovalStatusEnum,
    ApprovalStepStatusEnum,
    ApprovalTypeEnum,
    ApproverRoleEnum,
    IssueStatusEnum,
)
from app.schemas.approval import (
    ApprovalResponse,
    ApprovalStepResponse,
    DecideApprovalRequest,
)
from app.services.notification_service import notify_approval_decided, notify_roles


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class ForbiddenStepError(PermissionError):
    """Raised when the actor's role does not match the active step's required role."""


# ---------------------------------------------------------------------------
# Return type for the pure decision function
# ---------------------------------------------------------------------------

@dataclass
class StepDecisionResult:
    request_status: str      # new overall ApprovalRequest status
    next_step_order: int     # new current_step_order on the request
    decided_step_index: int  # 0-based index into the sorted steps list
    decided_step_status: str # "approved" or "rejected"


# ---------------------------------------------------------------------------
# Pure decision function (no DB — tested without a database in Phase 3)
# ---------------------------------------------------------------------------

def decide_current_step(
    request,          # duck-typed: needs .current_step_order and .steps (iterable)
    actor_role: str,
    decision: str,
) -> StepDecisionResult:
    """Compute the result of deciding the active approval step.

    Parameters
    ----------
    request   : object with .current_step_order (int) and .steps (list of objects
                with .step_order and .approver_role)
    actor_role: role string of the user making the decision
    decision  : 'approved' or 'rejected'

    Raises
    ------
    ForbiddenStepError  : actor_role ≠ active step's approver_role
    ValueError          : decision is not 'approved' or 'rejected'
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"Invalid decision {decision!r}. Must be 'approved' or 'rejected'."
        )

    steps = sorted(request.steps, key=lambda s: s.step_order)
    current_order = request.current_step_order

    # Find the active step
    active_idx: Optional[int] = None
    for i, step in enumerate(steps):
        if step.step_order == current_order:
            active_idx = i
            break

    if active_idx is None:
        raise ValueError(f"No step with order {current_order} found in this request.")

    active_step = steps[active_idx]
    step_role = (
        active_step.approver_role.value
        if hasattr(active_step.approver_role, "value")
        else str(active_step.approver_role)
    )

    if actor_role != step_role:
        raise ForbiddenStepError(
            f"Role '{actor_role}' cannot decide a step that requires role '{step_role}'."
        )

    if decision == "rejected":
        return StepDecisionResult(
            request_status="rejected",
            next_step_order=current_order,   # unchanged
            decided_step_index=active_idx,
            decided_step_status="rejected",
        )

    # decision == "approved"
    is_last = active_idx == len(steps) - 1
    return StepDecisionResult(
        request_status="approved" if is_last else "pending",
        next_step_order=current_order if is_last else current_order + 1,
        decided_step_index=active_idx,
        decided_step_status="approved",
    )


# ---------------------------------------------------------------------------
# ORM → schema helpers
# ---------------------------------------------------------------------------

def _step_to_response(step: ApprovalStep) -> ApprovalStepResponse:
    return ApprovalStepResponse(
        id=str(step.id),
        approvalRequestId=str(step.approval_request_id),
        stepOrder=step.step_order,
        approverRole=(
            step.approver_role.value
            if hasattr(step.approver_role, "value")
            else str(step.approver_role)
        ),
        approverUserId=str(step.approver_user_id) if step.approver_user_id else None,
        status=(
            step.status.value
            if hasattr(step.status, "value")
            else str(step.status)
        ),
        decidedBy=str(step.decided_by) if step.decided_by else None,
        decidedAt=step.decided_at.isoformat() if step.decided_at else None,
        comment=step.comment,
        createdAt=step.created_at.isoformat() if step.created_at else "",
    )


def _approval_to_response(approval: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        id=str(approval.id),
        number=approval.number,
        title=approval.title,
        type=approval.type.value if hasattr(approval.type, "value") else str(approval.type),
        description=approval.description or "",
        requester=approval.requester or "",
        outlet=approval.outlet or "",
        requestedDate=approval.requested_date.isoformat() if approval.requested_date else None,
        amount=approval.amount,
        status=approval.status.value if hasattr(approval.status, "value") else str(approval.status),
        issueId=str(approval.issue_id),
        issueNumber=approval.issue_number,
        currentStepOrder=approval.current_step_order,
        steps=[_step_to_response(s) for s in (approval.steps or [])],
    )


# ---------------------------------------------------------------------------
# DB: create approval + default 2 steps
# ---------------------------------------------------------------------------

DEFAULT_STEPS = [
    {"order": 1, "role": ApproverRoleEnum.manager},
    {"order": 2, "role": ApproverRoleEnum.admin},
]


def _next_apr_number(db: Session) -> str:
    from sqlalchemy import text
    year = datetime.now().year
    result = db.execute(
        text("""
            INSERT INTO approval_number_sequences (year, last_seq)
            VALUES (:year, 1)
            ON CONFLICT (year) DO UPDATE
              SET last_seq = approval_number_sequences.last_seq + 1
            RETURNING last_seq
        """),
        {"year": year},
    )
    seq = result.scalar_one()
    return f"APR-{year}-{seq:05d}"


def create_approval_with_steps(
    db: Session,
    *,
    issue_id,
    issue_number: str,
    title: str,
    approval_type: str,
    description: str = "",
    requester: str = "",
    outlet: str = "",
    amount: Optional[str] = None,
    flush_only: bool = False,
) -> ApprovalRequest:
    """Create an ApprovalRequest with the default 2-step chain (manager → admin).

    If flush_only=True, only db.flush() is called (caller manages the commit).
    This allows the entire issue → WO → approval creation to stay in one transaction.
    """
    number = _next_apr_number(db)

    approval = ApprovalRequest(
        issue_id=issue_id,
        issue_number=issue_number,
        number=number,
        title=title,
        type=approval_type,
        description=description,
        requester=requester,
        outlet=outlet,
        requested_date=date.today(),
        amount=amount,
        status=ApprovalStatusEnum.pending.value,
        current_step_order=1,
    )
    db.add(approval)
    db.flush()   # need approval.id for steps FK

    for step_def in DEFAULT_STEPS:
        db.add(ApprovalStep(
            approval_request_id=approval.id,
            step_order=step_def["order"],
            approver_role=step_def["role"].value,
            status=ApprovalStepStatusEnum.pending.value,
        ))

    if not flush_only:
        db.commit()
        db.refresh(approval)

    return approval


# ---------------------------------------------------------------------------
# DB: apply the decision result
# ---------------------------------------------------------------------------

def apply_step_decision(
    db: Session,
    approval: ApprovalRequest,
    result: StepDecisionResult,
    decided_by_id: Optional[str] = None,
    comment: Optional[str] = None,
) -> None:
    """Write the StepDecisionResult to the DB (no commit — caller commits)."""
    steps = sorted(approval.steps, key=lambda s: s.step_order)
    decided_step = steps[result.decided_step_index]

    decided_step.status = result.decided_step_status
    decided_step.decided_at = datetime.now(timezone.utc)
    if decided_by_id:
        import uuid as _uuid
        try:
            decided_step.decided_by = _uuid.UUID(decided_by_id)
        except ValueError:
            pass
    decided_step.comment = comment

    approval.status = result.request_status
    approval.current_step_order = result.next_step_order


# ---------------------------------------------------------------------------
# DB: list / get
# ---------------------------------------------------------------------------

def list_approvals(
    db: Session,
    type_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> List[ApprovalResponse]:
    query = db.query(ApprovalRequest)
    if type_filter:
        query = query.filter(ApprovalRequest.type == type_filter)
    if status_filter:
        query = query.filter(ApprovalRequest.status == status_filter)
    approvals = query.order_by(ApprovalRequest.created_at.desc()).all()
    return [_approval_to_response(a) for a in approvals]


def get_approval(db: Session, approval_id: str) -> Optional[ApprovalResponse]:
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        return None
    return _approval_to_response(approval)


# ---------------------------------------------------------------------------
# DB: decide (replaces the old single-step decide_approval)
# ---------------------------------------------------------------------------

def decide_approval(
    db: Session,
    approval_id: str,
    req: DecideApprovalRequest,
    actor_role: str,
) -> Optional[ApprovalResponse]:
    """Decide the active step of an ApprovalRequest on behalf of actor_role.

    Raises ForbiddenStepError if actor_role ≠ active step's role.
    Side effect: if final approved → downstream WO is advanced (handled by router).
    Side effect (FR-14): if rejected → parent Issue status → 'waiting'.
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        return None

    result = decide_current_step(approval, actor_role=actor_role, decision=req.decision)
    apply_step_decision(
        db,
        approval,
        result,
        decided_by_id=req.decidedBy,
        comment=req.comment,
    )

    # FR-14: rejection → parent Issue waiting
    if result.request_status == "rejected" and approval.issue:
        approval.issue.status = IssueStatusEnum.waiting.value

    db.commit()
    db.refresh(approval)

    notify_approval_decided(
        db,
        approval_number=approval.number,
        issue_number=approval.issue_number,
        decision=req.decision,
        requester_name=approval.requester or "",
        approval_id=approval.id,
    )
    db.commit()

    return _approval_to_response(approval)
