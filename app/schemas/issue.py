from typing import List, Optional
from pydantic import BaseModel


class IssueResponse(BaseModel):
    """Shape matches the frontend Issue interface in lib/types.ts exactly."""
    id: str
    number: str
    title: str
    description: str
    outlet: str
    category: str
    priority: str
    status: str
    assignee: str
    dueDate: Optional[str] = None
    createdDate: str
    slaBreach: bool
    taskIds: List[str] = []
    approvalId: Optional[str] = None
    workOrderId: Optional[str] = None   # populated when a WO is auto-generated


class CreateIssueRequest(BaseModel):
    """Shape matches the frontend CreateIssueInput interface in lib/types.ts."""
    title: str
    description: str = ""
    outlet: str
    category: str
    priority: str
    assignee: str = "Unassigned"
    dueDate: Optional[str] = None
    generateTask: bool = True
    generateApproval: bool = False
    approvalAmount: Optional[str] = None
    # CMMS fields (Tier 1)
    generateWorkOrder: bool = False
    assetId: Optional[str] = None
    estimatedCost: Optional[float] = None


class UpdateIssueRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    assignee: Optional[str] = None
    dueDate: Optional[str] = None
    priority: Optional[str] = None
