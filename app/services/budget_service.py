"""Maintenance budget vs spend (Tier 6.3).

Pure:
  budget_health  — remaining / pct / warning flag from amount + spent

DB:
  compute_budget_status — per-outlet budget vs actual spend for a month
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.asset import WorkOrder
from app.models.budget import Budget
from app.models.outlet import Outlet
from app.models.procurement import PurchaseOrder

# Warn when spend reaches this share of the budget.
WARN_RATIO: float = 0.8


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def budget_health(amount: int, spent: int, warn_ratio: float = WARN_RATIO) -> dict:
    remaining = amount - spent
    pct = round(spent / amount * 100, 1) if amount > 0 else (100.0 if spent > 0 else 0.0)
    return {
        "remaining": remaining,
        "pct": pct,
        "overBudget": spent > amount and amount > 0,
        "warning": amount > 0 and spent >= warn_ratio * amount,
    }


def _period_bounds(period: str) -> tuple[date, date]:
    """'YYYY-MM' → (first day, first day of next month)."""
    year, month = int(period[:4]), int(period[5:7])
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    # exclusive upper bound = first day of next month
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    _ = last_day
    return start, end


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _wo_spend(db: Session, outlet_id, start: date, end: date) -> int:
    """Completed work-order cost (labor+parts) realised in the period."""
    total = (
        db.query(func.coalesce(func.sum(WorkOrder.labor_cost + WorkOrder.parts_cost), 0))
        .filter(
            WorkOrder.outlet_id == outlet_id,
            WorkOrder.status == "completed",
            WorkOrder.completed_date >= start,
            WorkOrder.completed_date < end,
        )
        .scalar()
    )
    return int(total or 0)


def _po_spend(db: Session, outlet_id, start: date, end: date) -> int:
    """Purchase-order value committed in the period (excludes cancelled)."""
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    total = (
        db.query(func.coalesce(func.sum(PurchaseOrder.total), 0))
        .filter(
            PurchaseOrder.outlet_id == outlet_id,
            PurchaseOrder.status != "cancelled",
            PurchaseOrder.created_at >= start_dt,
            PurchaseOrder.created_at < end_dt,
        )
        .scalar()
    )
    return int(total or 0)


def compute_budget_status(db: Session, period: str, user=None) -> list[dict]:
    """Per-outlet budget vs spend for a month. Only outlets that have a budget
    for the period are returned. Outlet-scoped when `user` is a non-admin."""
    from app.services.outlet_scope_service import is_admin, user_outlet_ids

    start, end = _period_bounds(period)
    q = db.query(Budget).filter(Budget.period == period)
    if user is not None and not is_admin(user):
        allowed = user_outlet_ids(db, user)
        if not allowed:
            return []
        q = q.filter(Budget.outlet_id.in_(allowed))

    out = []
    for b in q.all():
        wo = _wo_spend(db, b.outlet_id, start, end)
        po = _po_spend(db, b.outlet_id, start, end)
        spent = wo + po
        outlet = db.query(Outlet).filter(Outlet.id == b.outlet_id).first()
        out.append({
            "budgetId": str(b.id),
            "outletId": str(b.outlet_id),
            "outlet": (outlet.name if outlet else b.outlet),
            "period": period,
            "amount": int(b.amount or 0),
            "spentWorkOrders": wo,
            "spentPurchaseOrders": po,
            "spent": spent,
            **budget_health(int(b.amount or 0), spent),
        })
    out.sort(key=lambda x: -x["pct"])
    return out
