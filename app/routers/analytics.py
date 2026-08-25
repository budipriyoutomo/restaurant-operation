from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.approval import ApprovalRequest
from app.models.issue import Issue
from app.models.task import Task
from app.services import cmms_analytics_service as cmms_svc
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# CMMS analytics response shapes (mirror frontend lib/types.ts)
# ---------------------------------------------------------------------------

class AssetAnalytics(BaseModel):
    assetId: str
    assetName: str
    outlet: str
    workOrders: int
    failures: int
    mttrHours: float
    mtbfHours: float
    uptimePct: float
    totalDowntimeHours: float
    totalCost: int
    repairCost: int
    purchaseCost: Optional[int] = None
    repairVsReplace: Optional[bool] = None


class FleetAnalytics(BaseModel):
    assetCount: int
    avgMttrHours: float
    avgMtbfHours: float
    avgUptimePct: float
    totalCost: int
    replaceCandidates: int


class CMMSAnalyticsResponse(BaseModel):
    perAsset: List[AssetAnalytics]
    fleet: FleetAnalytics


def _val(field) -> str:
    return field.value if hasattr(field, "value") else str(field)


def _sla_breach(due_date, status_value: str) -> bool:
    if due_date is None:
        return False
    if status_value in ("resolved", "closed"):
        return False
    return due_date < date.today()


@router.get("/summary")
def get_summary(db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    issues = db.query(Issue).all()

    issue_by_status: Dict[str, int] = {}
    issue_by_priority: Dict[str, int] = {}
    issue_by_category: Dict[str, int] = {}
    issue_by_outlet: Dict[str, int] = {}
    sla_breach_count = 0

    for issue in issues:
        status   = _val(issue.status)
        priority = _val(issue.priority)
        category = _val(issue.category)
        outlet   = issue.outlet or "Unknown"

        issue_by_status[status]     = issue_by_status.get(status, 0) + 1
        issue_by_priority[priority] = issue_by_priority.get(priority, 0) + 1
        issue_by_category[category] = issue_by_category.get(category, 0) + 1
        issue_by_outlet[outlet]     = issue_by_outlet.get(outlet, 0) + 1

        if _sla_breach(issue.due_date, status):
            sla_breach_count += 1

    tasks = db.query(Task).all()
    task_by_status: Dict[str, int] = {}
    for task in tasks:
        status = _val(task.status)
        task_by_status[status] = task_by_status.get(status, 0) + 1

    approvals = db.query(ApprovalRequest).all()
    approval_by_status: Dict[str, int] = {}
    approval_by_type: Dict[str, int] = {}
    for approval in approvals:
        status = _val(approval.status)
        atype  = _val(approval.type)
        approval_by_status[status] = approval_by_status.get(status, 0) + 1
        approval_by_type[atype]    = approval_by_type.get(atype, 0) + 1

    return {
        "issues": {
            "total":       len(issues),
            "sla_breach":  sla_breach_count,
            "by_status":   issue_by_status,
            "by_priority": issue_by_priority,
            "by_category": issue_by_category,
            "by_outlet":   issue_by_outlet,
        },
        "tasks": {
            "total":     len(tasks),
            "by_status": task_by_status,
        },
        "approvals": {
            "total":     len(approvals),
            "by_status": approval_by_status,
            "by_type":   approval_by_type,
        },
    }


@router.get("/budget")
def get_budget_status(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Budget vs actual spend (WO + PO) per outlet for a month (Tier 6.3),
    outlet-scoped for non-admins."""
    from app.services import budget_service
    return budget_service.compute_budget_status(db, period, user=current_user)


@router.get("/cmms", response_model=CMMSAnalyticsResponse)
def get_cmms_analytics(
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Per-asset reliability & cost metrics + fleet roll-up (Tier 3).

    MTTR / MTBF / uptime% / total cost per asset, plus a repair-vs-replace flag
    when the asset's purchase cost is known.
    """
    return cmms_svc.compute_fleet_analytics(db, outlet=outlet)
