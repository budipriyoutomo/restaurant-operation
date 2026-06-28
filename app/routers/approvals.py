from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.approval import ApprovalResponse, DecideApprovalRequest
from app.services import approval_service

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=List[ApprovalResponse])
def list_approvals(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List Approval Requests with optional type/status filters (FR-12)."""
    return approval_service.list_approvals(db, type_filter=type, status_filter=status)


@router.get("/{approval_id}", response_model=ApprovalResponse)
def get_approval(approval_id: str, db: Session = Depends(get_db)):
    """Get a single Approval Request detail."""
    result = approval_service.get_approval(db, approval_id)
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result


@router.patch("/{approval_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    approval_id: str,
    req: DecideApprovalRequest,
    db: Session = Depends(get_db),
):
    """Approve or reject an ApprovalRequest.

    Side effect (FR-14): if rejected, the parent Issue status is atomically
    set to 'waiting' in the same transaction.
    """
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    result = approval_service.decide_approval(db, approval_id, req)
    if not result:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result
