"""Daily ops checklist API shapes (Todo-Ops-Checklist Fase 1). camelCase like the other modules."""

from datetime import datetime, time
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ItemType = Literal["check", "number", "temperature"]
Shift = Literal["opening", "midday", "closing", "other"]
OpsResult = Literal["pass", "fail", "na"]
RunStatus = Literal["pending", "in_progress", "submitted", "missed"]
ScheduleFrequency = Literal["daily", "weekly", "monthly"]
TemperatureSource = Literal["checklist", "manual", "sensor"]


# ── Templates ────────────────────────────────────────────────────────────────

class TemplateItemIn(BaseModel):
    id: Optional[str] = None                  # existing item → updated; absent → new
    title: str = Field(min_length=1, max_length=500)
    section: str = Field(default="", max_length=100)
    itemType: ItemType = "check"
    minValue: Optional[float] = Field(default=None, ge=-9999.9, le=9999.9)
    maxValue: Optional[float] = Field(default=None, ge=-9999.9, le=9999.9)
    unit: str = Field(default="", max_length=20)
    requiresPhoto: bool = False
    isCritical: bool = False
    issueCategoryId: Optional[str] = None     # null → system "compliance"
    assetId: Optional[str] = None             # temperature item measuring one monitored asset (Fase 2)

    @model_validator(mode="after")
    def check_range(self):
        if self.assetId and self.itemType != "temperature":
            raise ValueError("only temperature items can be bound to an asset")
        if self.itemType == "check" and (self.minValue is not None or self.maxValue is not None):
            raise ValueError("check items have no min/max")
        if self.minValue is not None and self.maxValue is not None and self.minValue > self.maxValue:
            raise ValueError("minValue must not exceed maxValue")
        return self


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    shift: Shift = "other"
    isActive: bool = True
    items: List[TemplateItemIn] = Field(min_length=1)


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    shift: Optional[Shift] = None
    isActive: Optional[bool] = None
    items: Optional[List[TemplateItemIn]] = Field(default=None, min_length=1)   # full list; missing ones deactivate


class TemplateItemResponse(BaseModel):
    id: str
    title: str
    section: str
    itemType: str
    minValue: Optional[float] = None
    maxValue: Optional[float] = None
    unit: str
    requiresPhoto: bool
    isCritical: bool
    issueCategoryId: Optional[str] = None
    assetId: Optional[str] = None
    assetName: Optional[str] = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    shift: str
    isActive: bool
    items: List[TemplateItemResponse]          # active items, in order


# ── Schedules ────────────────────────────────────────────────────────────────

_MAX_MINUTES = 28 * 24 * 60       # the longest window (monthly); tighter per frequency in the service


def _check_days(v: Optional[List[int]]) -> Optional[List[int]]:
    if v is None:
        return v
    if any(d < 1 or d > 7 for d in v):
        raise ValueError("days are ISO weekdays 1 (Mon) … 7 (Sun)")
    return sorted(set(v))


class CreateScheduleRequest(BaseModel):
    templateId: str
    outlet: str
    # daily: daysOfWeek filter (empty = every day) · weekly: its one weekday ·
    # monthly: dayOfMonth. Window limits per frequency: ops_checklist_rules.schedule_problem.
    frequency: ScheduleFrequency = "daily"
    daysOfWeek: List[int] = []
    dayOfMonth: Optional[int] = None
    dueTime: time                              # "09:30", outlet wall clock
    openMinutesBefore: int = Field(default=60, ge=0, le=_MAX_MINUTES)
    graceMinutes: int = Field(default=120, ge=0, le=_MAX_MINUTES)
    isActive: bool = True

    @field_validator("daysOfWeek")
    @classmethod
    def check_days(cls, v):
        return _check_days(v)


class UpdateScheduleRequest(BaseModel):
    templateId: Optional[str] = None
    frequency: Optional[ScheduleFrequency] = None
    daysOfWeek: Optional[List[int]] = None
    dayOfMonth: Optional[int] = None           # only read with a monthly frequency
    dueTime: Optional[time] = None
    openMinutesBefore: Optional[int] = Field(default=None, ge=0, le=_MAX_MINUTES)
    graceMinutes: Optional[int] = Field(default=None, ge=0, le=_MAX_MINUTES)
    isActive: Optional[bool] = None

    @field_validator("daysOfWeek")
    @classmethod
    def check_days(cls, v):
        return _check_days(v)


class ScheduleResponse(BaseModel):
    id: str
    templateId: str
    templateName: str
    shift: str
    outlet: str
    outletId: str
    timezone: str
    frequency: ScheduleFrequency
    daysOfWeek: List[int]
    dayOfMonth: Optional[int] = None
    dueTime: str                               # HH:MM
    openMinutesBefore: int
    graceMinutes: int
    isActive: bool


# ── Runs ─────────────────────────────────────────────────────────────────────

class PhotoResponse(BaseModel):
    id: str
    fileUrl: str
    thumbnailUrl: str
    createdAt: str


class RunItemResponse(BaseModel):
    id: str
    templateItemId: Optional[str] = None
    title: str
    section: str
    itemType: str
    minValue: Optional[float] = None
    maxValue: Optional[float] = None
    unit: str
    requiresPhoto: bool
    isCritical: bool
    assetId: Optional[str] = None             # bound to a monitored asset (QR scan jumps here)
    assetName: Optional[str] = None
    result: Optional[str] = None
    value: Optional[float] = None
    notes: str
    correctiveAction: Optional[str] = None
    recordedAt: Optional[str] = None
    issueId: Optional[str] = None
    issueNumber: Optional[str] = None
    photos: List[PhotoResponse] = []


