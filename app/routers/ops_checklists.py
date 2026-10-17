"""Daily ops checklist API (Todo-Ops-Checklist Fase 1 §1.5).

Filling a checklist is everyday staff work → ops:view (keputusan #10).
Templates, schedules and the manual generator run → ops:manage.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ops_checklist import OpsChecklistTemplate
from app.models.outlet import Outlet
from app.schemas.ops_checklist import (
    AnswerItemRequest, CreateScheduleRequest, CreateTemperatureLogRequest, CreateTemplateRequest, PhotoResponse,
    RunDetailResponse, RunItemResponse, RunNowResponse, RunSummaryResponse, ScheduleResponse,
    ApplyPresetRequest, ApplyPresetResponse, CreatedSensorResponse, PresetResponse, CreateSensorRequest, DayResponse, KpiResponse, SensorReadingsRequest,
    SensorReadingsResponse, SensorResponse, SignoffRequest, SignoffResponse, TemperatureLogResponse, TemplateResponse, UpdateScheduleRequest, UpdateTemplateRequest,
)
from app.services import idempotency_service, storage_service
from app.services import ops_checklist_service as svc
from app.services import ops_signoff_service as signoff_svc
from app.services.auth_service import UserResponse, require_permission
from app.services.outlet_scope_service import assert_can_write_outlet, resolve_outlet_id

_VIEW = require_permission("ops", "view")
_MANAGE = require_permission("ops", "manage")

templates_router = APIRouter(prefix="/api/ops-checklist-templates", tags=["ops-checklists"])
temperature_router = APIRouter(prefix="/api/assets", tags=["ops-checklists"])
schedules_router = APIRouter(prefix="/api/ops-checklist-schedules", tags=["ops-checklists"])
router = APIRouter(prefix="/api/ops-checklists", tags=["ops-checklists"])
sensor_router = APIRouter(prefix="/api/temperature-sensors", tags=["ops-checklists"])


# ── Templates ────────────────────────────────────────────────────────────────

@templates_router.get("", response_model=List[TemplateResponse])
def list_templates(include_inactive: bool = Query(False), db: Session = Depends(get_db),
                   _: UserResponse = Depends(_VIEW)):
    q = db.query(OpsChecklistTemplate)
    if not include_inactive:
        q = q.filter(OpsChecklistTemplate.is_active.is_(True))
    return [svc.template_to_response(t, db) for t in q.order_by(OpsChecklistTemplate.name).all()]


@templates_router.post("", response_model=TemplateResponse, status_code=201)
def create_template(req: CreateTemplateRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.create_template(db, req, actor=current_user.email)


@templates_router.get("/presets", response_model=List[PresetResponse])
def list_presets(_: UserResponse = Depends(_VIEW)):
    """Built-in template sets per outlet type (cafe, resto, cloud kitchen) — Fase 4."""
    from app.services import ops_preset_service as presets
    return presets.catalogue()


@templates_router.post("/presets/{key}", response_model=ApplyPresetResponse, status_code=201)
def apply_preset(key: str, req: ApplyPresetRequest, db: Session = Depends(get_db),
                 current_user: UserResponse = Depends(_MANAGE)):
    """Create a set's templates (existing names are skipped) and, with an outlet, schedule them there."""
    from app.services import ops_preset_service as presets
    outlet = None
    if req.outlet:
        outlet_id = resolve_outlet_id(db, req.outlet)
        assert_can_write_outlet(db, outlet_id, current_user)
        outlet = db.query(Outlet).filter(Outlet.id == outlet_id).one()
    return presets.apply_preset(db, key, outlet, actor=current_user.email)


