from typing import Optional
from pydantic import BaseModel


class ApprovalResponse(BaseModel):
    """Shape matches the frontend ApprovalRequest interface in lib/types.ts exactly."""
    id: str
    number: str
    title: str
    type: str
    description: str
    requester: str
    outlet: str
    requestedDate: Optional[str] = None
    amount: Optional[str] = None
    status: str
    issueId: str
    issueNumber: str


class DecideApprovalRequest(BaseModel):
    """Body for PATCH /api/approvals/{id}/decide — FR-13."""
    decision: str           # "approved" or "rejected"
    decidedBy: Optional[str] = None
    decisionNote: Optional[str] = None