class RunSummaryResponse(BaseModel):
    id: str
    number: str
    scheduleId: str
    templateId: str
    templateName: str
    shift: str
    outlet: str
    outletId: str
    runDate: str
    opensAt: str
    dueAt: str
    closesAt: str
    status: str
    isLate: bool
    submittedByName: Optional[str] = None
    submittedAt: Optional[str] = None
    score: Optional[float] = None
    itemCount: int
    answeredCount: int
    failedCount: int
    criticalFailed: bool                       # a critical item failed — shown apart, score unchanged


class RunDetailResponse(RunSummaryResponse):
    items: List[RunItemResponse]


class AnswerItemRequest(BaseModel):
    """check items: result. number/temperature: value (result computed), or result "na"."""
    result: Optional[OpsResult] = None
    value: Optional[float] = Field(default=None, ge=-9999.9, le=9999.9)
    notes: Optional[str] = Field(default=None, max_length=2000)
    correctiveAction: Optional[str] = Field(default=None, max_length=2000)
    recordedAt: Optional[datetime] = None       # device clock when filled offline; default now


class RunNowResponse(BaseModel):
    created: int
    missed: int


# ── Temperature logs (Fase 2) ────────────────────────────────────────────────

class TemperatureLogResponse(BaseModel):
    id: str
    assetId: str
    outlet: str
    value: float
    minValue: Optional[float] = None
    maxValue: Optional[float] = None
    inRange: bool
    correctiveAction: Optional[str] = None
    source: TemperatureSource
    runItemId: Optional[str] = None
    issueId: Optional[str] = None
    recordedAt: str


class CreateTemperatureLogRequest(BaseModel):
    value: float = Field(ge=-99.9, le=999.9)
    correctiveAction: Optional[str] = Field(default=None, max_length=2000)   # required when out of range
    recordedAt: Optional[datetime] = None


# ── KPIs (Fase 3) ────────────────────────────────────────────────────────────

class FailingItem(BaseModel):
    title: str
    count: int


class OutletKpi(BaseModel):
    outlet: str
    outletId: str
    scheduled: int                  # runs that were due (finished or window closed)
    onTime: int
    late: int
    missed: int
    compliance: Optional[float] = None   # on time ÷ scheduled, %
    avgScore: Optional[float] = None
    topFailing: List[FailingItem] = []
    excursions: int = 0
    issuesCreated: int = 0


class KpiTotals(BaseModel):
    scheduled: int
    onTime: int
    late: int
    missed: int
    compliance: Optional[float] = None
    avgScore: Optional[float] = None
    excursions: int = 0
    issuesCreated: int = 0


class KpiWeek(BaseModel):
    weekStart: str
    scheduled: int
    onTime: int
    compliance: Optional[float] = None


class KpiResponse(BaseModel):
    from_: str = Field(alias="from")
    to: str
    outlets: List[OutletKpi]            # lowest compliance first
    totals: KpiTotals
    trend: List[KpiWeek]                # 8 weeks ending at `to`

    model_config = {"populate_by_name": True}


# ── Daily sign-off (Fase 4) ──────────────────────────────────────────────────

class SignoffRequest(BaseModel):
    outlet: str
    note: str = Field(default="", max_length=1000)


class DaySummary(BaseModel):
    runs: int = 0
    submitted: int = 0
    late: int = 0
    missed: int = 0
    failedItems: int = 0
    temperatureReadings: int = 0
    excursions: int = 0


class SignoffResponse(BaseModel):
    id: str
    outlet: str
    outletId: str
    businessDate: str
    signedByName: str
    signedAt: str
    note: str
    summary: DaySummary                         # what the manager saw when signing
    digest: str                                 # SHA-256 of the day's runs + temperature readings


class DayResponse(BaseModel):
    outlet: str
    outletId: str
    date: str
    summary: DaySummary                         # now
    runs: List["RunSummaryResponse"]
    signoff: Optional[SignoffResponse] = None
    canSign: bool
    problem: Optional[str] = None               # why it cannot be signed yet
    isIntact: Optional[bool] = None             # signed data still matches (None = not signed)


# ── IoT temperature sensors (Fase 4) ─────────────────────────────────────────

class CreateSensorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SensorResponse(BaseModel):
    id: str
    assetId: str
    name: str
    keyPrefix: str
    isActive: bool
    lastSeenAt: Optional[str] = None
    lastValue: Optional[float] = None
    createdAt: str


class CreatedSensorResponse(SensorResponse):
    key: str                                    # shown once; only its SHA-256 is stored


class SensorReadingIn(BaseModel):
    value: float = Field(ge=-99.9, le=999.9)
    recordedAt: datetime


class SensorReadingsRequest(BaseModel):
    readings: List[SensorReadingIn] = Field(max_length=500)


class SensorReadingsResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: List[dict]
    escalated: bool


# ── Built-in templates (Fase 4) ──────────────────────────────────────────────

class PresetTemplate(BaseModel):
    name: str
    shift: Shift
    frequency: ScheduleFrequency
    dueTime: str
    itemCount: int


class PresetResponse(BaseModel):
    key: str
    label: str
    templates: List[PresetTemplate]


class ApplyPresetRequest(BaseModel):
    outlet: Optional[str] = None               # also schedule the templates at this outlet


class ApplyPresetResponse(BaseModel):
    preset: str
    created: List[str]
    skipped: List[str]                          # names that already existed
    schedules: int
