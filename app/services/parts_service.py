"""Spare parts service (Tier 3).

Pure:
  compute_line_cost — quantity * unit_cost

DB:
  consume_part          — decrement stock, record consumption, refresh WO parts_cost
  recompute_wo_parts_cost — sum consumption lines onto work_orders.parts_cost
  part_to_response / wo_part_to_response
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.asset import WorkOrder
from app.models.part import Part, WorkOrderPart
from app.schemas.part import PartResponse, WorkOrderPartResponse
from app.services.audit_service import write_audit


class InsufficientStockError(ValueError):
    """Raised when a consumption requests more units than are in stock."""


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def compute_line_cost(quantity: int, unit_cost: int) -> int:
    return int(quantity) * int(unit_cost)


# ---------------------------------------------------------------------------
# ORM → schema
# ---------------------------------------------------------------------------

def part_to_response(p: Part) -> PartResponse:
    return PartResponse(
        id=str(p.id),
        sku=p.sku,
        name=p.name,
        category=p.category,
        unit=p.unit,
        unitCost=int(p.unit_cost or 0),
        stockQty=int(p.stock_qty or 0),
        reorderLevel=int(p.reorder_level or 0),
        outlet=p.outlet,
        isActive=bool(p.is_active),
        lowStock=int(p.stock_qty or 0) <= int(p.reorder_level or 0),
        createdAt=p.created_at.isoformat() if p.created_at else "",
    )


def wo_part_to_response(wp: WorkOrderPart) -> WorkOrderPartResponse:
    return WorkOrderPartResponse(
        id=str(wp.id),
        workOrderId=str(wp.work_order_id),
        partId=str(wp.part_id) if wp.part_id else None,
        partName=wp.part_name,
        quantity=int(wp.quantity),
        unitCost=int(wp.unit_cost or 0),
        lineCost=int(wp.line_cost or 0),
        createdAt=wp.created_at.isoformat() if wp.created_at else "",
    )


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def recompute_wo_parts_cost(db: Session, wo: WorkOrder) -> int:
    """Set wo.parts_cost = sum of all consumption line costs. Returns the total."""
    total = sum(int(wp.line_cost or 0) for wp in (wo.parts_used or []))
    wo.parts_cost = total
    return total


def consume_part(db: Session, wo: WorkOrder, part: Part, quantity: int) -> WorkOrderPart:
    """Consume `quantity` of `part` on `wo`: decrement stock, snapshot cost,
    recompute the WO's parts_cost. All in one transaction (caller commits)."""
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    if int(part.stock_qty or 0) < quantity:
        raise InsufficientStockError(
            f"Insufficient stock for '{part.name}': have {part.stock_qty}, need {quantity}."
        )

    unit_cost = int(part.unit_cost or 0)
    wp = WorkOrderPart(
        work_order_id=wo.id,
        part_id=part.id,
        part_name=part.name,
        quantity=quantity,
        unit_cost=unit_cost,
        line_cost=compute_line_cost(quantity, unit_cost),
    )
    db.add(wp)
    part.stock_qty = int(part.stock_qty or 0) - quantity
    db.flush()   # ensure wp is in wo.parts_used before recompute

    db.refresh(wo)
    recompute_wo_parts_cost(db, wo)

    write_audit(
        db,
        table_name="work_order_parts",
        record_id=str(wp.id),
        action="consume",
        new_value={"workOrder": wo.number, "part": part.name, "qty": quantity, "lineCost": wp.line_cost},
    )
    db.commit()
    db.refresh(wp)
    return wp
