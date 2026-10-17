"""Apply a built-in template set (ops_template_presets) to a company — Fase 4, UX-11.

Idempotent: a template whose name already exists (case-insensitive) is skipped,
so applying twice — or after the admin renamed nothing — changes nothing. With
an outlet, each template of the set also gets a schedule there unless one
already exists. Used by onboarding (API) and scripts/seed_ops_templates.py.
"""

from __future__ import annotations

from datetime import time
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ops_checklist import OpsChecklistSchedule, OpsChecklistTemplate, OpsChecklistTemplateItem
from app.models.outlet import Outlet
from app.services import ops_checklist_rules as R
from app.services.audit_service import write_audit
from app.services.ops_template_presets import PRESETS


def catalogue() -> List[dict]:
    return [{
        "key": key, "label": p["label"],
        "templates": [{"name": t["name"], "shift": t["shift"], "frequency": t.get("frequency", "daily"),
                       "dueTime": t["dueTime"], "itemCount": len(t["items"])} for t in p["templates"]],
    } for key, p in PRESETS.items()]


def _item(i: dict, order: int) -> OpsChecklistTemplateItem:
    return OpsChecklistTemplateItem(
        title=i["title"], section=i.get("section", ""), item_type=i["itemType"],
        min_value=i.get("minValue"), max_value=i.get("maxValue"), unit=i.get("unit", ""),
        requires_photo=i.get("requiresPhoto", False), is_critical=i.get("isCritical", False), order_index=order,
    )


def apply_preset(db: Session, key: str, outlet: Optional[Outlet], actor: str) -> Dict:
    preset = PRESETS.get(key)
    if preset is None:
        raise HTTPException(status_code=404, detail="Unknown template set")
    created: List[str] = []
    skipped: List[str] = []
    schedules = 0
    for spec in preset["templates"]:
        t = (db.query(OpsChecklistTemplate)
             .filter(func.lower(OpsChecklistTemplate.name) == spec["name"].lower()).first())
        if t is None:
            t = OpsChecklistTemplate(name=spec["name"], description=spec.get("description", ""), shift=spec["shift"],
                                     items=[_item(i, n) for n, i in enumerate(spec["items"])])
            db.add(t)
            db.flush()
            created.append(t.name)
        else:
            skipped.append(t.name)
        if outlet is None:
            continue
        exists = db.query(OpsChecklistSchedule.id).filter(
            OpsChecklistSchedule.template_id == t.id, OpsChecklistSchedule.outlet_id == outlet.id,
            OpsChecklistSchedule.deleted_at.is_(None)).first()
        if exists:
            continue
        frequency = spec.get("frequency", "daily")
        days, dom = spec.get("daysOfWeek", []), spec.get("dayOfMonth")
        open_before, grace = spec.get("openMinutesBefore", 60), spec.get("graceMinutes", 120)
        assert R.schedule_problem(frequency, days, dom, open_before, grace) is None, spec["name"]
        db.add(OpsChecklistSchedule(
            template_id=t.id, outlet=outlet.name, outlet_id=outlet.id, frequency=frequency, days_of_week=days,
            day_of_month=dom, due_time=time.fromisoformat(spec["dueTime"]),
            open_minutes_before=open_before, grace_minutes=grace,
        ))
        schedules += 1
    db.flush()
    write_audit(db, table_name="ops_checklist_templates", record_id=key, action="apply_preset",
                new_value={"preset": key, "created": created, "outlet": outlet.name if outlet else None,
                           "schedules": schedules}, performed_by=actor)
    db.commit()
    return {"preset": key, "created": created, "skipped": skipped, "schedules": schedules}