@templates_router.patch("/{template_id}", response_model=TemplateResponse)
def update_template(template_id: str, req: UpdateTemplateRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.update_template(db, svc.get_template_or_404(db, template_id), req, actor=current_user.email)


# ── Schedules ────────────────────────────────────────────────────────────────

@schedules_router.get("", response_model=List[ScheduleResponse])
def list_schedules(outlet: Optional[str] = Query(None), db: Session = Depends(get_db),
                   current_user: UserResponse = Depends(_VIEW)):
    q = svc.schedules_query(db, current_user)
    if outlet:
        q = q.filter(svc.OpsChecklistSchedule.outlet == outlet)
    rows = q.all()
    rows.sort(key=lambda s: (s.outlet, s.due_time))
    return [svc.schedule_to_response(s) for s in rows]


@schedules_router.post("", response_model=ScheduleResponse, status_code=201)
def create_schedule(req: CreateScheduleRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    outlet_id = resolve_outlet_id(db, req.outlet)      # unknown outlet → 422
    if outlet_id is None:
        raise HTTPException(status_code=422, detail="outlet is required")
    assert_can_write_outlet(db, outlet_id, current_user)
    outlet = db.query(Outlet).filter(Outlet.id == outlet_id).one()
    return svc.create_schedule(db, req, outlet, actor=current_user.email)


@schedules_router.patch("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(schedule_id: str, req: UpdateScheduleRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    s = svc.get_schedule_or_404(db, schedule_id, current_user)
    assert_can_write_outlet(db, s.outlet_id, current_user)
    return svc.update_schedule(db, s, req, actor=current_user.email)


@schedules_router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_MANAGE)):
    s = svc.get_schedule_or_404(db, schedule_id, current_user)
    assert_can_write_outlet(db, s.outlet_id, current_user)
    svc.delete_schedule(db, s, actor=current_user.email)


# ── Runs ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[RunSummaryResponse])
def list_runs(
    date_: Optional[date] = Query(None, alias="date", description="Shift date; default today at each outlet"),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    outlet: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
):
    if date_ is None and date_from is None and date_to is None:
        rows = svc.list_today(db, current_user, outlet=outlet, status=status)
    else:
        rows = (svc.runs_query(db, current_user, run_date=date_, date_from=date_from, date_to=date_to,
                               outlet=outlet, status=status)
                .order_by(svc.OpsChecklistRun.run_date.desc(), svc.OpsChecklistRun.due_at).limit(500).all())
    return [svc.run_to_summary(r) for r in rows]


@router.get("/kpis", response_model=KpiResponse, response_model_by_alias=True)
def get_kpis(
    date_from: Optional[date] = Query(None, alias="from", description="Shift date; default 6 days before `to`"),
    date_to: Optional[date] = Query(None, alias="to", description="Shift date; default today"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("ops", "view", ("analytics", "view"))),
):
    """Checklist compliance per outlet (worst first), totals and an 8-week trend (Fase 3)."""
    from datetime import date as _date, timedelta
    to = date_to or _date.today()
    frm = date_from or (to - timedelta(days=6))
    if frm > to:
        raise HTTPException(status_code=422, detail="from must not be after to")
    return svc.kpis(db, current_user, frm, to)


# ── Daily sign-off (Fase 4) ──────────────────────────────────────────────────

@router.get("/signoffs", response_model=List[SignoffResponse])
def list_signoffs(
    outlet: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
):
    rows = signoff_svc.list_signoffs(db, current_user, outlet=outlet, date_from=date_from, date_to=date_to)
    return [signoff_svc.signoff_to_response(s) for s in rows]


@router.get("/export.pdf")
def export_pdf(
    outlet: str = Query(...),
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
):
    """Checklist & temperature log of one outlet for an inspection (≤ 31 days), as PDF."""
    from app.services import ops_report_service as report
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="from must not be after to")
    if (date_to - date_from).days + 1 > report.MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"At most {report.MAX_DAYS} days per export")
    o = signoff_svc.outlet_or_404(db, outlet, current_user)
    pdf = report.render_pdf(report.report_data(db, o, date_from, date_to))
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in o.name)
    filename = f"ops-log-{safe}-{date_from.isoformat()}_{date_to.isoformat()}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/days/{day}", response_model=DayResponse)
def get_day(day: date, outlet: str = Query(...), db: Session = Depends(get_db),
            current_user: UserResponse = Depends(_VIEW)):
    """One outlet's business day: runs, summary, sign-off and whether it can be signed."""
    o = signoff_svc.outlet_or_404(db, outlet, current_user)
    return signoff_svc.day_view(db, o, day, current_user)


