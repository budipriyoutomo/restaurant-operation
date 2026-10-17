from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import PriorityEnum
from app.schemas.validators import in_enum

# Matches the VARCHAR(500) title columns. Records derived from an Issue add a
# prefix to its title, and issue_service trims those to fit.
TITLE_MAX_LENGTH = 500


class ClosureBlocker(BaseModel):
    type: str      # task | work_order | approval
    id: str
    number: str
    status: str


class IssueApprovalSummary(BaseModel):
    """Where the Issue's approval stands — for the card (Todo-Pilot §21/§22)."""
    id: str
    number: str
    status: str
    currentStepOrder: int
    stepCount: int
    escalated: bool = False


class IssueWorkOrderSummary(BaseModel):
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
    categoryId: Optional[str] = None        # master-data category (Todo-Ops-Checklist P1)
    categoryModule: Optional[str] = None    # behaviour of that category (maintenance, guest_service, …)
    priority: str
    status: str
    assignee: str
    dueDate: Optional[str] = None
    createdDate: str
    createdAt: Optional[str] = None   # ISO timestamp, for "2 hours ago" on the card (Todo-Pilot §21)
    slaBreach: bool
    taskIds: List[str] = []
    approvalId: Optional[str] = None
    workOrderId: Optional[str] = None   # populated when a WO is auto-generated
    # Derived records that are not terminal yet — while non-empty the Issue
    # cannot be resolved/closed (Todo-Pilot §1). Items: {type, id, number, status}.
    closureBlockers: List[ClosureBlocker] = []
    readyToClose: bool = False   # "Siap ditutup" — Todo-Pilot §18
    assigneeUserId: Optional[str] = None   # Todo-Pilot §17; null = not linked to a user
    photoCount: int = 0
    # Summaries so a page of cards needs no other list (Todo-Pilot §22)
    approval: Optional[IssueApprovalSummary] = None
    workOrder: Optional[IssueWorkOrderSummary] = None                    # photos taken when reported (Todo-Pilot §20)


class CreateIssueRequest(BaseModel):
    """Shape matches the frontend CreateIssueInput interface in lib/types.ts."""
    # Bounded here so an over-long title is a 422 naming the field, rather than
    # a VARCHAR(500) overflow surfacing as an opaque 500 mid-transaction.
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str = ""
    outlet: str
    # Master-data category (Todo-Ops-Checklist P1): by name, as before, or by id
    # (wins). Existence and active state are checked by category_service (422).
    category: Optional[str] = None
    categoryId: Optional[str] = None
    priority: str
    assignee: str = "Unassigned"
    assigneeUserId: Optional[str] = None   # picker choice; wins over `assignee` (Todo-Pilot §17)
    dueDate: Optional[str] = None
    generateTask: bool = True
    generateApproval: bool = False
    approvalAmount: Optional[int] = None  # IDR integer
    # CMMS fields (Tier 1)
    generateWorkOrder: bool = False
    assetId: Optional[str] = None
    estimatedCost: Optional[int] = None   # IDR integer

    @model_validator(mode="after")
    def check_category(self):
        if not (self.categoryId or (self.category and self.category.strip())):
            raise ValueError("category or categoryId is required")
        return self

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: str) -> str:
        return in_enum(v, PriorityEnum)


class UpdateIssueRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=TITLE_MAX_LENGTH)
    description: Optional[str] = None
    assignee: Optional[str] = None
    assigneeUserId: Optional[str] = None   # Todo-Pilot §17; wins over `assignee`
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


# Bulk close of "Siap ditutup" Issues (Todo-Pilot §18 / UX-5).
BULK_CLOSE_MAX = 100


class BulkCloseRequest(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=BULK_CLOSE_MAX)


class BulkCloseFailure(BaseModel):
    id: str
    number: Optional[str] = None
    reason: str


class BulkCloseResponse(BaseModel):
    closed: List[IssueResponse]
    failed: List[BulkCloseFailure]


class IssuePhotoResponse(BaseModel):
    """A photo on an Issue (Todo-Pilot §20). URLs are authenticated API routes."""
    id: str
    caption: Optional[str] = None
    fileUrl: str
    thumbnailUrl: str
    mimeType: Optional[str] = None
    createdAt: str


# ── Comments (Todo-Ops-Checklist P3) ─────────────────────────────────────────

COMMENT_MAX_LENGTH = 4000


class IssueCommentResponse(BaseModel):
    id: str
    issueId: str
    body: str
    kind: str                         # user | system
    authorUserId: Optional[str] = None
    authorName: str
    createdAt: str


class CreateIssueCommentRequest(BaseModel):
    """Only `body`: comments made through the API are always kind "user"."""
    body: str

    @field_validator("body")
    @classmethod
    def check_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        if len(v) > COMMENT_MAX_LENGTH:
            raise ValueError(f"must be at most {COMMENT_MAX_LENGTH} characters")
        return v
