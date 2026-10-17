from typing import Optional
from pydantic import BaseModel, Field, field_validator


def _check_timezone(v: Optional[str]) -> Optional[str]:
    """IANA zone name (Asia/Jakarta, Asia/Makassar, Asia/Kuala_Lumpur, …) → 422 otherwise."""
    if v is None:
        return v
    from app.services.ops_checklist_rules import zone
    try:
        zone(v)
    except ValueError:
        raise ValueError("must be an IANA timezone, e.g. Asia/Jakarta")
    return v


class OutletResponse(BaseModel):
    id: str
    name: str
    code: str
    status: str
    # IDR. None = the outlet uses the global default (approvalThresholdDefault).
    approvalThreshold: Optional[int] = None
    approvalThresholdDefault: int
    timezone: str = "Asia/Jakarta"


class CreateOutletRequest(BaseModel):
    name: str
    code: str
    status: str = "operational"
    approvalThreshold: Optional[int] = Field(default=None, ge=0)
    timezone: str = "Asia/Jakarta"

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v):
        return _check_timezone(v)


class UpdateOutletRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    status: Optional[str] = None
    # Send null explicitly to clear the override (field presence is checked).
    approvalThreshold: Optional[int] = Field(default=None, ge=0)
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v):
        return _check_timezone(v)
