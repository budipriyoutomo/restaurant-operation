"""QA audit checklist API shapes (Todo-Pilot §7). camelCase like the rest of the API."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Templates ────────────────────────────────────────────────────────────────

class TemplateItemInput(BaseModel):
    id: Optional[str] = None                    # PATCH: existing item to update in place
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(default="", max_length=100)
    weight: int = Field(default=1, ge=1, le=10)
    requiresPhoto: bool = False
    isCritical: bool = False


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    items: List[TemplateItemInput] = Field(min_length=1)


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    isActive: Optional[bool] = None
    # Replaces the checklist: listed ids are updated, new ones added, missing
    # ones deactivated (kept for past findings).
    items: Optional[List[TemplateItemInput]] = Field(default=None, min_length=1)


class TemplateItemResponse(BaseModel):
    id: str
    title: str
    category: str
    weight: int
    requiresPhoto: bool
    isCritical: bool
    orderIndex: int


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    isActive: bool
    items: List[TemplateItemResponse]       # active items only, in order


# ── Audits ───────────────────────────────────────────────────────────────────

class StartAuditRequest(BaseModel):
    templateId: str
    outlet: str = Field(min_length=1)
    auditDate: Optional[str] = None          # ISO date; default today


class UpdateFindingRequest(BaseModel):
    result: Optional[Literal["pass", "fail", "na"]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class PhotoResponse(BaseModel):
    id: str
    fileUrl: str
    thumbnailUrl: str
    createdAt: str


class FindingResponse(BaseModel):
    id: str
    templateItemId: Optional[str]
    title: str
    category: str
    weight: int
    requiresPhoto: bool
    isCritical: bool
    result: Optional[str]
    notes: str
    isRepeat: bool
    issueId: Optional[str]
    photos: List[PhotoResponse]


class AuditProgress(BaseModel):
    answered: int
    total: int


class AuditSummaryResponse(BaseModel):
    id: str
    number: str
    templateId: str
    templateName: str
    outlet: str
    auditor: str
    auditDate: str
    status: str                              # in_progress | submitted
    score: Optional[float]
    progress: AuditProgress
    failedCount: int
    repeatCount: int
    submittedAt: Optional[str]


class AuditDetailResponse(AuditSummaryResponse):
    findings: List[FindingResponse]


class OutletScore(BaseModel):
    outlet: str
    latestScore: Optional[float]
    latestDate: Optional[str]
    audits: int
    repeatFindings: int                      # repeat fails across submitted audits


class TrendPoint(BaseModel):
    month: str
    score: float
    audits: int


class ScoresResponse(BaseModel):
    outlets: List[OutletScore]
    trend: Dict[str, List[TrendPoint]]
