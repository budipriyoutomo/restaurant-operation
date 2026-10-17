"""Daily ops checklist — database side (Todo-Ops-Checklist Fase 1 §1.4).

  templates   CRUD; items deactivated, never deleted (runs point at them)
  schedules   CRUD per outlet (soft delete)
  generator   generate_due_runs: one run per (schedule, shift date) — the UNIQUE
              constraint is the idempotency key; a race loses on it inside a
              savepoint. mark_missed_runs locks runs whose window closed.
  answering   answer_item / add_photo while the window is open
  submit      submit_run: one transaction — checks, score, late flag, and per
              failed item either a new Issue (create_issue, commit=False, routed
              assignee) or, when that item already has an open Issue at this
              outlet, a system comment on it and a one-time raise to high.
Pure rules: app/services/ops_checklist_rules.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.category import Category
from app.models.issue import Issue
from app.models.ops_checklist import (
    OpsChecklistPhoto, OpsChecklistRun, OpsChecklistRunItem, OpsChecklistSchedule, OpsChecklistTemplate,
    OpsChecklistTemplateItem,
)
from app.models.outlet import Outlet
from app.schemas.ops_checklist import (
    AnswerItemRequest, CreateScheduleRequest, CreateTemplateRequest, PhotoResponse, RunDetailResponse,
    RunItemResponse, RunSummaryResponse, ScheduleResponse, TemplateItemResponse, TemplateResponse,
    UpdateScheduleRequest, UpdateTemplateRequest,
)
from app.services import ops_checklist_rules as R
from app.services import storage_service
from app.services.audit_service import write_audit
from app.services.category_service import system_category
from app.services.numbering import next_number
from app.services.outlet_scope_service import assert_can_access, scoped_query

# Days to fix a failed item, by the priority of the Issue it raises (as QA audits).
_DUE_DAYS = {"critical": 1, "high": 3, "medium": 7}
_ISSUE_FINAL = ("resolved", "closed", "cancelled")
_OPEN_STATUSES = ("pending", "in_progress")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _uuid(value, what: str, status: int = 422) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status, detail=f"Unknown {what}")


def _fmt_value(item) -> str:
    if item.value is None:
        return ""
    return f"{float(item.value):g}{(' ' + item.unit) if item.unit else ''}"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def template_to_response(t: OpsChecklistTemplate, db: Optional[Session] = None) -> TemplateResponse:
    ids = {i.asset_id for i in t.items if i.asset_id}
    asset_names = dict(db.query(Asset.id, Asset.name).filter(Asset.id.in_(ids)).all()) if (db is not None and ids) else {}
    return TemplateResponse(
        id=str(t.id), name=t.name, description=t.description or "", shift=t.shift, isActive=bool(t.is_active),
        items=[
            TemplateItemResponse(
                id=str(i.id), title=i.title, section=i.section or "", itemType=i.item_type,
                minValue=_f(i.min_value), maxValue=_f(i.max_value), unit=i.unit or "",
                requiresPhoto=bool(i.requires_photo), isCritical=bool(i.is_critical),
                issueCategoryId=str(i.issue_category_id) if i.issue_category_id else None,
                assetId=str(i.asset_id) if i.asset_id else None, assetName=asset_names.get(i.asset_id),
            )
            for i in t.items if i.is_active
        ],
    )


def _issue_category_id(db: Session, category_id: Optional[str]) -> Optional[uuid.UUID]:
    if not category_id:
        return None
    cid = _uuid(category_id, "issue category")
    found = db.query(Category.id).filter(Category.id == cid, Category.deleted_at.is_(None),
                                         Category.is_active.is_(True)).first()
    if found is None:
        raise HTTPException(status_code=422, detail="Unknown or inactive issue category")
    return cid


def _monitored_asset_id(db: Session, asset_id: Optional[str]) -> Optional[uuid.UUID]:
    if not asset_id:
        return None
    aid = _uuid(asset_id, "asset")
    asset = db.query(Asset).filter(Asset.id == aid).first()
    if asset is None or not asset.temp_monitored:
        raise HTTPException(status_code=422, detail="Unknown asset or asset not temperature-monitored")
    return aid


def _apply_items(db: Session, t: OpsChecklistTemplate, items) -> None:
    existing = {str(i.id): i for i in t.items}
    keep = set()
    for idx, it in enumerate(items):
        row = existing.get(it.id) if it.id else None
        if it.id and row is None:
            raise HTTPException(status_code=422, detail=f"Unknown template item id {it.id}")
        if row is None:
            row = OpsChecklistTemplateItem()
            t.items.append(row)
        row.title, row.section, row.item_type = it.title.strip(), it.section.strip(), it.itemType
        row.min_value, row.max_value, row.unit = it.minValue, it.maxValue, it.unit
        row.requires_photo, row.is_critical = it.requiresPhoto, it.isCritical
        row.issue_category_id = _issue_category_id(db, it.issueCategoryId)
        row.asset_id = _monitored_asset_id(db, it.assetId)
        row.order_index, row.is_active = idx, True
        if it.id:
            keep.add(it.id)
    for item_id, row in existing.items():
        if item_id not in keep:
            row.is_active = False


def _template_snapshot(t: OpsChecklistTemplate) -> dict:
    return {"name": t.name, "shift": t.shift, "isActive": t.is_active,
            "items": sum(1 for i in t.items if i.is_active)}


def create_template(db: Session, req: CreateTemplateRequest, actor: str) -> TemplateResponse:
    t = OpsChecklistTemplate(name=req.name.strip(), description=req.description, shift=req.shift,
                             is_active=req.isActive)
    db.add(t)
    _apply_items(db, t, req.items)
    db.flush()
    write_audit(db, table_name="ops_checklist_templates", record_id=str(t.id), action="create",
                new_value=_template_snapshot(t), performed_by=actor)
    db.commit()
    db.refresh(t)
    return template_to_response(t, db)


def update_template(db: Session, t: OpsChecklistTemplate, req: UpdateTemplateRequest, actor: str) -> TemplateResponse:
    old = _template_snapshot(t)
    if req.name is not None:
        t.name = req.name.strip()
    if req.description is not None:
        t.description = req.description
    if req.shift is not None:
        t.shift = req.shift
    if req.isActive is not None:
        t.is_active = req.isActive
    if req.items is not None:
        _apply_items(db, t, req.items)
    db.flush()
    write_audit(db, table_name="ops_checklist_templates", record_id=str(t.id), action="update",
                old_value=old, new_value=_template_snapshot(t), performed_by=actor)
    db.commit()
    db.refresh(t)
    return template_to_response(t, db)


def get_template_or_404(db: Session, template_id: str) -> OpsChecklistTemplate:
    t = db.query(OpsChecklistTemplate).filter(OpsChecklistTemplate.id == _uuid(template_id, "template", 404)).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def schedule_to_response(s: OpsChecklistSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=str(s.id), templateId=str(s.template_id), templateName=s.template.name if s.template else "",
        shift=s.template.shift if s.template else "other", outlet=s.outlet, outletId=str(s.outlet_id),
        timezone=(s.outlet_ref.timezone if s.outlet_ref else None) or "Asia/Jakarta",
        frequency=s.frequency or "daily", daysOfWeek=list(s.days_of_week or []), dayOfMonth=s.day_of_month,
        dueTime=s.due_time.strftime("%H:%M"),
        openMinutesBefore=s.open_minutes_before, graceMinutes=s.grace_minutes, isActive=bool(s.is_active),
    )


def _schedule_snapshot(s: OpsChecklistSchedule) -> dict:
    return {"templateId": str(s.template_id), "outlet": s.outlet, "frequency": s.frequency,
            "daysOfWeek": list(s.days_of_week or []), "dayOfMonth": s.day_of_month,
            "dueTime": s.due_time.strftime("%H:%M") if s.due_time else None,
            "openMinutesBefore": s.open_minutes_before, "graceMinutes": s.grace_minutes, "isActive": s.is_active}


def _active_template(db: Session, template_id: str) -> OpsChecklistTemplate:
    t = db.query(OpsChecklistTemplate).filter(OpsChecklistTemplate.id == _uuid(template_id, "template")).first()
    if t is None or not t.is_active:
        raise HTTPException(status_code=422, detail="Unknown or inactive template")
    return t


def _assert_assets_at_outlet(db: Session, t: OpsChecklistTemplate, outlet_id) -> None:
    """Asset-bound items measure equipment at one outlet: the schedule must be there."""
    ids = {i.asset_id for i in t.items if i.is_active and i.asset_id}
    if not ids:
        return
    elsewhere = [name for name, oid in db.query(Asset.name, Asset.outlet_id).filter(Asset.id.in_(ids)) if oid != outlet_id]
    if elsewhere:
        raise HTTPException(status_code=422, detail=f"Template measures equipment at another outlet: {', '.join(sorted(elsewhere))}")


def _assert_shape(frequency: str, days_of_week, day_of_month, open_minutes_before: int, grace_minutes: int) -> None:
    problem = R.schedule_problem(frequency, days_of_week or [], day_of_month, open_minutes_before, grace_minutes)
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def create_schedule(db: Session, req: CreateScheduleRequest, outlet: Outlet, actor: str) -> ScheduleResponse:
    _assert_shape(req.frequency, req.daysOfWeek, req.dayOfMonth, req.openMinutesBefore, req.graceMinutes)
    t = _active_template(db, req.templateId)
    _assert_assets_at_outlet(db, t, outlet.id)
    s = OpsChecklistSchedule(
        template_id=t.id, outlet=outlet.name, outlet_id=outlet.id, frequency=req.frequency,
        days_of_week=req.daysOfWeek, day_of_month=req.dayOfMonth,
        due_time=req.dueTime.replace(second=0, microsecond=0, tzinfo=None),
        open_minutes_before=req.openMinutesBefore, grace_minutes=req.graceMinutes, is_active=req.isActive,
    )
    db.add(s)
    db.flush()
    write_audit(db, table_name="ops_checklist_schedules", record_id=str(s.id), action="create",
                new_value=_schedule_snapshot(s), performed_by=actor)
    db.commit()
    db.refresh(s)
    return schedule_to_response(s)


def update_schedule(db: Session, s: OpsChecklistSchedule, req: UpdateScheduleRequest, actor: str) -> ScheduleResponse:
    old = _schedule_snapshot(s)
    if req.templateId is not None:
        t = _active_template(db, req.templateId)
        _assert_assets_at_outlet(db, t, s.outlet_id)
        s.template_id = t.id
    if req.frequency is not None:
        s.frequency = req.frequency
    if req.daysOfWeek is not None:
        s.days_of_week = req.daysOfWeek
    # dayOfMonth belongs to monthly schedules only: leaving monthly clears it.
    if s.frequency != "monthly":
        s.day_of_month = None
    elif req.dayOfMonth is not None:
        s.day_of_month = req.dayOfMonth
    if req.dueTime is not None:
        s.due_time = req.dueTime.replace(second=0, microsecond=0, tzinfo=None)
    if req.openMinutesBefore is not None:
        s.open_minutes_before = req.openMinutesBefore
    if req.graceMinutes is not None:
        s.grace_minutes = req.graceMinutes
    if req.isActive is not None:
        s.is_active = req.isActive
    _assert_shape(s.frequency, s.days_of_week, s.day_of_month, s.open_minutes_before, s.grace_minutes)
    db.flush()
    write_audit(db, table_name="ops_checklist_schedules", record_id=str(s.id), action="update",
                old_value=old, new_value=_schedule_snapshot(s), performed_by=actor)
    db.commit()
    db.refresh(s)
    return schedule_to_response(s)


def delete_schedule(db: Session, s: OpsChecklistSchedule, actor: str) -> None:
    """Soft delete: past runs keep pointing at it; no new runs."""
    s.deleted_at, s.is_active = _now(), False
    write_audit(db, table_name="ops_checklist_schedules", record_id=str(s.id), action="delete",
                old_value=_schedule_snapshot(s), performed_by=actor)
    db.commit()


def schedules_query(db: Session, user):
    return scoped_query(db, OpsChecklistSchedule, user).filter(OpsChecklistSchedule.deleted_at.is_(None))


def get_schedule_or_404(db: Session, schedule_id: str, user) -> OpsChecklistSchedule:
    s = schedules_query(db, user).filter(OpsChecklistSchedule.id == _uuid(schedule_id, "schedule", 404)).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return s


# ---------------------------------------------------------------------------
# Generator & missed runs (cron: scripts/run_ops_checklists.py)
# ---------------------------------------------------------------------------

def _snapshot_items(db: Session, template: OpsChecklistTemplate, outlet_id) -> List[OpsChecklistRunItem]:
    """Copy the active items. An asset-bound item takes the asset's limits unless
    it sets its own, and is left out when the asset is not at this outlet."""
    from app.services.temperature_rules import effective_limits
    ids = {i.asset_id for i in template.items if i.is_active and i.asset_id}
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(ids))} if ids else {}
    rows = []
    for i in template.items:
        if not i.is_active:
            continue
        asset = assets.get(i.asset_id) if i.asset_id else None
        if i.asset_id and (asset is None or asset.outlet_id != outlet_id):
            continue
        lo, hi = effective_limits(i.min_value, i.max_value, asset.temp_min if asset else None,
                                  asset.temp_max if asset else None)
        rows.append(OpsChecklistRunItem(
            template_item_id=i.id, title=i.title, section=i.section, item_type=i.item_type,
            min_value=lo, max_value=hi, unit=i.unit or ("°C" if asset else ""), requires_photo=i.requires_photo,
            is_critical=i.is_critical, issue_category_id=i.issue_category_id, order_index=i.order_index,
            asset_id=asset.id if asset else None, asset_name=asset.name if asset else None,
        ))
    return rows


def _generate_one(db: Session, s: OpsChecklistSchedule, template: OpsChecklistTemplate, tz: str, run_date: date,
                  today: date, now: datetime) -> Optional[OpsChecklistRun]:
    if not R.is_scheduled_on(s.frequency or "daily", s.days_of_week or [], s.day_of_month, run_date):
        return None
    opens, due, closes = R.compute_run_window(run_date, s.due_time, tz, s.open_minutes_before, s.grace_minutes)
    if run_date != today and opens > now:
        return None       # a later date's run appears once its window opens
    if closes < now:
        return None       # a schedule created after the window: don't start it as "missed"
    exists = db.query(OpsChecklistRun.id).filter(OpsChecklistRun.schedule_id == s.id,
                                                 OpsChecklistRun.run_date == run_date).first()
    if exists:
        return None
    items = _snapshot_items(db, template, s.outlet_id)
    if not items:
        return None
    savepoint = db.begin_nested()
    try:
        run = OpsChecklistRun(
            number=next_number(db, "OPS", "ops_checklist_number_sequences"),
            schedule_id=s.id, template_id=template.id, template_name=template.name, shift=template.shift,
            outlet=s.outlet, outlet_id=s.outlet_id, run_date=run_date,
            opens_at=opens, due_at=due, closes_at=closes, status="pending", items=items,
        )
        db.add(run)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return None
    write_audit(db, table_name="ops_checklist_runs", record_id=str(run.id), action="generate",
                new_value={"number": run.number, "outlet": run.outlet, "runDate": run_date.isoformat()})
    return run


def generate_due_runs(db: Session, now: Optional[datetime] = None) -> List[OpsChecklistRun]:
    """Create the runs of every active schedule: today's (outlet-local shift date)
    and, for weekly/monthly schedules that open days ahead, a later date's run
    once its window is open. Safe to call any number of times: an existing
    (schedule, date) run is skipped, and a concurrent duplicate loses on the
    UNIQUE constraint."""
    now = now or _now()
    created: List[OpsChecklistRun] = []
    schedules = (db.query(OpsChecklistSchedule)
                 .filter(OpsChecklistSchedule.is_active.is_(True), OpsChecklistSchedule.deleted_at.is_(None)).all())
    for s in schedules:
        template = s.template
        if template is None or not template.is_active:
            continue
        tz = (s.outlet_ref.timezone if s.outlet_ref else None) or "Asia/Jakarta"
        today = R.local_date(now, tz)
        for run_date in R.candidate_run_dates(today, s.open_minutes_before):
            run = _generate_one(db, s, template, tz, run_date, today, now)
            if run is not None:
                created.append(run)
    db.commit()
    return created


def _notify_missed(db: Session, r: OpsChecklistRun) -> None:
    """Managers of the outlet (in-app; WhatsApp when opted in) — Fase 3."""
    from app.services.notification_service import notify_roles
    from app.services.notification_texts import L
    notify_roles(
        db, ["manager"],
        title=L(f"Checklist terlewat: {r.template_name}", f"Checklist missed: {r.template_name}"),
        message=L(f"{r.outlet} — {r.number} ({r.run_date.strftime('%d-%m-%Y')}) tidak dikirim sampai jendela tutup.",
                  f"{r.outlet} — {r.number} ({r.run_date.strftime('%d-%m-%Y')}) was not submitted before its window closed."),
        ntype="warning", entity_type="ops_checklists", entity_id=r.id, outlet_id=r.outlet_id,
        event="ops_checklist_missed",
    )


def mark_missed_runs(db: Session, now: Optional[datetime] = None) -> List[OpsChecklistRun]:
    """Lock runs still open after their window closed and tell the outlet's
    managers. Idempotent: a run turns missed — and is announced — once."""
    now = now or _now()
    runs = (db.query(OpsChecklistRun)
            .filter(OpsChecklistRun.status.in_(_OPEN_STATUSES), OpsChecklistRun.closes_at < now).all())
    for r in runs:
        r.status = "missed"
        write_audit(db, table_name="ops_checklist_runs", record_id=str(r.id), action="missed",
                    new_value={"number": r.number, "outlet": r.outlet, "runDate": r.run_date.isoformat()})
        _notify_missed(db, r)
    db.commit()
    return runs


DUE_SOON = timedelta(minutes=30)


def remind_due_soon(db: Session, now: Optional[datetime] = None) -> List[OpsChecklistRun]:
    """Runs nobody has started, due within 30 minutes: remind the outlet's staff
    once (in-app; WhatsApp only for those who opted in to waOpsDueSoon)."""
    from app.services.notification_service import notify_roles
    from app.services.notification_texts import L
    now = now or _now()
    runs = (db.query(OpsChecklistRun)
            .filter(OpsChecklistRun.status == "pending", OpsChecklistRun.due_soon_notified_at.is_(None),
                    OpsChecklistRun.opens_at <= now, OpsChecklistRun.due_at > now,
                    OpsChecklistRun.due_at <= now + DUE_SOON).all())
    for r in runs:
        minutes = max(1, int((r.due_at - now).total_seconds() // 60))
        notify_roles(
            db, ["staff"],
            title=L(f"Checklist belum diisi: {r.template_name}", f"Checklist not started: {r.template_name}"),
            message=L(f"{r.outlet} — tenggat {minutes} menit lagi ({r.number}).",
                      f"{r.outlet} — due in {minutes} minutes ({r.number})."),
            ntype="info", entity_type="ops_checklists", entity_id=r.id, outlet_id=r.outlet_id,
            event="ops_checklist_due_soon",
        )
        r.due_soon_notified_at = now
    db.commit()
    return runs


# ---------------------------------------------------------------------------
# Runs: read
# ---------------------------------------------------------------------------

def _photo_response(run_id, item_id, p: OpsChecklistPhoto) -> PhotoResponse:
    base = f"/api/ops-checklists/{run_id}/items/{item_id}/photos/{p.id}"
    return PhotoResponse(id=str(p.id), fileUrl=f"{base}/file", thumbnailUrl=f"{base}/thumbnail",
                         createdAt=p.created_at.isoformat() if p.created_at else "")


def _item_response(run_id, i: OpsChecklistRunItem, issue_numbers: Dict) -> RunItemResponse:
    return RunItemResponse(
        id=str(i.id), templateItemId=str(i.template_item_id) if i.template_item_id else None,
        title=i.title, section=i.section or "", itemType=i.item_type,
        minValue=_f(i.min_value), maxValue=_f(i.max_value), unit=i.unit or "",
        requiresPhoto=bool(i.requires_photo), isCritical=bool(i.is_critical),
        assetId=str(i.asset_id) if i.asset_id else None, assetName=i.asset_name,
        result=i.result, value=_f(i.value), notes=i.notes or "", correctiveAction=i.corrective_action,
        recordedAt=i.recorded_at.isoformat() if i.recorded_at else None,
        issueId=str(i.issue_id) if i.issue_id else None, issueNumber=issue_numbers.get(i.issue_id),
        photos=[_photo_response(run_id, i.id, p) for p in i.photos],
    )


def _summary_fields(r: OpsChecklistRun) -> dict:
    items = r.items or []
    return dict(
        id=str(r.id), number=r.number, scheduleId=str(r.schedule_id), templateId=str(r.template_id),
        templateName=r.template_name, shift=r.shift, outlet=r.outlet, outletId=str(r.outlet_id),
        runDate=r.run_date.isoformat(), opensAt=r.opens_at.isoformat(), dueAt=r.due_at.isoformat(),
        closesAt=r.closes_at.isoformat(), status=r.status, isLate=bool(r.is_late),
        submittedByName=r.submitted_by_name, submittedAt=r.submitted_at.isoformat() if r.submitted_at else None,
        score=_f(r.score), itemCount=len(items), answeredCount=sum(1 for i in items if i.result),
        failedCount=sum(1 for i in items if i.result == "fail"),
        criticalFailed=any(i.result == "fail" and i.is_critical for i in items),
    )


def run_to_summary(r: OpsChecklistRun) -> RunSummaryResponse:
    return RunSummaryResponse(**_summary_fields(r))


def run_to_detail(db: Session, r: OpsChecklistRun) -> RunDetailResponse:
    ids = {i.issue_id for i in r.items if i.issue_id}
    numbers = dict(db.query(Issue.id, Issue.number).filter(Issue.id.in_(ids)).all()) if ids else {}
    return RunDetailResponse(**_summary_fields(r), items=[_item_response(r.id, i, numbers) for i in r.items])


def _today_by_outlet(db: Session, now: datetime) -> Dict[uuid.UUID, date]:
    return {o.id: R.local_date(now, o.timezone or "Asia/Jakarta") for o in db.query(Outlet)}


def runs_query(db: Session, user, *, run_date: Optional[date] = None, date_from: Optional[date] = None,
               date_to: Optional[date] = None, outlet: Optional[str] = None, status: Optional[str] = None):
    q = scoped_query(db, OpsChecklistRun, user)
    if outlet:
        q = q.filter(OpsChecklistRun.outlet == outlet)
    if status:
        q = q.filter(OpsChecklistRun.status == status)
    if run_date:
        q = q.filter(OpsChecklistRun.run_date == run_date)
    if date_from:
        q = q.filter(OpsChecklistRun.run_date >= date_from)
    if date_to:
        q = q.filter(OpsChecklistRun.run_date <= date_to)
    return q


def list_today(db: Session, user, *, outlet: Optional[str] = None, status: Optional[str] = None,
               now: Optional[datetime] = None) -> List[OpsChecklistRun]:
    """Runs of *today at each outlet* — outlets in different zones can be on different
    dates — plus runs of another date whose window is open now (weekly deep cleaning
    due Sunday, open since Friday)."""
    now = now or _now()
    utc_today = now.date()
    today = _today_by_outlet(db, now)
    rows = runs_query(db, user, date_from=utc_today - timedelta(days=1), date_to=utc_today + timedelta(days=1),
                      outlet=outlet, status=status).order_by(OpsChecklistRun.due_at).all()
    todays = [r for r in rows if today.get(r.outlet_id) == r.run_date]
    seen = {r.id for r in todays}
    open_now = (runs_query(db, user, outlet=outlet, status=status)
                .filter(OpsChecklistRun.status.in_(_OPEN_STATUSES),
                        OpsChecklistRun.opens_at <= now, OpsChecklistRun.closes_at >= now).all())
    extra = [r for r in open_now if r.id not in seen]
    return sorted(todays + extra, key=lambda r: r.due_at)


def my_open_runs(db: Session, user, now: Optional[datetime] = None) -> List[OpsChecklistRun]:
    """My Work: today's runs at my outlets still to fill in (window not closed)."""
    now = now or _now()
    return [r for r in list_today(db, user, now=now) if r.status in _OPEN_STATUSES and r.closes_at >= now]


