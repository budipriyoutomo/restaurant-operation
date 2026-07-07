from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.approval import (
    ApprovalResponse,
    DecideApprovalRequest,
    DelegateApprovalRequest,
    EscalateStaleRequest,
    EscalateStaleResponse,
)
from app.services import approval_service
from app.services.approval_service import ESCALATION_THRESHOLD_DAYS, ForbiddenStepError
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=List[ApprovalResponse])
def list_approvals(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    """List Approval Requests with optional type/status filters (FR-12)."""
    return approval_service.list_approvals(db, type_filter=type, status_filter=status)


@router.get("/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    """Get a single Approval Request with all steps and current_step_order."""
    result = approval_service.get_approval(db, approval_id)
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result


@router.patch("/{approval_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    approval_id: str,
    req: DecideApprovalRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Decide the *active step* of an ApprovalRequest on behalf of the caller.

    The caller's role must match the active step's approver_role; otherwise 403.
    Side effects:
    - If last step approved → overall request becomes 'approved'.
    - If any step rejected  → overall request becomes 'rejected',
      parent Issue status   → 'waiting' (FR-14).
    """
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    try:
        result = approval_service.decide_approval(
            db,
            approval_id,
            req,
            actor_role=current_user.role,
        )
    except ForbiddenStepError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result


@router.post("/escalate-stale", response_model=EscalateStaleResponse)
def escalate_stale(
    req: EscalateStaleRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    """Flag & notify pending approvals stuck longer than the threshold (Tier 3).
    Manual/cron trigger; idempotent until the request advances."""
    threshold = req.thresholdDays if req.thresholdDays is not None else ESCALATION_THRESHOLD_DAYS
    escalated = approval_service.escalate_stale_approvals(db, threshold_days=threshold)
    return EscalateStaleResponse(
        escalated=len(escalated),
        approvalIds=[str(a.id) for a in escalated],
    )


@router.patch("/{approval_id}/delegate", response_model=ApprovalResponse)
def delegate_approval(
    approval_id: str,
    req: DelegateApprovalRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Reassign the active approval step to another user and/or role (Tier 3)."""
    if req.toUserId is None and req.toRole is None:
        raise HTTPException(status_code=422, detail="Provide toUserId and/or toRole")
    try:
        result = approval_service.delegate_active_step(
            db, approval_id, to_user_id=req.toUserId, to_role=req.toRole,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result
