from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from app.models.enums import IssueCategoryEnum, PriorityEnum
from app.schemas.validators import in_enum

# Matches the VARCHAR(500) title columns. Records derived from an Issue add a
# prefix to its title, and issue_service trims those to fit.
TITLE_MAX_LENGTH = 500


class ClosureBlocker(BaseModel):
    type: str      # task | work_order | approval
    id: str
    number: str
    status: str


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
    # Derived records that are not terminal yet — while non-empty the Issue
    # cannot be resolved/closed (Todo-Pilot §1). Items: {type, id, number, status}.
    closureBlockers: List[ClosureBlocker] = []


class CreateIssueRequest(BaseModel):
    """Shape matches the frontend CreateIssueInput interface in lib/types.ts."""
    # Bounded here so an over-long title is a 422 naming the field, rather than
    # a VARCHAR(500) overflow surfacing as an opaque 500 mid-transaction.
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str = ""
    outlet: str
    category: str
    priority: str
    assignee: str = "Unassigned"
    dueDate: Optional[str] = None
    generateTask: bool = True
    generateApproval: bool = False
    approvalAmount: Optional[int] = None  # IDR integer
    # CMMS fields (Tier 1)
    generateWorkOrder: bool = False
    assetId: Optional[str] = None
    estimatedCost: Optional[int] = None   # IDR integer

    @field_validator("category")
    @classmethod
    def check_category(cls, v: str) -> str:
        return in_enum(v, IssueCategoryEnum)

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: str) -> str:
        return in_enum(v, PriorityEnum)


class UpdateIssueRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=TITLE_MAX_LENGTH)
    description: Optional[str] = None
    assignee: Optional[str] = None
    dueDate: Optional[str] = None
    priority: Optional[str] = None

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: Optional[str]) -> Optional[str]:
        return in_enum(v, PriorityEnum)


class CancelIssueRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)


class ReopenIssueRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ReviseApprovalRequest(BaseModel):
    """Revised cost after a rejection; goes back through the approval chain."""
    amount: int = Field(ge=0)            # IDR integer
    reason: Optional[str] = Field(default=None, max_length=1000)
