from typing import Optional
from pydantic import BaseModel, Field


class OutletResponse(BaseModel):
    id: str
    name: str
    code: str
    status: str
    # IDR. None = the outlet uses the global default (approvalThresholdDefault).
    approvalThreshold: Optional[int] = None
    approvalThresholdDefault: int


class CreateOutletRequest(BaseModel):
    name: str
    code: str
    status: str = "operational"
    approvalThreshold: Optional[int] = Field(default=None, ge=0)


class UpdateOutletRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    status: Optional[str] = None
    # Send null explicitly to clear the override (field presence is checked).
    approvalThreshold: Optional[int] = Field(default=None, ge=0)
