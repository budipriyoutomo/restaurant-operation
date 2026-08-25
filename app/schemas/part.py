from typing import List, Optional

from pydantic import BaseModel, Field


class PartResponse(BaseModel):
    """Shape matches the frontend Part interface in lib/types.ts."""
    id: str
    sku: str
    name: str
    category: str
    unit: str
    unitCost: int          # IDR integer
    stockQty: int
    reorderLevel: int
    outlet: Optional[str] = None
    isActive: bool
    lowStock: bool         # derived: stock_qty <= reorder_level
    createdAt: str


class CreatePartRequest(BaseModel):
    sku: str
    name: str
    category: str = "General"
    unit: str = "pcs"
    unitCost: int = Field(0, ge=0)
    stockQty: int = Field(0, ge=0)
    reorderLevel: int = Field(0, ge=0)
    outlet: Optional[str] = None
    isActive: bool = True


class UpdatePartRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    unitCost: Optional[int] = Field(None, ge=0)
    stockQty: Optional[int] = Field(None, ge=0)      # absolute set (e.g. restock)
    reorderLevel: Optional[int] = Field(None, ge=0)
    outlet: Optional[str] = None
    isActive: Optional[bool] = None


class WorkOrderPartResponse(BaseModel):
    id: str
    workOrderId: str
    partId: Optional[str] = None
    partName: str
    quantity: int
    unitCost: int
    lineCost: int
    createdAt: str


class ConsumePartRequest(BaseModel):
    partId: str
    quantity: int = Field(gt=0)


class PartPriceHistoryEntry(BaseModel):
    """One vendor's purchasing history for a part (Tier 6.2)."""
    vendorId: Optional[str] = None
    vendorName: Optional[str] = None
    lastUnitCost: int
    avgUnitCost: int
    minUnitCost: int
    timesOrdered: int
    totalQuantity: int
    lastOrderedAt: Optional[str] = None
