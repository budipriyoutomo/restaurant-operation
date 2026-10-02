"""Guest Service API shapes (Todo-Pilot §8). camelCase like the rest of the API."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Channel = Literal["walk-in", "phone", "google-review", "instagram", "whatsapp", "other"]
CompensationType = Literal["none", "discount", "free-item", "voucher", "refund", "other"]


class CreateGuestCaseRequest(BaseModel):
    # Issue fields
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    outlet: str = Field(min_length=1)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    assignee: str = "Unassigned"
    dueDate: Optional[str] = None
    # Guest fields
    guestName: str = Field(default="", max_length=200)
    guestContact: str = Field(default="", max_length=200)
    channel: Channel
    reportedAt: Optional[str] = None          # ISO datetime; default now; not in the future


class UpdateGuestCaseRequest(BaseModel):
    guestName: Optional[str] = Field(default=None, max_length=200)
    guestContact: Optional[str] = Field(default=None, max_length=200)
    channel: Optional[Channel] = None
    reportedAt: Optional[str] = None


class RespondRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class RecoveryRequest(BaseModel):
    compensationType: CompensationType
    compensationValue: int = 0               # integer IDR
    currency: str = Field(default="IDR", min_length=3, max_length=3)
    note: str = Field(default="", max_length=2000)


class GuestCaseResponse(BaseModel):
    id: str
    issueId: str
    issueNumber: str
    title: str
    status: str                              # the Issue's status
    priority: str
    outlet: str
    guestName: str
    guestContact: str
    channel: str
    reportedAt: str
    firstResponseAt: Optional[str]
    firstResponseBy: str
    firstResponseNote: str
    resolvedAt: Optional[str]
    firstResponseMinutes: Optional[int]
    resolutionMinutes: Optional[int]
    compensationType: str
    compensationValue: int
    currency: str
    compensationNote: str


class OutletKpi(BaseModel):
    outlet: str
    cases: int
    open: int
    avgFirstResponseMinutes: Optional[int]
    avgResolutionMinutes: Optional[int]


class GuestKpiResponse(BaseModel):
    cases: int
    open: int
    responded: int
    resolved: int
    avgFirstResponseMinutes: Optional[int]
    medianFirstResponseMinutes: Optional[int]
    avgResolutionMinutes: Optional[int]
    medianResolutionMinutes: Optional[int]
    targetMinutes: int
    withinTargetPct: Optional[float]
    byChannel: Dict[str, int]
    compensationTotal: Dict[str, int]
    perOutlet: List[OutletKpi]
