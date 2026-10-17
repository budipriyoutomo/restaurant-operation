"""IoT temperature sensors (Todo-Ops-Checklist Fase 4).

A sensor is a device on one monitored asset. It posts readings with its own
key (X-Sensor-Key); only the key's SHA-256 is stored and the key is shown once,
when a manager adds the sensor. The key identifies the company too: there is
no user behind a sensor request.

Readings land in temperature_logs (source = sensor) with the asset's limits
snapshotted, like any other reading. A device retrying a batch is harmless
(UNIQUE sensor_id, recorded_at). Escalation is not per reading — a sensor
reports every few minutes and an open door is not an incident: it escalates
(temperature_service.escalate) when an excursion has lasted
TEMP_SENSOR_SUSTAIN_MINUTES, and again at most every
TEMP_SENSOR_REESCALATE_MINUTES while it lasts.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenancy import bypass_tenant, set_tenant
from app.models.asset import Asset
from app.models.company import Company
from app.models.outlet import Outlet
from app.models.temperature_log import TemperatureLog, TemperatureSensor
from app.services import temperature_rules as T
from app.services.audit_service import write_audit
from app.services.ops_checklist_rules import CLOCK_SKEW, zone

KEY_PREFIX = "rsk_"
MAX_AGE = timedelta(hours=24)          # a device that was offline may send up to a day of backlog


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ── Management (ops:manage) ──────────────────────────────────────────────────

def create_sensor(db: Session, asset: Asset, name: str, actor: str) -> Tuple[TemperatureSensor, str]:
    if not asset.temp_monitored:
        raise HTTPException(status_code=422, detail=f"{asset.name} is not temperature-monitored")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    s = TemperatureSensor(asset_id=asset.id, name=name.strip(), key_prefix=key[:12], key_hash=_hash(key),
                          created_by=actor)
    db.add(s)
    db.flush()
    write_audit(db, table_name="temperature_sensors", record_id=str(s.id), action="create",
                new_value={"asset": asset.number, "name": s.name, "keyPrefix": s.key_prefix}, performed_by=actor)
    db.commit()
    db.refresh(s)
    return s, key


def list_sensors(db: Session, asset: Asset) -> List[TemperatureSensor]:
    return (db.query(TemperatureSensor).filter(TemperatureSensor.asset_id == asset.id)
            .order_by(TemperatureSensor.is_active.desc(), TemperatureSensor.created_at).all())


def get_sensor_or_404(db: Session, asset: Asset, sensor_id: str) -> TemperatureSensor:
    try:
        sid = uuid.UUID(sensor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sensor not found")
    s = db.query(TemperatureSensor).filter(TemperatureSensor.id == sid, TemperatureSensor.asset_id == asset.id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return s


def revoke(db: Session, s: TemperatureSensor, actor: str) -> None:
    """The key stops working at once; the sensor's readings stay in the log."""
    if s.is_active:
        s.is_active, s.revoked_at = False, _now()
        write_audit(db, table_name="temperature_sensors", record_id=str(s.id), action="revoke",
                    old_value={"name": s.name, "keyPrefix": s.key_prefix}, performed_by=actor)
        db.commit()


# ── Ingest (X-Sensor-Key) ────────────────────────────────────────────────────

