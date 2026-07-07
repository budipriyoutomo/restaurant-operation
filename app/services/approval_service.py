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
from app.services.notification_service import notify_approval_decided, notify_next_approver, notify_roles


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
        amount=int(approval.amount) if approval.amount is not None else None,
        currency=approval.currency if hasattr(approval, "currency") else "IDR",
        status=approval.status.value if hasattr(approval.status, "value") else str(approval.status),
        issueId=str(approval.issue_id),
        issueNumber=approval.issue_number,
        currentStepOrder=approval.current_step_order,
        escalated=bool(getattr(approval, "escalated", False)),
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
    amount: Optional[int] = None,         # IDR integer
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

    # Resolve the step chain from a matching policy (Tier 2.2); fall back to
    # the default 2-step chain (manager → admin) when no policy applies.
    from app.services.approval_policy_service import resolve_steps_for_request
    policy_steps = resolve_steps_for_request(db, approval_type, amount, outlet or None)
    if policy_steps:
        step_defs = [{"order": s["order"], "role": s["role"]} for s in policy_steps]
    else:
        step_defs = [
            {"order": s["order"], "role": s["role"].value}
            for s in DEFAULT_STEPS
        ]

    for step_def in step_defs:
        db.add(ApprovalStep(
            approval_request_id=approval.id,
            step_order=step_def["order"],
            approver_role=step_def["role"],
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
    # If the chain advanced to a new step, reset the staleness clock.
    if result.request_status == "pending" and result.next_step_order != approval.current_step_order:
        approval.current_step_since = datetime.now(timezone.utc)
        approval.escalated = False
    approval.current_step_order = result.next_step_order


# ---------------------------------------------------------------------------
# Delegation + auto-escalation (Tier 3)
# ---------------------------------------------------------------------------

ESCALATION_THRESHOLD_DAYS: int = 3


def is_step_stale(since, now, threshold_days: int) -> bool:
    """True when the active step has been pending at least `threshold_days`."""
    if since is None:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return (now - since).total_seconds() >= threshold_days * 86400


def _active_step(approval: ApprovalRequest):
    return next((s for s in approval.steps if s.step_order == approval.current_step_order), None)


def delegate_active_step(
    db: Session,
    approval_id: str,
    to_user_id: Optional[str] = None,
    to_role: Optional[str] = None,
) -> Optional[ApprovalResponse]:
    """Reassign the currently-active approval step to another user and/or role.
    Notifies the new approver. Only valid while the request is still pending."""
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        return None
    if (approval.status.value if hasattr(approval.status, "value") else str(approval.status)) != "pending":
        raise ValueError("Only a pending approval can be delegated")

    step = _active_step(approval)
    if step is None:
        raise ValueError("No active step to delegate")

    if to_role is not None:
        if to_role not in {r.value for r in ApproverRoleEnum}:
            raise ValueError(f"Invalid role: {to_role}")
        step.approver_role = to_role
    if to_user_id is not None:
        import uuid as _uuid
        try:
            step.approver_user_id = _uuid.UUID(to_user_id)
        except ValueError:
            raise ValueError("Invalid user id")

    # Reset staleness — a fresh approver just received it.
    approval.current_step_since = datetime.now(timezone.utc)
    approval.escalated = False
    db.commit()
    db.refresh(approval)

    role = step.approver_role.value if hasattr(step.approver_role, "value") else str(step.approver_role)
    notify_next_approver(
        db,
        approval_number=approval.number,
        issue_number=approval.issue_number,
        approver_role=role,
        approval_id=approval.id,
        approver_user_id=step.approver_user_id,
    )
    db.commit()
    return _approval_to_response(approval)


def escalate_stale_approvals(db: Session, threshold_days: int = ESCALATION_THRESHOLD_DAYS) -> list:
    """Flag & notify pending approvals whose active step has been stuck too long.
    Idempotent: an already-escalated request is skipped until it advances."""
    now = datetime.now(timezone.utc)
    pending = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.status == ApprovalStatusEnum.pending.value,
            ApprovalRequest.escalated.is_(False),
        )
        .all()
    )
    escalated = []
    for approval in pending:
        if not is_step_stale(approval.current_step_since, now, threshold_days):
            continue
        approval.escalated = True
        notify_roles(
            db,
            roles=["admin"],
            title=f"Approval macet: {approval.number}",
            message=f"Approval untuk {approval.issue_number} menggantung > {threshold_days} hari dan dieskalasi.",
            ntype="critical",
            entity_type="approvals",
            entity_id=approval.id,
        )
        escalated.append(approval)
    db.commit()
    return escalated


# ---------------------------------------------------------------------------
# Downstream WO sync when an ApprovalRequest is finalized
# ---------------------------------------------------------------------------

def _sync_linked_work_order(db: Session, approval: ApprovalRequest, new_status: str) -> None:
    """When an approval is approved/rejected, advance or cancel the linked WO.

    Linked WO is the one where WorkOrder.approval_id == approval.id.
    - approved  → on-hold → in-progress
    - rejected  → on-hold → cancelled
    """
    if new_status not in ("approved", "rejected"):
        return   # still pending — nothing to do

    from app.models.asset import WorkOrder
    from app.models.enums import WorkOrderStatusEnum
    from app.services.work_order_service import InvalidTransitionError, transition_work_order

    wo = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.approval_id == approval.id,
            WorkOrder.requires_approval.is_(True),
        )
        .first()
    )
    if not wo:
        return

    target = (
        WorkOrderStatusEnum.in_progress if new_status == "approved"
        else WorkOrderStatusEnum.cancelled
    )
    try:
        transition_work_order(db, wo, target)
    except InvalidTransitionError:
        pass   # WO may already be in a terminal state — don't crash the approval


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

    # Downstream WO sync — lazy import to avoid circular dependency
    _sync_linked_work_order(db, approval, result.request_status)

    # FR-14: rejection → parent Issue waiting
    if result.request_status == "rejected" and approval.issue:
        approval.issue.status = IssueStatusEnum.waiting.value

    db.commit()
    db.refresh(approval)

    if result.request_status == "pending":
        # Chain advanced — notify the approver(s) of the now-active step (Tier 2.3).
        next_step = next(
            (s for s in approval.steps if s.step_order == approval.current_step_order),
            None,
        )
        if next_step is not None:
            role = (
                next_step.approver_role.value
                if hasattr(next_step.approver_role, "value")
                else str(next_step.approver_role)
            )
            notify_next_approver(
                db,
                approval_number=approval.number,
                issue_number=approval.issue_number,
                approver_role=role,
                approval_id=approval.id,
                approver_user_id=next_step.approver_user_id,
            )
    else:
        # Final decision (approved/rejected) — notify the requester (Tier 2.3).
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