def get_run_or_404(db: Session, run_id: str, user) -> OpsChecklistRun:
    r = db.query(OpsChecklistRun).filter(OpsChecklistRun.id == _uuid(run_id, "checklist", 404)).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    assert_can_access(db, r, user)       # 404 for other outlets
    return r


def get_item_or_404(r: OpsChecklistRun, item_id: str) -> OpsChecklistRunItem:
    item = next((i for i in r.items if str(i.id) == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    return item


# ---------------------------------------------------------------------------
# Runs: fill in
# ---------------------------------------------------------------------------

def _assert_open(db: Session, r: OpsChecklistRun, now: datetime) -> None:
    if r.status == "submitted":
        raise HTTPException(status_code=409, detail=f"{r.number} is already submitted")
    if r.status == "missed":
        raise HTTPException(status_code=409, detail=f"{r.number} was missed and is locked")
    if now > r.closes_at:
        r.status = "missed"
        write_audit(db, table_name="ops_checklist_runs", record_id=str(r.id), action="missed",
                    new_value={"number": r.number})
        _notify_missed(db, r)
        db.commit()
        raise HTTPException(status_code=409, detail=f"{r.number} was missed and is locked")
    if now < r.opens_at:
        raise HTTPException(status_code=409, detail=f"{r.number} opens at {r.opens_at.isoformat()}")


def answer_item(db: Session, r: OpsChecklistRun, item: OpsChecklistRunItem, req: AnswerItemRequest,
                user_id: str, now: Optional[datetime] = None) -> RunItemResponse:
    now = now or _now()
    _assert_open(db, r, now)
    recorded = req.recordedAt or now
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    problem = R.recorded_at_problem(recorded, now, r.opens_at, r.closes_at)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    measured = item.item_type in ("number", "temperature")
    if req.result == "na":
        item.result, item.value = "na", None
    elif measured:
        if req.result is not None:
            raise HTTPException(status_code=422, detail="Measured items take a value (or result \"na\")")
        if req.value is not None:
            item.value = req.value
            item.result = R.evaluate_item(item.item_type, req.value, item.min_value, item.max_value)
    else:
        if req.value is not None:
            raise HTTPException(status_code=422, detail="Check items take a result, not a value")
        if req.result is not None:
            item.result = req.result
    if req.notes is not None:
        item.notes = req.notes
    if req.correctiveAction is not None:
        item.corrective_action = req.correctiveAction.strip() or None
    item.recorded_at, item.received_at, item.recorded_by = recorded, now, uuid.UUID(str(user_id))
    if r.status == "pending":
        r.status = "in_progress"
    from app.services.temperature_service import sync_checklist_log
    sync_checklist_log(db, r, item)          # Fase 2: same transaction as the answer
    db.commit()
    db.refresh(item)
    return _item_response(r.id, item, {})


def add_photo(db: Session, r: OpsChecklistRun, item: OpsChecklistRunItem, raw: bytes, mime: str, user_id: str,
              actor: str, now: Optional[datetime] = None) -> PhotoResponse:
    now = now or _now()
    _assert_open(db, r, now)
    stored = storage_service.save_image(raw, mime)
    photo = OpsChecklistPhoto(run_item_id=item.id, storage_key=stored.storage_key, thumbnail_key=stored.thumbnail_key,
                              mime_type=stored.mime_type, size_bytes=stored.size_bytes,
                              uploaded_by=uuid.UUID(str(user_id)))
    db.add(photo)
    if r.status == "pending":
        r.status = "in_progress"
    write_audit(db, table_name="ops_checklist_photos", record_id=str(r.id), action="upload",
                new_value={"number": r.number, "item": item.title, "bytes": stored.size_bytes}, performed_by=actor)
    db.commit()
    db.refresh(photo)
    return _photo_response(r.id, item.id, photo)


# ---------------------------------------------------------------------------
# Runs: submit
# ---------------------------------------------------------------------------

class _ItemView:
    """What the pure rules read from a run item."""

    def __init__(self, i: OpsChecklistRunItem):
        self.title, self.item_type, self.result = i.title, i.item_type, i.result
        self.requires_photo, self.photo_count = bool(i.requires_photo), len(i.photos or [])
        self.corrective_action, self.is_critical = i.corrective_action, bool(i.is_critical)


def _open_issue_for(db: Session, r: OpsChecklistRun, item: OpsChecklistRunItem) -> Optional[Issue]:
    """The still-open Issue an earlier run raised for the same dedup key (keputusan #8)."""
    if item.template_item_id is None:
        return None
    issue_ids = [row[0] for row in (
        db.query(OpsChecklistRunItem.issue_id)
        .join(OpsChecklistRun, OpsChecklistRun.id == OpsChecklistRunItem.run_id)
        .filter(OpsChecklistRunItem.template_item_id == item.template_item_id,
                OpsChecklistRun.outlet_id == r.outlet_id,
                OpsChecklistRunItem.issue_id.isnot(None),
                OpsChecklistRunItem.id != item.id)
        .distinct().all())]
    if not issue_ids:
        return None
    return (db.query(Issue).filter(Issue.id.in_(issue_ids), Issue.status.notin_(_ISSUE_FINAL))
            .order_by(Issue.created_at.desc()).first())


def _issue_category(db: Session, item: OpsChecklistRunItem) -> Category:
    """The item's category, or Compliance when none / deactivated since."""
    if item.issue_category_id:
        cat = db.query(Category).filter(Category.id == item.issue_category_id, Category.deleted_at.is_(None),
                                        Category.is_active.is_(True)).first()
        if cat is not None:
            return cat
    return system_category(db, "compliance")


def _raise_or_comment(db: Session, r: OpsChecklistRun, item: OpsChecklistRunItem, actor: str) -> None:
    from app.schemas.issue import CreateIssueRequest
    from app.services.issue_comment_service import add_comment
    from app.services.issue_service import create_issue

    value = _fmt_value(item)
    existing = _open_issue_for(db, r, item)
    if existing is not None:
        item.issue_id = existing.id
        add_comment(db, existing, "\n".join(filter(None, [
            f"Gagal lagi {r.run_date.strftime('%d-%m-%Y')} ({r.number}, {r.template_name})" + (f": {value}" if value else ""),
            f"Catatan: {item.notes}" if item.notes else "",
            f"Tindakan koreksi: {item.corrective_action}" if item.corrective_action else "",
        ])), kind="system", actor=actor, commit=False)
        escalated_before = db.query(OpsChecklistRunItem.id).filter(
            OpsChecklistRunItem.issue_id == existing.id, OpsChecklistRunItem.escalated_issue.is_(True),
            OpsChecklistRunItem.id != item.id).first()
        if escalated_before is None:
            # Once per Issue: a priority a manager lowers afterwards stays lowered.
            item.escalated_issue = True
            old = _v(existing.priority)
            new = R.repeat_priority(old)
            if new:
                existing.priority = new
                write_audit(db, table_name="issues", record_id=str(existing.id), action="update",
                            old_value={"priority": old}, new_value={"priority": new, "reason": f"checklist {r.number}"},
                            performed_by=actor)
        return

    category = _issue_category(db, item)
    priority = R.fail_issue_priority(bool(item.is_critical), is_repeat=False)
    description = "\n".join(filter(None, [
        f"Temuan checklist {r.number} ({r.template_name}) tanggal {r.run_date.strftime('%d-%m-%Y')} di {r.outlet}.",
        f"Nilai: {value}" if value else "",
        f"Catatan: {item.notes}" if item.notes else "",
        f"Tindakan koreksi: {item.corrective_action}" if item.corrective_action else "",
        "Item kritis." if item.is_critical else "",
    ]))
    issue = create_issue(db, CreateIssueRequest(
        title=f"[Checklist] {item.title} — {r.outlet}"[:500], description=description, outlet=r.outlet,
        categoryId=str(category.id), priority=priority, assignee="Unassigned",
        dueDate=(date.today() + timedelta(days=_DUE_DAYS[priority])).isoformat(), generateTask=True,
    ), commit=False)
    item.issue_id = uuid.UUID(issue.id)


def _escalate_temperature(db: Session, r: OpsChecklistRun, item: OpsChecklistRunItem, actor: str) -> None:
    """A failed asset-bound temperature item (Fase 2): Food Safety issue deduped
    per asset, maybe a Maintenance WO — see temperature_service.escalate."""
    from app.models.temperature_log import TemperatureLog
    from app.services.temperature_service import escalate, sync_checklist_log
    asset = db.get(Asset, item.asset_id)
    log = db.query(TemperatureLog).filter(TemperatureLog.run_item_id == item.id).first() or sync_checklist_log(db, r, item)
    if asset is None or log is None:
        _raise_or_comment(db, r, item, actor)
        return
    issue = escalate(db, asset, log, actor=actor, is_critical=bool(item.is_critical),
                     context=f"checklist {r.number}, {r.run_date.strftime('%d-%m-%Y')}")
    item.issue_id = issue.id


def submit_run(db: Session, r: OpsChecklistRun, user_id: str, user_name: str, actor: str,
               now: Optional[datetime] = None) -> RunDetailResponse:
    now = now or _now()
    if r.status == "submitted":
        raise HTTPException(status_code=409, detail=f"{r.number} is already submitted")
    _assert_open(db, r, now)           # missed / not yet open → 409

    problems = R.submit_problems([_ItemView(i) for i in r.items])
    if problems:
        raise HTTPException(status_code=409, detail={"message": f"{r.number} is not complete.", "problems": problems})

    r.is_late = R.classify_submission(now, r.opens_at, r.due_at, r.closes_at) == "late"
    failed = [i for i in r.items if i.result == "fail"]
    for item in failed:
        if item.asset_id is not None and item.item_type == "temperature":
            _escalate_temperature(db, r, item, actor)
        else:
            _raise_or_comment(db, r, item, actor)

    score = R.run_score([_ItemView(i) for i in r.items])
    r.score = score
    r.status = "submitted"
    r.submitted_at, r.submitted_by, r.submitted_by_name = now, uuid.UUID(str(user_id)), user_name
    write_audit(db, table_name="ops_checklist_runs", record_id=str(r.id), action="submit",
                new_value={"number": r.number, "score": score, "failed": len(failed), "late": r.is_late},
                performed_by=actor)
    db.commit()
    db.refresh(r)
    return run_to_detail(db, r)


def run_now(db: Session) -> dict:
    """Generator + missed for the current company — for demos and the pilot."""
    created = generate_due_runs(db)
    missed = mark_missed_runs(db)
    return {"created": len(created), "missed": len(missed)}


def photo_or_404(r: OpsChecklistRun, item: OpsChecklistRunItem, photo_id: str) -> OpsChecklistPhoto:
    p = next((p for p in item.photos if str(p.id) == photo_id), None)
    if p is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return p


# ---------------------------------------------------------------------------
# KPIs (Fase 3)
# ---------------------------------------------------------------------------

def kpis(db: Session, user, date_from: date, date_to: date, now: Optional[datetime] = None, weeks: int = 8) -> dict:
    """Compliance per outlet over [date_from, date_to] (shift dates), worst first,
    plus totals and a weekly trend of the `weeks` weeks ending at date_to."""
    from app.models.temperature_log import TemperatureLog
    from app.services import ops_kpi_rules as K
    from app.services.outlet_scope_service import scoped_query as scoped

    now = now or _now()
    trend_from = K.week_start(date_to) - timedelta(weeks=weeks - 1)
    runs = runs_query(db, user, date_from=min(date_from, trend_from), date_to=date_to).all()

    def tally(rows) -> dict:
        due = [r for r in rows if K.counts_toward_compliance(r.status, r.closes_at < now)]
        on_time = sum(1 for r in due if r.status == "submitted" and not r.is_late)
        late = sum(1 for r in due if r.status == "submitted" and r.is_late)
        scores = [float(r.score) for r in due if r.status == "submitted" and r.score is not None]
        return {"scheduled": len(due), "onTime": on_time, "late": late, "missed": len(due) - on_time - late,
                "compliance": K.compliance_rate(on_time, len(due)),
                "avgScore": round(sum(scores) / len(scores), 1) if scores else None}

    in_range = [r for r in runs if date_from <= r.run_date <= date_to]
    excursions = {}
    start = datetime.combine(date_from - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date_to + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    for oid, n in (scoped(db, TemperatureLog, user)
                   .filter(TemperatureLog.in_range.is_(False), TemperatureLog.recorded_at >= start,
                           TemperatureLog.recorded_at < end)
                   .with_entities(TemperatureLog.outlet_id, func.count()).group_by(TemperatureLog.outlet_id)):
        excursions[oid] = n

    outlets = []
    by_outlet: Dict = {}
    for r in in_range:
        by_outlet.setdefault((r.outlet_id, r.outlet), []).append(r)
    for (oid, name), rows in by_outlet.items():
        failed = [i.title for r in rows for i in r.items if i.result == "fail"]
        issues = {i.issue_id for r in rows for i in r.items if i.issue_id}
        outlets.append({"outlet": name, "outletId": str(oid), **tally(rows),
                        "topFailing": K.top_failing_items(failed, 3), "excursions": excursions.get(oid, 0),
                        "issuesCreated": len(issues)})
    outlets.sort(key=lambda o: (o["compliance"] is None, o["compliance"] if o["compliance"] is not None else 0, o["outlet"]))

    trend = []
    for w in range(weeks):
        ws = trend_from + timedelta(weeks=w)
        week_rows = [r for r in runs if ws <= r.run_date <= ws + timedelta(days=6)]
        t = tally(week_rows)
        trend.append({"weekStart": ws.isoformat(), "scheduled": t["scheduled"], "onTime": t["onTime"],
                      "compliance": t["compliance"]})

    totals = tally(in_range)
    totals["excursions"] = sum(excursions.values())
    totals["issuesCreated"] = len({i.issue_id for r in in_range for i in r.items if i.issue_id})
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "outlets": outlets, "totals": totals,
            "trend": trend}
