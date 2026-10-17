"""Food safety temperature log — database side (Todo-Ops-Checklist Fase 2).

  sync_checklist_log  answering a temperature item bound to an asset writes its
                      temperature_logs row in the same transaction; answering
                      again updates that row, "n/a" removes it
  record_manual       a reading outside the schedule (scan the freezer's QR);
                      escalates at once
  escalate            for one out-of-range reading, in the caller's transaction:
                        - Food Safety issue for the asset — or, while one is
                          still open, a system comment on it (dedup per asset)
                        - TEMP_CONSECUTIVE_LIMIT excursions in a row, or one
                          major excursion → Maintenance issue + corrective WO
                          on the asset (create_issue, as Tier 1.3) — skipped,
                          with a comment, while a corrective WO is still active
                        - notification `temperature_excursion` to the outlet's managers
Checklist readings escalate when the checklist is submitted (the reading is
final then); manual readings escalate when recorded.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset, WorkOrder
from app.models.issue import Issue
from app.models.ops_checklist import OpsChecklistRun, OpsChecklistRunItem
from app.models.temperature_log import TemperatureLog
from app.services import temperature_rules as T
from app.services.audit_service import write_audit
from app.services.category_service import system_category
from app.services.notification_service import notify_roles
from app.services.notification_texts import L

_ISSUE_FINAL = ("resolved", "closed", "cancelled")
_WO_ACTIVE = ("scheduled", "in-progress", "on-hold")
_DUE_DAYS = {"critical": 1, "high": 3, "medium": 7}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _fmt(value, unit: str = "°C") -> str:
    return f"{float(value):g} {unit}".strip()


def _limits_text(log: TemperatureLog) -> str:
    lo, hi = _f(log.min_value), _f(log.max_value)
    if lo is not None and hi is not None:
        return f"{lo:g} s/d {hi:g} °C"
    if lo is not None:
        return f"≥ {lo:g} °C"
    if hi is not None:
        return f"≤ {hi:g} °C"
    return "-"


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def sync_checklist_log(db: Session, run: OpsChecklistRun, item: OpsChecklistRunItem) -> Optional[TemperatureLog]:
    """Keep the item's log row in step with its answer (same transaction)."""
    if item.item_type != "temperature" or item.asset_id is None:
        return None
    log = db.query(TemperatureLog).filter(TemperatureLog.run_item_id == item.id).first()
    if item.value is None or item.result == "na":
        if log is not None:
            db.delete(log)
        return None
    if log is None:
        log = TemperatureLog(asset_id=item.asset_id, outlet=run.outlet, outlet_id=run.outlet_id,
                             source="checklist", run_item_id=item.id)
        db.add(log)
    log.value, log.min_value, log.max_value = item.value, item.min_value, item.max_value
    log.in_range = T.in_range(item.value, item.min_value, item.max_value)
    log.corrective_action = item.corrective_action
    log.recorded_at = item.recorded_at or _now()
    log.received_at = item.received_at or _now()
    log.recorded_by = item.recorded_by
    db.flush()
    return log


def get_monitored_asset_or_404(db: Session, asset_id: str, user) -> Asset:
    from app.services.outlet_scope_service import assert_can_access
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = db.query(Asset).filter(Asset.id == aid).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, user)
    return asset


def record_manual(db: Session, asset: Asset, value: float, corrective_action: Optional[str],
                  recorded_at: Optional[datetime], user_id: str, actor: str) -> TemperatureLog:
    from app.services.ops_checklist_rules import CLOCK_SKEW
    if not asset.temp_monitored:
        raise HTTPException(status_code=422, detail=f"{asset.name} is not temperature-monitored")
    now = _now()
    recorded = recorded_at or now
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    if recorded > now + CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="recordedAt is in the future")
    if recorded < now - timedelta(hours=24):
        raise HTTPException(status_code=422, detail="recordedAt is more than 24 hours ago")
    ok = T.in_range(value, asset.temp_min, asset.temp_max)
    action = (corrective_action or "").strip() or None
    if not ok and action is None:
        raise HTTPException(status_code=409, detail="A corrective action is required for a reading outside the limits")
    log = TemperatureLog(
        asset_id=asset.id, outlet=asset.outlet, outlet_id=asset.outlet_id, value=value,
        min_value=asset.temp_min, max_value=asset.temp_max, in_range=ok, corrective_action=action,
        source="manual", recorded_at=recorded, received_at=now, recorded_by=uuid.UUID(str(user_id)),
    )
    db.add(log)
    db.flush()
    write_audit(db, table_name="temperature_logs", record_id=str(asset.id), action="record",
                new_value={"asset": asset.number, "value": value, "inRange": ok, "source": "manual"}, performed_by=actor)
    if not ok:
        escalate(db, asset, log, actor=actor, is_critical=False,
                 context=f"pencatatan manual {recorded.strftime('%d-%m-%Y %H:%M')} UTC")
    db.commit()
    db.refresh(log)
    return log


def list_logs(db: Session, asset: Asset, days: int) -> List[TemperatureLog]:
    since = _now() - timedelta(days=days)
    return (db.query(TemperatureLog)
            .filter(TemperatureLog.asset_id == asset.id, TemperatureLog.recorded_at >= since)
            .order_by(TemperatureLog.recorded_at).all())


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

