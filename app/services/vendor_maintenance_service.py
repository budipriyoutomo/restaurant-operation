"""Vendor / external maintenance (Tier 3).

Pure:
  on_time_pct — share of completed vendor WOs met their SLA

DB:
  assign_vendor        — attach a vendor + SLA to a work order
  vendor_performance   — aggregate a vendor's WO stats
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.asset import WorkOrder
from app.models.vendor import Vendor
from app.services.audit_service import write_audit


def on_time_pct(on_time: int, completed: int) -> float:
    if completed <= 0:
        return 0.0
    return round(on_time / completed * 100, 2)


def assign_vendor(db: Session, wo: WorkOrder, vendor: Vendor, sla_due: Optional[date]) -> WorkOrder:
    wo.vendor_id = vendor.id
    wo.vendor_name = vendor.name
    wo.sla_due = sla_due
    write_audit(
        db,
        table_name="work_orders",
        record_id=str(wo.id),
        action="assign_vendor",
        new_value={"number": wo.number, "vendor": vendor.name, "slaDue": sla_due.isoformat() if sla_due else None},
    )
    db.commit()
    db.refresh(wo)
    return wo


def vendor_performance(db: Session, vendor_id: str) -> dict:
    wos = db.query(WorkOrder).filter(WorkOrder.vendor_id == vendor_id).all()

    def status(w: WorkOrder) -> str:
        return w.status.value if hasattr(w.status, "value") else str(w.status)

    completed = [w for w in wos if status(w) == "completed"]
    on_time = [w for w in completed if w.sla_met is True]

    resolution_days = []
    for w in completed:
        if w.created_at and w.completed_date:
            delta = (w.completed_date - w.created_at.date()).days
            resolution_days.append(max(0, delta))

    return {
        "vendorId": str(vendor_id),
        "totalAssigned": len(wos),
        "completed": len(completed),
        "onTime": len(on_time),
        "onTimePct": on_time_pct(len(on_time), len(completed)),
        "avgResolutionDays": round(sum(resolution_days) / len(resolution_days), 2) if resolution_days else 0.0,
        "openWorkOrders": len([w for w in wos if status(w) not in ("completed", "cancelled")]),
    }
