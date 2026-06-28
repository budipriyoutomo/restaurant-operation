from typing import Optional, List
from decimal import Decimal
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class AssetResponse(BaseModel):
    """Shape matches the frontend Asset interface in lib/types.ts exactly."""
    id: str
    number: str
    name: str
    category: str
    outlet: str
    status: str
    serialNumber: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    installDate: Optional[str] = None
    lastPM: Optional[str] = None
    nextPM: Optional[str] = None
    createdAt: str


class CreateAssetRequest(BaseModel):
    name: str
    category: str
    outlet: str
    status: str = "operational"
    serialNumber: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    installDate: Optional[str] = None
    lastPM: Optional[str] = None
    nextPM: Optional[str] = None


class UpdateAssetRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    outlet: Optional[str] = None
    status: Optional[str] = None
    serialNumber: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    installDate: Optional[str] = None
    lastPM: Optional[str] = None
    nextPM: Optional[str] = None


class AssetHistoryResponse(BaseModel):
    """Paginated work order list for GET /api/assets/{id}/history."""
    items: List["WorkOrderResponse"]
    total: int
    page: int
    pageSize: int


class AssetSummaryResponse(BaseModel):
    """Aggregate stats for GET /api/assets/{id}/summary."""
    totalWorkOrders: int
    totalDowntimeHours: float
    totalLaborCost: float
    totalPartsCost: float
    totalCost: float
    lastPM: Optional[str] = None
    nextPM: Optional[str] = None
    workOrdersLast90Days: int


# ---------------------------------------------------------------------------
# WorkOrder checklist & attachments
# ---------------------------------------------------------------------------

class ChecklistItemResponse(BaseModel):
    """Shape matches the frontend ChecklistItem interface in lib/types.ts."""
    id: str
    workOrderId: str
    title: str
    isDone: bool
    doneBy: Optional[str] = None    # user UUID
    doneAt: Optional[str] = None
    orderIndex: int


class ChecklistItemCreate(BaseModel):
    title: str
    orderIndex: int = 0


class ChecklistItemUpdate(BaseModel):
    isDone: bool


class WorkOrderAttachmentResponse(BaseModel):
    """Shape matches the frontend WorkOrderAttachment interface in lib/types.ts."""
    id: str
    workOrderId: str
    fileUrl: str
    caption: Optional[str] = None
    uploadedBy: str     # user UUID
    createdAt: str


class WorkOrderAttachmentCreate(BaseModel):
    fileUrl: str
    caption: Optional[str] = None


# ---------------------------------------------------------------------------
# WorkOrder cost & transition
# ---------------------------------------------------------------------------

class WorkOrderCostUpdate(BaseModel):
    laborHours: Optional[float] = None
    laborCost: Optional[float] = None
    partsCost: Optional[float] = None


class WorkOrderTransitionRequest(BaseModel):
    targetStatus: str   # must be a valid WorkOrderStatusEnum value


# ---------------------------------------------------------------------------
# WorkOrder
# ---------------------------------------------------------------------------

class WorkOrderResponse(BaseModel):
    """Shape matches the frontend WorkOrder interface in lib/types.ts exactly."""
    id: str
    number: str
    type: str
    assetId: Optional[str] = None
    assetName: str
    outlet: str
    issueId: Optional[str] = None
    issueNumber: Optional[str] = None
    title: str
    description: str
    priority: str
    status: str
    assignee: str
    scheduledDate: Optional[str] = None
    completedDate: Optional[str] = None
    createdAt: str
    # Cost & downtime fields (migration 012)
    downtimeStart: Optional[str] = None
    downtimeEnd: Optional[str] = None
    laborHours: Optional[float] = None
    laborCost: float = 0
    partsCost: float = 0
    totalCost: float = 0
    estimatedCost: Optional[float] = None
    requiresApproval: bool = False
    approvalId: Optional[str] = None


class WorkOrderDetailResponse(WorkOrderResponse):
    """Extended response with checklist, attachments, and cost rollup."""
    checklistItems: List[ChecklistItemResponse] = []
    attachments: List[WorkOrderAttachmentResponse] = []


# Resolve forward ref for AssetHistoryResponse
AssetHistoryResponse.model_rebuild()


class CreateWorkOrderRequest(BaseModel):
    assetId: str                          # must exist in assets table
    issueId: Optional[str] = None
    issueNumber: Optional[str] = None
    type: str = "corrective"
    title: str
    description: str = ""
    priority: str = "medium"
    assignee: str = "Unassigned"
    scheduledDate: Optional[str] = None
    estimatedCost: Optional[float] = None


class UpdateWorkOrderRequest(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    scheduledDate: Optional[str] = None
    completedDate: Optional[str] = None
