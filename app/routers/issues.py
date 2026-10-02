from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.issue import Issue
from app.services.outlet_scope_service import assert_can_access, assert_can_write_outlet, resolve_outlet_id
from app.schemas.issue import (
    CancelIssueRequest,
    CreateIssueRequest,
    IssueResponse,
    ReopenIssueRequest,
    ReviseApprovalRequest,
    UpdateIssueRequest,
)
from app.services import idempotency_service, issue_service
from app.services.auth_service import UserResponse, get_current_user, require_permission

router = APIRouter(prefix="/api/issues", tags=["issues"])

_ISSUE_VIEW = require_permission("issues", "view", ("dashboard", "view"), ("maintenance", "view"), ("qa", "view"), ("guest-service", "view"), ("it-support", "view"))


def _get_issue_or_404(db: Session, issue_id: str, user) -> Issue:
    """404 both when missing and when outside the caller's outlets."""
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    assert_can_access(db, issue, user)
    return issue


@router.post("", response_model=IssueResponse, status_code=201)
def create_issue(
    req: CreateIssueRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
    idempotency_key: Optional[str] = Header(None),
):
    """Create a new Issue and auto-generate Task/Approval based on toggles (FR-6).

    Up to four records come out of one call, so a retried/double-tapped submit
    carrying the same Idempotency-Key gets the first response back instead of
    a second set of records (Todo-Pilot §3).
    """
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    # A non-admin may only file issues for outlets they are assigned to.
    assert_can_write_outlet(db, resolve_outlet_id(db, req.outlet), current_user)
    resp = issue_service.create_issue(db, req)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.get("", response_model=List[IssueResponse])
def list_issues(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
):
    """List all Issues with optional filters."""
    return issue_service.list_issues(db, status=status, category=category, outlet=outlet, user=current_user)


@router.get("/{issue_id}", response_model=IssueResponse)
def get_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
):
    """Get a single Issue with its linked Task/Approval summaries (FR-7)."""
    _get_issue_or_404(db, issue_id, current_user)
    return issue_service.get_issue(db, issue_id)


@router.patch("/{issue_id}", response_model=IssueResponse)
def update_issue(
    issue_id: str,
    req: UpdateIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """Update Issue fields. Requires manager or admin role.

    Moving to resolved/closed is refused (409, with `blockers`) while any
    Task / Work Order / Approval of the Issue is still open (Todo-Pilot §1).
    """
    _get_issue_or_404(db, issue_id, current_user)
    return issue_service.update_issue(db, issue_id, req, actor=current_user.email)


@router.post("/{issue_id}/cancel", response_model=IssueResponse)
def cancel_issue(
    issue_id: str,
    req: CancelIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """Cancel the Issue with its open Tasks/WOs; a pending approval is rejected."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.cancel_issue(db, issue, req.reason, actor=current_user.email)


@router.post("/{issue_id}/reopen", response_model=IssueResponse)
def reopen_issue(
    issue_id: str,
    req: ReopenIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """resolved → in-progress; a reason is required."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.reopen_issue(db, issue, req.reason, actor=current_user.email)


@router.post("/{issue_id}/revise-approval", response_model=IssueResponse)
def revise_approval(
    issue_id: str,
    req: ReviseApprovalRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """After a rejection: resubmit a revised amount through the same approval."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.revise_approval(db, issue, req.amount, req.reason, actor=current_user.email)
