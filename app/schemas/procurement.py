from typing import List, Optional

from pydantic import BaseModel, Field


# ── Purchase Request ────────────────────────────────────────────────────────

class PurchaseRequestItemResponse(BaseModel):
    id: str
    partId: Optional[str] = None
    partName: str
    quantity: int
    estUnitCost: int
    lineTotal: int


class PurchaseRequestResponse(BaseModel):
    id: str
    number: str
    status: str
    source: str
    outlet: Optional[str] = None
    requestedBy: Optional[str] = None
    notes: Optional[str] = None
    totalEst: int
    approvalId: Optional[str] = None
    items: List[PurchaseRequestItemResponse] = []
    createdAt: str


class CreatePurchaseRequestItem(BaseModel):
    partId: Optional[str] = None
    partName: str
    quantity: int = Field(gt=0)
    estUnitCost: int = Field(0, ge=0)


class CreatePurchaseRequestRequest(BaseModel):
    outlet: Optional[str] = None
    notes: Optional[str] = None
    items: List[CreatePurchaseRequestItem]


class OrderPurchaseRequestRequest(BaseModel):
    vendorId: str


class ScanLowStockResponse(BaseModel):
    created: int
    purchaseRequestIds: List[str] = []
    partsScanned: int


# ── Purchase Order ──────────────────────────────────────────────────────────

class PurchaseOrderItemResponse(BaseModel):
    id: str
    partId: Optional[str] = None
    partName: str
    quantityOrdered: int
    quantityReceived: int
    unitCost: int
    lineTotal: int


class PurchaseOrderResponse(BaseModel):
    id: str
    number: str
    status: str
    purchaseRequestId: Optional[str] = None
    vendorId: Optional[str] = None
    vendorName: Optional[str] = None
    outlet: Optional[str] = None
    total: int
    items: List[PurchaseOrderItemResponse] = []
    createdAt: str


class ReceiveLine(BaseModel):
    purchaseOrderItemId: str
    quantityReceived: int = Field(ge=0)


class ReceiveGoodsRequest(BaseModel):
    lines: List[ReceiveLine]
    notes: Optional[str] = None


class GoodsReceiptResponse(BaseModel):
    id: str
    number: str
    purchaseOrderId: str
    receivedBy: Optional[str] = None
    createdAt: str
