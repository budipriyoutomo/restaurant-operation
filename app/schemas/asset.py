from typing import Optional
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


class UpdateWorkOrderRequest(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    scheduledDate: Optional[str] = None
    completedDate: Optional[str] = None
