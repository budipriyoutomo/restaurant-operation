"""Preventive Maintenance scheduling service.

Pure functions (no DB):
  compute_next_due_date  — advance a due date by a schedule's interval

DB functions:
  generate_due_preventive_work_orders  — idempotent generator (scheduler entry)
  pm_to_response                        — ORM → PMScheduleResponse

Idempotency (Todo-CMMS.md §2.1 / decision #4): a preventive WO is uniquely keyed
by (pm_schedule_id, pm_period_key). The generator both (a) skips a schedule whose
current period already produced a WO, and (b) advances `next_due_date` past the
generated period — so running it twice on the same day never duplicates a WO.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.asset import Asset, WorkOrder, WorkOrderChecklistItem
from app.models.meter_reading import MeterReading
from app.models.pm_schedule import PMSchedule
from app.schemas.pm_schedule import PMScheduleResponse
from app.services.audit_service import write_audit


# ---------------------------------------------------------------------------
# Pure: next-due-date arithmetic
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Add `months` to a date, clamping the day to the target month's length
    (e.g. Jan 31 + 1 month → Feb 28/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_next_due_date(current: date, interval_type: str, interval_value: int) -> date:
    """Return the next due date after `current` for the given interval.

    interval_type: 'days' | 'weeks' | 'months'. Raises ValueError otherwise.
    """
    if interval_value < 1:
        raise ValueError("interval_value must be >= 1")
    if interval_type == "days":
        return current + timedelta(days=interval_value)
    if interval_type == "weeks":
        return current + timedelta(weeks=interval_value)
    if interval_type == "months":
        return _add_months(current, interval_value)
    raise ValueError(f"Unknown interval_type: {interval_type!r}")


# ---------------------------------------------------------------------------
# ORM → schema
# ---------------------------------------------------------------------------

def pm_to_response(sched: PMSchedule, asset_name: Optional[str] = None) -> PMScheduleResponse:
    name = asset_name
    if name is None and sched.asset is not None:
        name = sched.asset.name
    interval_type = (
        sched.interval_type.value if hasattr(sched.interval_type, "value")
        else str(sched.interval_type)
    )
    assignee_role = (
        sched.assignee_role.value if (sched.assignee_role and hasattr(sched.assignee_role, "value"))
        else (str(sched.assignee_role) if sched.assignee_role else None)
    )
    trigger_type = (
        sched.trigger_type.value if hasattr(sched.trigger_type, "value")
        else str(sched.trigger_type)
    )
    return PMScheduleResponse(
        id=str(sched.id),
        assetId=str(sched.asset_id),
        assetName=name or "",
        name=sched.name,
        triggerType=trigger_type,
        intervalType=interval_type,
        intervalValue=sched.interval_value,
        meterInterval=sched.meter_interval,
        lastMeterValue=sched.last_meter_value,
        checklist=list(sched.checklist or []),
        assigneeRole=assignee_role,
        assigneeUserId=str(sched.assignee_user_id) if sched.assignee_user_id else None,
        assigneeName=sched.assignee_name,
        leadTimeDays=sched.lead_time_days,
        nextDueDate=sched.next_due_date.isoformat() if sched.next_due_date else None,
        lastGeneratedAt=sched.last_generated_at.isoformat() if sched.last_generated_at else None,
        isActive=bool(sched.is_active),
        outlet=sched.outlet,
        createdAt=sched.created_at.isoformat() if sched.created_at else "",
    )


# ---------------------------------------------------------------------------
# Pure: meter-based due test
# ---------------------------------------------------------------------------

def meter_due(latest_value: Optional[int], last_meter_value: Optional[int], meter_interval: int) -> bool:
    """A meter schedule is due when usage has advanced at least one interval
    past the value at last generation."""
    if latest_value is None or meter_interval is None or meter_interval < 1:
        return False
    threshold = (last_meter_value or 0) + meter_interval
    return latest_value >= threshold


# ---------------------------------------------------------------------------
# DB: WO number sequence (mirrors issue_service._next_wo_number)
# ---------------------------------------------------------------------------

def _next_wo_number(db: Session) -> str:
    from sqlalchemy import text
    year = datetime.now().year
    result = db.execute(
        text("""
            INSERT INTO work_order_number_sequences (year, last_seq)
            VALUES (:year, 1)
            ON CONFLICT (year) DO UPDATE
              SET last_seq = work_order_number_sequences.last_seq + 1
            RETURNING last_seq
        """),
        {"year": year},
    )
    seq = result.scalar_one()
    return f"WO-{year}-{seq:05d}"


def _resolve_assignee(sched: PMSchedule) -> str:
    if sched.assignee_name:
        return sched.assignee_name
    if sched.assignee_role is not None:
        return sched.assignee_role.value if hasattr(sched.assignee_role, "value") else str(sched.assignee_role)
    return "Unassigned"


# ---------------------------------------------------------------------------
# DB: idempotent generator
# ---------------------------------------------------------------------------

def generate_due_preventive_work_orders(
    db: Session,
    today: Optional[date] = None,
) -> List[WorkOrder]:
    """Create preventive WOs for every due, active schedule. Idempotent.

    A schedule is "due" when `next_due_date <= today + lead_time_days`.
    Returns the list of newly-created work orders (empty if nothing was due).
    Commits once at the end.
    """
    today = today or date.today()

    schedules = (
        db.query(PMSchedule)
        .filter(
            PMSchedule.is_active.is_(True),
            PMSchedule.deleted_at.is_(None),
        )
        .all()
    )

    created: List[WorkOrder] = []

    for sched in schedules:
        trigger = sched.trigger_type.value if hasattr(sched.trigger_type, "value") else str(sched.trigger_type)

        if trigger == "meter":
            wo = _maybe_generate_meter(db, sched, created)
        else:
            wo = _maybe_generate_calendar(db, sched, today, created)

    db.commit()
    for wo in created:
        db.refresh(wo)
    return created


def _already_generated(db: Session, sched: PMSchedule, period_key: str) -> bool:
    return (
        db.query(WorkOrder.id)
        .filter(
            WorkOrder.pm_schedule_id == sched.id,
            WorkOrder.pm_period_key == period_key,
        )
        .first()
        is not None
    )


def _spawn_pm_wo(db: Session, sched: PMSchedule, period_key: str,
                 scheduled_date: Optional[date]) -> WorkOrder:
    asset = db.query(Asset).filter(Asset.id == sched.asset_id).first()
    asset_name = asset.name if asset else sched.name

    wo = WorkOrder(
        number=_next_wo_number(db),
        type="preventive",
        asset_id=sched.asset_id,
        asset_name=asset_name,
        outlet=sched.outlet,
        title=f"[Preventif] {sched.name}",
        description="",
        priority="medium",
        status="scheduled",
        assignee=_resolve_assignee(sched),
        scheduled_date=scheduled_date,
        pm_schedule_id=sched.id,
        pm_period_key=period_key,
    )
    db.add(wo)
    db.flush()  # need wo.id for checklist items

    for idx, title in enumerate(sched.checklist or []):
        db.add(WorkOrderChecklistItem(work_order_id=wo.id, title=str(title), order_index=idx))

    write_audit(
        db,
        table_name="work_orders",
        record_id=str(wo.id),
        action="pm_generated",
        new_value={"number": wo.number, "pmScheduleId": str(sched.id), "period": period_key},
    )
    sched.last_generated_at = datetime.now(timezone.utc)
    return wo


def _maybe_generate_calendar(db: Session, sched: PMSchedule, today: date,
                             created: List[WorkOrder]) -> Optional[WorkOrder]:
    if sched.next_due_date is None:
        return None
    # lead_time_days is per-schedule → apply the "due" test in Python.
    if sched.next_due_date > today + timedelta(days=sched.lead_time_days):
        return None

    interval_type = sched.interval_type.value if hasattr(sched.interval_type, "value") else str(sched.interval_type)
    period_key = sched.next_due_date.isoformat()

    if _already_generated(db, sched, period_key):
        # WO exists but next_due_date wasn't advanced (e.g. prior crash) — heal it.
        sched.next_due_date = compute_next_due_date(sched.next_due_date, interval_type, sched.interval_value)
        return None

    wo = _spawn_pm_wo(db, sched, period_key, scheduled_date=sched.next_due_date)
    sched.next_due_date = compute_next_due_date(sched.next_due_date, interval_type, sched.interval_value)
    created.append(wo)
    return wo


def _latest_meter_value(db: Session, asset_id) -> Optional[int]:
    # A meter reading (running hours, cycles) is cumulative & monotonic, so the
    # current value is the highest recorded — robust even when several readings
    # share a timestamp.
    from sqlalchemy import func
    val = (
        db.query(func.max(MeterReading.value))
        .filter(MeterReading.asset_id == asset_id)
        .scalar()
    )
    return int(val) if val is not None else None


def _maybe_generate_meter(db: Session, sched: PMSchedule,
                          created: List[WorkOrder]) -> Optional[WorkOrder]:
    latest = _latest_meter_value(db, sched.asset_id)
    if not meter_due(latest, sched.last_meter_value, sched.meter_interval):
        return None

    threshold = (sched.last_meter_value or 0) + sched.meter_interval
    period_key = f"m{threshold}"

    if _already_generated(db, sched, period_key):
        sched.last_meter_value = threshold
        return None

    wo = _spawn_pm_wo(db, sched, period_key, scheduled_date=date.today())
    sched.last_meter_value = threshold
    created.append(wo)
    return wo
