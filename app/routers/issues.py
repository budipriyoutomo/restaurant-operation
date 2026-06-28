from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.issue import CreateIssueRequest, IssueResponse, UpdateIssueRequest
from app.services import issue_service
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/issues", tags=["issues"])


@router.post("", response_model=IssueResponse, status_code=201)
def create_issue(
    req: CreateIssueRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    """Create a new Issue and auto-generate Task/Approval based on toggles (FR-6)."""
    return issue_service.create_issue(db, req)


@router.get("", response_model=List[IssueResponse])
def list_issues(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    """List all Issues with optional filters."""
    return issue_service.list_issues(db, status=status, category=category, outlet=outlet)


@router.get("/{issue_id}", response_model=IssueResponse)
def get_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    """Get a single Issue with its linked Task/Approval summaries (FR-7)."""
    result = issue_service.get_issue(db, issue_id)
    if not result:
        raise HTTPException(status_code=404, detail="Issue not found")
    return result


@router.patch("/{issue_id}", response_model=IssueResponse)
def update_issue(
    issue_id: str,
    req: UpdateIssueRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Update Issue fields. Requires manager or admin role."""
    result = issue_service.update_issue(db, issue_id, req)
    if not result:
        raise HTTPException(status_code=404, detail="Issue not found")
    return result
