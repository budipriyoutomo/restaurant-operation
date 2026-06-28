"""Approval service — handles the decide action with its cross-table side effect.

FR-14: When an Approval is rejected, the parent Issue's status must become
'waiting'. This write to two tables is done atomically here in the service
layer, not split across two API calls from the frontend.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest
from app.models.enums import IssueStatusEnum
from app.schemas.approval import ApprovalResponse, DecideApprovalRequest


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
    )


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


def decide_approval(
    db: Session,
    approval_id: str,
    req: DecideApprovalRequest,
) -> Optional[ApprovalResponse]:
    """Approve or reject an ApprovalRequest.

    Side effect (FR-14): if decision is 'rejected', also sets the parent
    Issue status to 'waiting'. Both writes happen in the same transaction.
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not approval:
        return None

    approval.status = req.decision
    approval.decided_at = datetime.now(timezone.utc)
    approval.decided_by = req.decidedBy
    approval.decision_note = req.decisionNote

    # FR-14: rejection writes back to parent Issue
    if req.decision == "rejected" and approval.issue:
        approval.issue.status = IssueStatusEnum.waiting.value

    db.commit()
    db.refresh(approval)
    return _approval_to_response(approval)
