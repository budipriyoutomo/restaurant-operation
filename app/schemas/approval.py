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
    amount: Optional[int] = None          # IDR integer; format "Rp X" only in frontend
    currency: str = "IDR"
    status: str
    issueId: Optional[str] = None        # null for procurement approvals (Tier 6.1)
    issueNumber: Optional[str] = None
    purchaseRequestId: Optional[str] = None
    currentStepOrder: int = 1
    escalated: bool = False
    steps: List[ApprovalStepResponse] = []


class DecideApprovalRequest(BaseModel):
    """Body for PATCH /api/approvals/{id}/decide — decides the active step."""
    decision: str               # "approved" or "rejected"
    comment: Optional[str] = None
    decidedBy: Optional[str] = None


class DelegateApprovalRequest(BaseModel):
    """Body for PATCH /api/approvals/{id}/delegate — reassign the active step."""
    toUserId: Optional[str] = None
    toRole: Optional[str] = None


class EscalateStaleRequest(BaseModel):
    """Body for POST /api/approvals/escalate-stale."""
    thresholdDays: Optional[int] = None


class EscalateStaleResponse(BaseModel):
    escalated: int
    approvalIds: List[str] = []
