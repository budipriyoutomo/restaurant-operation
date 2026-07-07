from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset
from app.models.enums import PMIntervalTypeEnum
from app.models.pm_schedule import PMSchedule
from app.schemas.pm_schedule import (
    CreatePMScheduleRequest,
    PMScheduleResponse,
    RunGeneratorResponse,
    UpdatePMScheduleRequest,
)
from app.services import pm_schedule_service as pm_svc
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/pm-schedules", tags=["cmms"])

_VALID_INTERVALS = {e.value for e in PMIntervalTypeEnum}


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _require_asset(db: Session, asset_id: str) -> Asset:
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def _validate_interval(interval_type: str) -> None:
    if interval_type not in _VALID_INTERVALS:
        raise HTTPException(
            status_code=422,
            detail=f"intervalType must be one of {sorted(_VALID_INTERVALS)}",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[PMScheduleResponse])
def list_pm_schedules(
    asset_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(PMSchedule).filter(PMSchedule.deleted_at.is_(None))
    if asset_id:
        q = q.filter(PMSchedule.asset_id == asset_id)
    if active_only:
        q = q.filter(PMSchedule.is_active.is_(True))
    schedules = q.order_by(PMSchedule.next_due_date).all()
    return [pm_svc.pm_to_response(s) for s in schedules]


@router.post("", response_model=PMScheduleResponse, status_code=201)
def create_pm_schedule(
    req: CreatePMScheduleRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    _validate_interval(req.intervalType)
    if req.triggerType not in {"calendar", "meter"}:
        raise HTTPException(status_code=422, detail="triggerType must be 'calendar' or 'meter'")
    asset = _require_asset(db, req.assetId)

    next_due = None
    if req.triggerType == "meter":
        if not req.meterInterval or req.meterInterval < 1:
            raise HTTPException(status_code=422, detail="meterInterval (>=1) is required for meter schedules")
    else:
        next_due = _parse_date(req.nextDueDate)
        if next_due is None:
            raise HTTPException(status_code=422, detail="nextDueDate (ISO date) is required for calendar schedules")

    sched = PMSchedule(
        asset_id=asset.id,
        name=req.name,
        trigger_type=req.triggerType,
        interval_type=req.intervalType,
        interval_value=req.intervalValue,
        meter_interval=req.meterInterval,
        checklist=list(req.checklist or []),
        assignee_role=req.assigneeRole,
        assignee_user_id=req.assigneeUserId,
        assignee_name=req.assigneeName,
        lead_time_days=req.leadTimeDays,
        next_due_date=next_due,
        is_active=req.isActive,
        outlet=asset.outlet,
    )
    db.add(sched)
    db.flush()
    write_audit(db, table_name="pm_schedules", record_id=str(sched.id), action="create",
                new_value={"name": sched.name, "assetId": str(asset.id)})
    db.commit()
    db.refresh(sched)
    return pm_svc.pm_to_response(sched, asset_name=asset.name)


@router.get("/{schedule_id}", response_model=PMScheduleResponse)
def get_pm_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    sched = (
        db.query(PMSchedule)
        .filter(PMSchedule.id == schedule_id, PMSchedule.deleted_at.is_(None))
        .first()
    )
    if not sched:
        raise HTTPException(status_code=404, detail="PM schedule not found")
    return pm_svc.pm_to_response(sched)


@router.patch("/{schedule_id}", response_model=PMScheduleResponse)
def update_pm_schedule(
    schedule_id: str,
    req: UpdatePMScheduleRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    sched = (
        db.query(PMSchedule)
        .filter(PMSchedule.id == schedule_id, PMSchedule.deleted_at.is_(None))
        .first()
    )
    if not sched:
        raise HTTPException(status_code=404, detail="PM schedule not found")

    if req.triggerType is not None:
        if req.triggerType not in {"calendar", "meter"}:
            raise HTTPException(status_code=422, detail="triggerType must be 'calendar' or 'meter'")
        sched.trigger_type = req.triggerType
    if req.meterInterval is not None:
        sched.meter_interval = req.meterInterval
    if req.intervalType is not None:
        _validate_interval(req.intervalType)
        sched.interval_type = req.intervalType
    if req.name is not None:
        sched.name = req.name
    if req.intervalValue is not None:
        sched.interval_value = req.intervalValue
    if req.checklist is not None:
        sched.checklist = list(req.checklist)
    if req.assigneeRole is not None:
        sched.assignee_role = req.assigneeRole
    if req.assigneeUserId is not None:
        sched.assignee_user_id = req.assigneeUserId
    if req.assigneeName is not None:
        sched.assignee_name = req.assigneeName
    if req.leadTimeDays is not None:
        sched.lead_time_days = req.leadTimeDays
    if req.nextDueDate is not None:
        parsed = _parse_date(req.nextDueDate)
        if parsed is None:
            raise HTTPException(status_code=422, detail="nextDueDate must be an ISO date (YYYY-MM-DD)")
        sched.next_due_date = parsed
    if req.isActive is not None:
        sched.is_active = req.isActive

    sched.updated_at = datetime.now(timezone.utc)
    write_audit(db, table_name="pm_schedules", record_id=str(sched.id), action="update",
                new_value={"name": sched.name})
    db.commit()
    db.refresh(sched)
    return pm_svc.pm_to_response(sched)


@router.delete("/{schedule_id}", status_code=204)
def delete_pm_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    sched = (
        db.query(PMSchedule)
        .filter(PMSchedule.id == schedule_id, PMSchedule.deleted_at.is_(None))
        .first()
    )
    if not sched:
        raise HTTPException(status_code=404, detail="PM schedule not found")
    sched.deleted_at = datetime.now(timezone.utc)
    sched.is_active = False
    write_audit(db, table_name="pm_schedules", record_id=str(sched.id), action="delete",
                old_value={"name": sched.name})
    db.commit()


@router.post("/run-now", response_model=RunGeneratorResponse)
def run_generator_now(
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    """Manually trigger the preventive-WO generator (demo/pilot). Idempotent."""
    evaluated = (
        db.query(PMSchedule)
        .filter(PMSchedule.is_active.is_(True), PMSchedule.deleted_at.is_(None))
        .count()
    )
    created = pm_svc.generate_due_preventive_work_orders(db)
    return RunGeneratorResponse(
        generated=len(created),
        workOrderIds=[str(wo.id) for wo in created],
        schedulesEvaluated=evaluated,
    )