@router.post("/days/{day}/signoff", response_model=SignoffResponse, status_code=201)
def sign_day(day: date, req: SignoffRequest, db: Session = Depends(get_db),
             current_user: UserResponse = Depends(_MANAGE)):
    """The manager's "today's logs are checked" — once per outlet per business day."""
    o = signoff_svc.outlet_or_404(db, req.outlet, current_user)
    return signoff_svc.sign(db, o, day, req.note, current_user)


@router.post("/run-now", response_model=RunNowResponse)
def run_now(db: Session = Depends(get_db), _: UserResponse = Depends(_MANAGE)):
    """Run the generator and the missed-run job now (demo / pilot); the cron does this every 15 min."""
    return svc.run_now(db)


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW)):
    return svc.run_to_detail(db, svc.get_run_or_404(db, run_id, current_user))


@router.patch("/{run_id}/items/{item_id}", response_model=RunItemResponse)
def answer_item(run_id: str, item_id: str, req: AnswerItemRequest, request: Request,
                db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW),
                idempotency_key: Optional[str] = Header(None)):
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    r = svc.get_run_or_404(db, run_id, current_user)
    resp = svc.answer_item(db, r, svc.get_item_or_404(r, item_id), req, current_user.id)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 200, resp)
    return resp


@router.post("/{run_id}/items/{item_id}/photos", response_model=PhotoResponse, status_code=201)
def upload_photo(run_id: str, item_id: str, request: Request, file: UploadFile = File(...),
                 db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW),
                 idempotency_key: Optional[str] = Header(None)):
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    r = svc.get_run_or_404(db, run_id, current_user)
    resp = svc.add_photo(db, r, svc.get_item_or_404(r, item_id), file.file.read(), file.content_type or "",
                         current_user.id, actor=current_user.email)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


def _serve(db: Session, run_id: str, item_id: str, photo_id: str, user, thumbnail: bool):
    r = svc.get_run_or_404(db, run_id, user)
    p = svc.photo_or_404(r, svc.get_item_or_404(r, item_id), photo_id)
    key = (p.thumbnail_key or p.storage_key) if thumbnail else p.storage_key
    return StreamingResponse(storage_service.open_stream(key), media_type=p.mime_type)


@router.get("/{run_id}/items/{item_id}/photos/{photo_id}/file")
def serve_photo(run_id: str, item_id: str, photo_id: str, db: Session = Depends(get_db),
                current_user: UserResponse = Depends(_VIEW)):
    return _serve(db, run_id, item_id, photo_id, current_user, thumbnail=False)


@router.get("/{run_id}/items/{item_id}/photos/{photo_id}/thumbnail")
def serve_thumbnail(run_id: str, item_id: str, photo_id: str, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_VIEW)):
    return _serve(db, run_id, item_id, photo_id, current_user, thumbnail=True)


@router.post("/{run_id}/submit", response_model=RunDetailResponse)
def submit_run(run_id: str, request: Request, db: Session = Depends(get_db),
               current_user: UserResponse = Depends(_VIEW), idempotency_key: Optional[str] = Header(None)):
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    r = svc.get_run_or_404(db, run_id, current_user)
    resp = svc.submit_run(db, r, current_user.id, current_user.name, actor=current_user.email)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 200, resp)
    return resp


# ── Temperature logs (Fase 2) ────────────────────────────────────────────────

def _log_response(log) -> TemperatureLogResponse:
    f = lambda v: float(v) if v is not None else None   # noqa: E731
    return TemperatureLogResponse(
        id=str(log.id), assetId=str(log.asset_id), outlet=log.outlet, value=float(log.value),
        minValue=f(log.min_value), maxValue=f(log.max_value), inRange=bool(log.in_range),
        correctiveAction=log.corrective_action, source=log.source,
        runItemId=str(log.run_item_id) if log.run_item_id else None,
        issueId=str(log.issue_id) if log.issue_id else None, recordedAt=log.recorded_at.isoformat(),
    )


