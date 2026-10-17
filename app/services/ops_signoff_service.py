"""Manager's daily sign-off — database side (Todo-Ops-Checklist Fase 4).

  day_data   the runs (by shift date) and temperature readings (by business day,
             05:00–05:00 at the outlet) of one outlet's day, as plain dicts; the
             digest and the PDF export are built from it
  day_view   what the Daily Ops page shows for a day: summary, runs, sign-off,
             whether it can be signed and whether the data still matches
  sign       one sign-off per outlet per business day (409 for a second), only
             once no checklist of the day can still be filled in
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ops_checklist import OpsChecklistRun, OpsDailySignoff
from app.models.outlet import Outlet
from app.models.temperature_log import TemperatureLog
from app.schemas.ops_checklist import DayResponse, SignoffResponse
from app.services import ops_checklist_rules as R
from app.services import ops_signoff_rules as S
from app.services.audit_service import write_audit
from app.services.auth_service import has_permission
from app.services.outlet_scope_service import allowed_outlet_ids, assert_can_write_outlet, scoped_query


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def tz_of(outlet: Outlet) -> str:
    return outlet.timezone or "Asia/Jakarta"


def outlet_or_404(db: Session, name: str, user) -> Outlet:
    o = db.query(Outlet).filter(Outlet.name == name, Outlet.deleted_at.is_(None)).first()
    if o is None:
        raise HTTPException(status_code=404, detail="Outlet not found")
    allowed = allowed_outlet_ids(db, user)
    if allowed is not None and o.id not in allowed:
        raise HTTPException(status_code=404, detail="Outlet not found")     # 404, as assert_can_access
    return o


def day_runs(db: Session, outlet: Outlet, day: date) -> List[OpsChecklistRun]:
    return (db.query(OpsChecklistRun)
            .filter(OpsChecklistRun.outlet_id == outlet.id, OpsChecklistRun.run_date == day)
            .order_by(OpsChecklistRun.due_at).all())


def day_logs(db: Session, outlet: Outlet, day: date) -> List[TemperatureLog]:
    start, end = S.business_day_bounds(day, tz_of(outlet))
    return (db.query(TemperatureLog)
            .filter(TemperatureLog.outlet_id == outlet.id, TemperatureLog.recorded_at >= start,
                    TemperatureLog.recorded_at < end)
            .order_by(TemperatureLog.recorded_at).all())


def run_dict(r: OpsChecklistRun) -> dict:
    return {
        "number": r.number, "status": r.status, "isLate": bool(r.is_late), "score": _f(r.score),
        "submittedAt": _iso(r.submitted_at),
        "items": [{"title": i.title, "result": i.result, "value": _f(i.value), "notes": i.notes or "",
                   "correctiveAction": i.corrective_action} for i in r.items],
    }


def log_dict(log: TemperatureLog) -> dict:
    return {"assetId": str(log.asset_id), "recordedAt": _iso(log.recorded_at), "value": _f(log.value),
            "inRange": bool(log.in_range), "correctiveAction": log.corrective_action, "source": log.source}


def _fingerprint(runs: List[OpsChecklistRun], logs: List[TemperatureLog]):
    rd, ld = [run_dict(r) for r in runs], [log_dict(x) for x in logs]
    return S.day_summary(rd, ld), S.day_digest(rd, ld)


def signoff_to_response(s: OpsDailySignoff) -> SignoffResponse:
    return SignoffResponse(
        id=str(s.id), outlet=s.outlet, outletId=str(s.outlet_id), businessDate=s.business_date.isoformat(),
        signedByName=s.signed_by_name, signedAt=_iso(s.signed_at), note=s.note or "", summary=s.summary or {},
        digest=s.digest,
    )


def _signoff(db: Session, outlet: Outlet, day: date) -> Optional[OpsDailySignoff]:
    return (db.query(OpsDailySignoff)
            .filter(OpsDailySignoff.outlet_id == outlet.id, OpsDailySignoff.business_date == day).first())


def _may_sign(db: Session, outlet: Outlet, user) -> bool:
    if not has_permission(user, "ops", "manage"):
        return False
    try:
        assert_can_write_outlet(db, outlet.id, user)
    except HTTPException:
        return False
    return True


def day_view(db: Session, outlet: Outlet, day: date, user, now: Optional[datetime] = None) -> DayResponse:
    from app.services.ops_checklist_service import run_to_summary
    now = now or _now()
    runs, logs = day_runs(db, outlet, day), day_logs(db, outlet, day)
    summary, digest = _fingerprint(runs, logs)
    signed = _signoff(db, outlet, day)
    problem = None if signed else S.signoff_problem(day, R.local_date(now, tz_of(outlet)), runs, now)
    return DayResponse(
        outlet=outlet.name, outletId=str(outlet.id), date=day.isoformat(), summary=summary,
        runs=[run_to_summary(r) for r in runs],
        signoff=signoff_to_response(signed) if signed else None,
        canSign=signed is None and problem is None and _may_sign(db, outlet, user),
        problem=problem,
        isIntact=(signed.digest == digest) if signed else None,
    )


def sign(db: Session, outlet: Outlet, day: date, note: str, user, now: Optional[datetime] = None) -> SignoffResponse:
    assert_can_write_outlet(db, outlet.id, user)          # 403 for another outlet's manager
    now = now or _now()
    if _signoff(db, outlet, day) is not None:
        raise HTTPException(status_code=409, detail="Hari ini sudah ditandatangani")
    runs, logs = day_runs(db, outlet, day), day_logs(db, outlet, day)
    problem = S.signoff_problem(day, R.local_date(now, tz_of(outlet)), runs, now)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    summary, digest = _fingerprint(runs, logs)
    s = OpsDailySignoff(
        outlet=outlet.name, outlet_id=outlet.id, business_date=day, signed_by=uuid.UUID(str(user.id)),
        signed_by_name=user.name, signed_at=now, note=(note or "").strip(), summary=summary, digest=digest,
    )
    savepoint = db.begin_nested()
    try:
        db.add(s)
        db.flush()
        savepoint.commit()
    except IntegrityError:                                 # a concurrent sign-off won
        savepoint.rollback()
        raise HTTPException(status_code=409, detail="Hari ini sudah ditandatangani")
    write_audit(db, table_name="ops_daily_signoffs", record_id=str(s.id), action="sign",
                new_value={"outlet": outlet.name, "date": day.isoformat(), "digest": digest, **summary},
                performed_by=user.email)
    db.commit()
    db.refresh(s)
    return signoff_to_response(s)


def list_signoffs(db: Session, user, *, outlet: Optional[str] = None, date_from: Optional[date] = None,
                  date_to: Optional[date] = None) -> List[OpsDailySignoff]:
    q = scoped_query(db, OpsDailySignoff, user)
    if outlet:
        q = q.filter(OpsDailySignoff.outlet == outlet)
    if date_from:
        q = q.filter(OpsDailySignoff.business_date >= date_from)
    if date_to:
        q = q.filter(OpsDailySignoff.business_date <= date_to)
    return q.order_by(OpsDailySignoff.business_date.desc(), OpsDailySignoff.outlet).limit(500).all()
