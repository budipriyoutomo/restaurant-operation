from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PM Schedule
# ---------------------------------------------------------------------------

class PMScheduleResponse(BaseModel):
    """Shape matches the frontend PMSchedule interface in lib/types.ts exactly."""
    id: str
    assetId: str
    assetName: str                       # denormalized for display
    name: str
    triggerType: str                     # calendar | meter
    intervalType: str                    # days | weeks | months (calendar)
    intervalValue: int
    meterInterval: Optional[int] = None  # meter units per PM (meter)
    lastMeterValue: Optional[int] = None
    checklist: List[str] = []
    assigneeRole: Optional[str] = None
    assigneeUserId: Optional[str] = None
    assigneeName: Optional[str] = None
    leadTimeDays: int = 0
    nextDueDate: Optional[str] = None    # ISO date (calendar only)
    lastGeneratedAt: Optional[str] = None
    isActive: bool
    outlet: str
    createdAt: str


class CreatePMScheduleRequest(BaseModel):
    assetId: str
    name: str
    triggerType: str = "calendar"        # calendar | meter
    intervalType: str = "days"           # days | weeks | months
    intervalValue: int = Field(30, ge=1)
    meterInterval: Optional[int] = Field(None, ge=1)
    checklist: List[str] = []
    assigneeRole: Optional[str] = None
    assigneeUserId: Optional[str] = None
    assigneeName: Optional[str] = None
    leadTimeDays: int = Field(0, ge=0)
    nextDueDate: Optional[str] = None    # required for calendar schedules
    isActive: bool = True


class UpdatePMScheduleRequest(BaseModel):
    name: Optional[str] = None
    triggerType: Optional[str] = None
    intervalType: Optional[str] = None
    intervalValue: Optional[int] = Field(None, ge=1)
    meterInterval: Optional[int] = Field(None, ge=1)
    checklist: Optional[List[str]] = None
    assigneeRole: Optional[str] = None
    assigneeUserId: Optional[str] = None
    assigneeName: Optional[str] = None
    leadTimeDays: Optional[int] = Field(None, ge=0)
    nextDueDate: Optional[str] = None
    isActive: Optional[bool] = None


# ---------------------------------------------------------------------------
# Meter readings (meter-based PM)
# ---------------------------------------------------------------------------

class MeterReadingResponse(BaseModel):
    id: str
    assetId: str
    value: int
    note: Optional[str] = None
    recordedBy: Optional[str] = None
    recordedAt: str


class CreateMeterReadingRequest(BaseModel):
    value: int = Field(ge=0)
    note: Optional[str] = None


class RunGeneratorResponse(BaseModel):
    """Result of POST /api/pm-schedules/run-now."""
    generated: int                       # number of preventive WOs created
    workOrderIds: List[str] = []
    schedulesEvaluated: int