@temperature_router.get("/{asset_id}/temperature-logs", response_model=List[TemperatureLogResponse])
def list_temperature_logs(
    asset_id: str, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("ops", "view", ("cmms", "view"), ("assets", "view"))),
):
    """Readings of one asset, oldest first — the 30-day chart in CMMS."""
    from app.services import temperature_service as temp
    asset = temp.get_monitored_asset_or_404(db, asset_id, current_user)
    return [_log_response(log) for log in temp.list_logs(db, asset, days)]


@temperature_router.post("/{asset_id}/temperature-logs", response_model=TemperatureLogResponse, status_code=201)
def create_temperature_log(asset_id: str, req: CreateTemperatureLogRequest, request: Request,
                           db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW),
                           idempotency_key: Optional[str] = Header(None)):
    """A reading outside the schedule (e.g. the freezer's QR). Out of range needs a
    corrective action (409) and escalates at once."""
    from app.services import temperature_service as temp
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    asset = temp.get_monitored_asset_or_404(db, asset_id, current_user)
    log = temp.record_manual(db, asset, req.value, req.correctiveAction, req.recordedAt, current_user.id,
                             actor=current_user.email)
    resp = _log_response(log)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


# ── IoT temperature sensors (Fase 4) ─────────────────────────────────────────

def _sensor_response(s) -> SensorResponse:
    return SensorResponse(
        id=str(s.id), assetId=str(s.asset_id), name=s.name, keyPrefix=s.key_prefix, isActive=bool(s.is_active),
        lastSeenAt=s.last_seen_at.isoformat() if s.last_seen_at else None,
        lastValue=float(s.last_value) if s.last_value is not None else None, createdAt=s.created_at.isoformat(),
    )


@temperature_router.get("/{asset_id}/temperature-sensors", response_model=List[SensorResponse])
def list_sensors(asset_id: str, db: Session = Depends(get_db),
                 current_user: UserResponse = Depends(require_permission("ops", "view", ("cmms", "view"), ("assets", "view")))):
    from app.services import temperature_service as temp
    from app.services import temperature_sensor_service as sensors
    asset = temp.get_monitored_asset_or_404(db, asset_id, current_user)
    return [_sensor_response(s) for s in sensors.list_sensors(db, asset)]


@temperature_router.post("/{asset_id}/temperature-sensors", response_model=CreatedSensorResponse, status_code=201)
def create_sensor(asset_id: str, req: CreateSensorRequest, db: Session = Depends(get_db),
                  current_user: UserResponse = Depends(_MANAGE)):
    """Add a device to a monitored asset. The response carries its key — the only time it is shown."""
    from app.services import temperature_service as temp
    from app.services import temperature_sensor_service as sensors
    asset = temp.get_monitored_asset_or_404(db, asset_id, current_user)
    if asset.outlet_id:
        assert_can_write_outlet(db, asset.outlet_id, current_user)
    s, key = sensors.create_sensor(db, asset, req.name, actor=current_user.email)
    return CreatedSensorResponse(**_sensor_response(s).model_dump(), key=key)


@temperature_router.delete("/{asset_id}/temperature-sensors/{sensor_id}", status_code=204)
def revoke_sensor(asset_id: str, sensor_id: str, db: Session = Depends(get_db),
                  current_user: UserResponse = Depends(_MANAGE)):
    from app.services import temperature_service as temp
    from app.services import temperature_sensor_service as sensors
    asset = temp.get_monitored_asset_or_404(db, asset_id, current_user)
    if asset.outlet_id:
        assert_can_write_outlet(db, asset.outlet_id, current_user)
    sensors.revoke(db, sensors.get_sensor_or_404(db, asset, sensor_id), actor=current_user.email)
    return Response(status_code=204)


@sensor_router.post("/readings", response_model=SensorReadingsResponse)
def push_readings(req: SensorReadingsRequest, db: Session = Depends(get_db),
                  x_sensor_key: Optional[str] = Header(None)):
    """Readings from a device, authenticated by its X-Sensor-Key (no user). Up to
    500 per call (a backlog after being offline, ≤ 24 hours old). Retrying is safe."""
    from app.services import temperature_sensor_service as sensors
    sensor = sensors.authenticate(db, x_sensor_key)
    return sensors.ingest(db, sensor, [(r.value, r.recordedAt) for r in req.readings])