def open_food_safety_issue(db: Session, asset_id) -> Optional[Issue]:
    """The still-open Food Safety issue an earlier excursion on this asset raised."""
    issue_ids = [r[0] for r in db.query(TemperatureLog.issue_id)
                 .filter(TemperatureLog.asset_id == asset_id, TemperatureLog.issue_id.isnot(None)).distinct()]
    if not issue_ids:
        return None
    return (db.query(Issue).filter(Issue.id.in_(issue_ids), Issue.status.notin_(_ISSUE_FINAL))
            .order_by(Issue.created_at.desc()).first())


def _active_corrective_wo(db: Session, asset_id) -> Optional[WorkOrder]:
    return (db.query(WorkOrder)
            .filter(WorkOrder.asset_id == asset_id, WorkOrder.type == "corrective", WorkOrder.status.in_(_WO_ACTIVE))
            .order_by(WorkOrder.created_at.desc()).first())


def _needs_maintenance(db: Session, asset: Asset, log: TemperatureLog) -> bool:
    severity = T.excursion_severity(log.value, log.min_value, log.max_value, settings.TEMP_MAJOR_DELTA)
    if severity == "major":
        return True
    limit = settings.TEMP_CONSECUTIVE_LIMIT
    recent = (db.query(TemperatureLog.in_range).filter(TemperatureLog.asset_id == asset.id)
              .order_by(TemperatureLog.recorded_at.desc(), TemperatureLog.created_at.desc()).limit(limit).all())
    return T.consecutive_excursions([bool(r[0]) for r in recent], limit)


def escalate(db: Session, asset: Asset, log: TemperatureLog, *, actor: str, context: str,
             is_critical: bool = True) -> Issue:
    """Escalate one out-of-range reading (see module doc). Returns the Food Safety
    issue it raised or was added to. Does not commit."""
    from app.schemas.issue import CreateIssueRequest
    from app.services.issue_comment_service import add_comment
    from app.services.issue_service import create_issue

    reading = f"{asset.name}: {_fmt(log.value)} (batas {_limits_text(log)})"
    action = f"Tindakan koreksi: {log.corrective_action}" if log.corrective_action else ""

    # 1. Food Safety — one open issue per asset.
    issue = open_food_safety_issue(db, asset.id)
    if issue is not None:
        add_comment(db, issue, "\n".join(filter(None, [f"Suhu di luar batas lagi ({context}). {reading}", action])),
                    kind="system", actor=actor, commit=False)
    else:
        priority = "critical" if is_critical else "high"
        created = create_issue(db, CreateIssueRequest(
            title=f"[Suhu] {asset.name} di luar batas — {asset.outlet}"[:500],
            description="\n".join(filter(None, [f"Suhu di luar batas ({context}).", reading, action])),
            outlet=asset.outlet, categoryId=str(system_category(db, "food_safety").id), priority=priority,
            assignee="Unassigned", dueDate=(date.today() + timedelta(days=_DUE_DAYS[priority])).isoformat(),
            generateTask=True,
        ), commit=False)
        issue = db.get(Issue, uuid.UUID(created.id))
    log.issue_id = issue.id

    # 2. The equipment itself — Maintenance issue + corrective WO.
    if _needs_maintenance(db, asset, log):
        wo = _active_corrective_wo(db, asset.id)
        if wo is not None:
            if wo.issue_id:
                wo_issue = db.get(Issue, wo.issue_id)
                if wo_issue is not None:
                    add_comment(db, wo_issue, f"Suhu masih di luar batas ({context}). {reading}",
                                kind="system", actor=actor, commit=False)
        else:
            create_issue(db, CreateIssueRequest(
                title=f"[Suhu] Perbaikan {asset.name} — {asset.outlet}"[:500],
                description="\n".join([
                    f"Suhu {asset.name} berulang kali / jauh di luar batas ({context}).", reading,
                    f"Laporan Food Safety: {issue.number}.",
                ]),
                outlet=asset.outlet, categoryId=str(system_category(db, "maintenance").id), priority="high",
                assignee="Unassigned", dueDate=(date.today() + timedelta(days=1)).isoformat(),
                generateTask=False, generateWorkOrder=True, assetId=str(asset.id),
            ), commit=False)

    # 3. Tell the outlet's managers (in-app; WhatsApp when they opted in).
    notify_roles(
        db, ["manager"],
        title=L(f"Suhu di luar batas: {asset.name}", f"Temperature out of range: {asset.name}"),
        message=L(f"{asset.outlet} — {_fmt(log.value)} (batas {_limits_text(log)}). Laporan {issue.number}.",
                  f"{asset.outlet} — {_fmt(log.value)} (limits {_limits_text(log)}). Issue {issue.number}."),
        ntype="critical", entity_type="issues", entity_id=issue.id, outlet_id=asset.outlet_id,
        event="temperature_excursion",
    )
    write_audit(db, table_name="temperature_logs", record_id=str(asset.id), action="excursion",
                new_value={"asset": asset.number, "value": float(log.value), "issue": issue.number}, performed_by=actor)
    return issue
