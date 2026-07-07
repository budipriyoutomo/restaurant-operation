"""CMMS analytics (Tier 3) — MTTR, MTBF, uptime, cost, repair-vs-replace.

Pure functions (no DB) — unit-tested:
  compute_mttr, compute_mtbf, compute_uptime_pct, repair_vs_replace

DB function:
  compute_fleet_analytics — per-asset metrics + fleet roll-up
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.asset import Asset, WorkOrder
from app.services.work_order_service import compute_downtime_hours

# Repair cost ≥ this fraction of purchase cost → flag repair-vs-replace.
REPAIR_REPLACE_RATIO: float = 0.5


# ---------------------------------------------------------------------------
# Pure metric functions
# ---------------------------------------------------------------------------

def compute_mttr(downtime_hours: List[float]) -> float:
    """Mean Time To Repair — average downtime across repairs. 0 if none."""
    if not downtime_hours:
        return 0.0
    return sum(downtime_hours) / len(downtime_hours)


def compute_mtbf(period_hours: float, total_downtime_hours: float, failures: int) -> float:
    """Mean Time Between Failures — operating hours per failure.

    = (period - downtime) / failures. Returns 0 when there are no failures.
    Never returns a negative value.
    """
    if failures <= 0:
        return 0.0
    uptime = period_hours - total_downtime_hours
    if uptime < 0:
        uptime = 0.0
    return uptime / failures


def compute_uptime_pct(period_hours: float, total_downtime_hours: float) -> float:
    """Percentage of the observation window the asset was operational (0–100)."""
    if period_hours <= 0:
        return 100.0
    pct = (period_hours - total_downtime_hours) / period_hours * 100
    return max(0.0, min(100.0, pct))


def repair_vs_replace(
    total_repair_cost: int,
    purchase_cost: Optional[int],
    ratio: float = REPAIR_REPLACE_RATIO,
) -> Optional[bool]:
    """True when accumulated repair cost ≥ ratio × purchase cost.

    Returns None when purchase cost is unknown (can't decide).
    """
    if purchase_cost is None or purchase_cost <= 0:
        return None
    return total_repair_cost >= ratio * purchase_cost


# ---------------------------------------------------------------------------
# DB aggregation
# ---------------------------------------------------------------------------

def _as_utc(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _asset_start(asset: Asset, wos: List[WorkOrder], now: datetime) -> datetime:
    """Observation-window start: install date, else earliest WO, else asset creation."""
    if asset.install_date is not None:
        return datetime(asset.install_date.year, asset.install_date.month,
                        asset.install_date.day, tzinfo=timezone.utc)
    wo_starts = [_as_utc(wo.created_at) for wo in wos if wo.created_at is not None]
    if wo_starts:
        return min(wo_starts)
    return _as_utc(asset.created_at) or now


def compute_fleet_analytics(db: Session, outlet: Optional[str] = None,
                            ratio: float = REPAIR_REPLACE_RATIO) -> dict:
    """Return {'perAsset': [...], 'fleet': {...}} for all assets (optionally
    filtered by outlet)."""
    q = db.query(Asset)
    if outlet:
        q = q.filter(Asset.outlet == outlet)
    assets = q.order_by(Asset.name).all()

    now = datetime.now(timezone.utc)
    per_asset = []

    def wo_type(wo: WorkOrder) -> str:
        return wo.type.value if hasattr(wo.type, "value") else str(wo.type)

    for asset in assets:
        wos = list(asset.work_orders or [])
        corrective = [w for w in wos if wo_type(w) == "corrective"]

        downtimes = [
            compute_downtime_hours(_as_utc(w.downtime_start), _as_utc(w.downtime_end))
            for w in corrective
        ]
        downtimes = [d for d in downtimes if d > 0]
        total_downtime = sum(downtimes)
        failures = len(corrective)

        period_hours = (now - _asset_start(asset, wos, now)).total_seconds() / 3600

        total_cost = sum(int(w.labor_cost or 0) + int(w.parts_cost or 0) for w in wos)
        repair_cost = sum(int(w.labor_cost or 0) + int(w.parts_cost or 0) for w in corrective)

        per_asset.append({
            "assetId": str(asset.id),
            "assetName": asset.name,
            "outlet": asset.outlet,
            "workOrders": len(wos),
            "failures": failures,
            "mttrHours": round(compute_mttr(downtimes), 2),
            "mtbfHours": round(compute_mtbf(period_hours, total_downtime, failures), 2),
            "uptimePct": round(compute_uptime_pct(period_hours, total_downtime), 2),
            "totalDowntimeHours": round(total_downtime, 2),
            "totalCost": total_cost,
            "repairCost": repair_cost,
            "purchaseCost": asset.purchase_cost,
            "repairVsReplace": repair_vs_replace(repair_cost, asset.purchase_cost, ratio),
        })

    # Fleet roll-up
    with_failures = [a for a in per_asset if a["failures"] > 0]
    n_uptime = len(per_asset)
    fleet = {
        "assetCount": len(per_asset),
        "avgMttrHours": round(
            sum(a["mttrHours"] for a in with_failures) / len(with_failures), 2
        ) if with_failures else 0.0,
        "avgMtbfHours": round(
            sum(a["mtbfHours"] for a in with_failures) / len(with_failures), 2
        ) if with_failures else 0.0,
        "avgUptimePct": round(
            sum(a["uptimePct"] for a in per_asset) / n_uptime, 2
        ) if n_uptime else 100.0,
        "totalCost": sum(a["totalCost"] for a in per_asset),
        "replaceCandidates": sum(1 for a in per_asset if a["repairVsReplace"] is True),
    }

    return {"perAsset": per_asset, "fleet": fleet}