def authenticate(db: Session, key: Optional[str]) -> TemperatureSensor:
    """The sensor behind a key; switches the session to its company. 401 otherwise."""
    if not key or not key.startswith(KEY_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid sensor key")
    with bypass_tenant(db):
        s = db.query(TemperatureSensor).filter(TemperatureSensor.key_hash == _hash(key)).first()
        company = db.get(Company, s.company_id) if s is not None else None
    if s is None or not s.is_active or company is None or not company.is_active:
        raise HTTPException(status_code=401, detail="Invalid sensor key")
    set_tenant(db, s.company_id)
    return s


def _reading_problem(at: datetime, now: datetime) -> Optional[str]:
    if at > now + CLOCK_SKEW:
        return "recordedAt is in the future"
    if at < now - MAX_AGE:
        return "recordedAt is more than 24 hours ago"
    return None


def _maybe_escalate(db: Session, sensor: TemperatureSensor, asset: Asset, newest: TemperatureLog) -> bool:
    from app.services.temperature_service import escalate
    if newest.in_range:
        return False
    since = newest.recorded_at - MAX_AGE
    last_ok = (db.query(func.max(TemperatureLog.recorded_at))
               .filter(TemperatureLog.asset_id == asset.id, TemperatureLog.in_range.is_(True),
                       TemperatureLog.recorded_at <= newest.recorded_at, TemperatureLog.recorded_at >= since).scalar())
    q = db.query(TemperatureLog.recorded_at, TemperatureLog.in_range).filter(
        TemperatureLog.asset_id == asset.id, TemperatureLog.recorded_at <= newest.recorded_at,
        TemperatureLog.recorded_at >= (last_ok or since))
    readings = [(at, bool(ok)) for at, ok in q.order_by(TemperatureLog.recorded_at.desc()).all()]
    last_escalated = (db.query(func.max(TemperatureLog.recorded_at))
                      .filter(TemperatureLog.asset_id == asset.id, TemperatureLog.source == "sensor",
                              TemperatureLog.issue_id.isnot(None), TemperatureLog.id != newest.id).scalar())
    sustain = timedelta(minutes=settings.TEMP_SENSOR_SUSTAIN_MINUTES)
    cooldown = timedelta(minutes=settings.TEMP_SENSOR_REESCALATE_MINUTES)
    if not T.sensor_should_escalate(readings, last_escalated, sustain, cooldown):
        return False
    start = min(at for at, ok in readings if not ok)
    outlet = db.get(Outlet, asset.outlet_id) if asset.outlet_id else None
    tz = (outlet.timezone if outlet else None) or "Asia/Jakarta"
    escalate(db, asset, newest, actor=f"sensor:{sensor.name}", is_critical=False,
             context=f"sensor {sensor.name}, di luar batas sejak {start.astimezone(zone(tz)).strftime('%d-%m-%Y %H:%M')}")
    return True


def ingest(db: Session, sensor: TemperatureSensor, readings: List[Tuple[float, datetime]],
           now: Optional[datetime] = None) -> dict:
    now = now or _now()
    asset = db.get(Asset, sensor.asset_id)
    if asset is None or not asset.temp_monitored:
        raise HTTPException(status_code=409, detail="The sensor's asset is no longer temperature-monitored")
    accepted, duplicates, rejected = 0, 0, []
    newest: Optional[TemperatureLog] = None
    seen = set()
    for value, at in sorted(readings, key=lambda r: r[1]):
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        problem = _reading_problem(at, now)
        if problem:
            rejected.append({"recordedAt": at.isoformat(), "reason": problem})
            continue
        exists = at in seen or db.query(TemperatureLog.id).filter(
            TemperatureLog.sensor_id == sensor.id, TemperatureLog.recorded_at == at).first() is not None
        if exists:
            duplicates += 1
            continue
        seen.add(at)
        log = TemperatureLog(
            asset_id=asset.id, outlet=asset.outlet, outlet_id=asset.outlet_id, value=value,
            min_value=asset.temp_min, max_value=asset.temp_max, in_range=T.in_range(value, asset.temp_min, asset.temp_max),
            source="sensor", sensor_id=sensor.id, recorded_at=at, received_at=now,
        )
        savepoint = db.begin_nested()
        try:
            db.add(log)
            db.flush()
            savepoint.commit()
        except IntegrityError:                 # the same retry, concurrently
            savepoint.rollback()
            duplicates += 1
            continue
        accepted += 1
        newest = log
    sensor.last_seen_at = now
    escalated = False
    if newest is not None:
        sensor.last_value = newest.value
        escalated = _maybe_escalate(db, sensor, asset, newest)
    db.commit()
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "escalated": escalated}
