from typing import Optional, List
from pydantic import BaseModel


class ApprovalStepResponse(BaseModel):
    """Shape matches the frontend ApprovalStep interface in lib/types.ts."""
    id: str
    approvalRequestId: str
    stepOrder: int
    approverRole: str           # staff | manager | admin
    approverUserId: Optional[str] = None
    status: str                 # pending | approved | rejected | skipped
    decidedBy: Optional[str] = None
    decidedAt: Optional[str] = None
    comment: Optional[str] = None
    createdAt: str


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
    currentStepOrder: int = 1
    steps: List[ApprovalStepResponse] = []


class DecideApprovalRequest(BaseModel):
    """Body for PATCH /api/approvals/{id}/decide — decides the active step."""
    decision: str               # "approved" or "rejected"
    comment: Optional[str] = None
    decidedBy: Optional[str] = None
