"""Procurement service (Tier 6.1) — PR → PO → Goods Receipt.

Closes the loop the CMMS opens: a part hitting its reorder level raises a
Purchase Request, approved through the shared approval engine (type
`procurement`), turned into a Purchase Order, and received (topping stock back
up via the same parts service that consumption uses).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.part import Part
from app.models.procurement import (
    GoodsReceipt, GoodsReceiptItem,
    PurchaseOrder, PurchaseOrderItem,
    PurchaseRequest, PurchaseRequestItem,
)
from app.services import parts_service
from app.services.audit_service import write_audit
from app.services.outlet_scope_service import resolve_outlet_id

# PR statuses that still "hold" a part — used to avoid duplicate auto-PRs.
_OPEN_PR_STATUSES = ("pending_approval", "approved", "ordered")


# ---------------------------------------------------------------------------
# Number sequences
# ---------------------------------------------------------------------------

def _next_number(db: Session, prefix: str) -> str:
    year = datetime.now().year
    seq = db.execute(
        text("""
            INSERT INTO procurement_number_sequences (prefix, year, last_seq)
            VALUES (:p, :y, 1)
            ON CONFLICT (prefix, year) DO UPDATE
              SET last_seq = procurement_number_sequences.last_seq + 1
            RETURNING last_seq
        """),
        {"p": prefix, "y": year},
    ).scalar_one()
    return f"{prefix}-{year}-{seq:05d}"


# ---------------------------------------------------------------------------
# Purchase Requests
# ---------------------------------------------------------------------------

def create_purchase_request(
    db: Session,
    *,
    items: List[dict],          # [{part_id?, part_name, quantity, est_unit_cost}]
    outlet: Optional[str],
    source: str = "manual",
    requested_by: str = "",
    notes: Optional[str] = None,
) -> PurchaseRequest:
    """Create a PR + its items and attach a procurement approval (shared engine)."""
    if not items:
        raise HTTPException(status_code=422, detail="A purchase request needs at least one item")

    from app.services.approval_service import create_approval_with_steps

    outlet_id = resolve_outlet_id(db, outlet)
    pr = PurchaseRequest(
        number=_next_number(db, "PR"),
        status="pending_approval",
        outlet=outlet,
        outlet_id=outlet_id,
        source=source,
        requested_by=requested_by,
        notes=notes,
    )
    db.add(pr)
    db.flush()

    total = 0
    for it in items:
        qty = int(it["quantity"])
        unit = int(it.get("est_unit_cost", 0))
        line = qty * unit
        total += line
        db.add(PurchaseRequestItem(
            purchase_request_id=pr.id,
            part_id=it.get("part_id"),
            part_name=it["part_name"],
            quantity=qty,
            est_unit_cost=unit,
            line_total=line,
        ))
    pr.total_est = total

    # Approval through the existing engine — procurement policy (Tier 2.2) or the
    # default 2-step chain. No second approval machine.
    create_approval_with_steps(
        db,
        purchase_request_id=pr.id,
        title=f"Purchase Request {pr.number}",
        approval_type="procurement",
        description=notes or "",
        requester=requested_by,
        outlet=outlet or "",
        outlet_id=outlet_id,
        amount=total,
        flush_only=True,
    )

    write_audit(db, table_name="purchase_requests", record_id=str(pr.id), action="create",
                new_value={"number": pr.number, "source": source, "total": total})
    db.commit()
    db.refresh(pr)
    return pr


def maybe_auto_reorder(db: Session, part: Part) -> Optional[PurchaseRequest]:
    """Raise an auto-reorder PR for a part that has hit its reorder level.

    Idempotent: skips if reorder isn't configured, stock is fine, or an open
    auto-PR already exists for the part (so a second consumption doesn't spam a
    duplicate). Order quantity tops the part back up to 2× its reorder level.
    """
    reorder = int(part.reorder_level or 0)
    if reorder <= 0 or int(part.stock_qty or 0) > reorder:
        return None

    existing = (
        db.query(PurchaseRequestItem.id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestItem.purchase_request_id)
        .filter(
            PurchaseRequestItem.part_id == part.id,
            PurchaseRequest.source == "auto_reorder",
            PurchaseRequest.status.in_(_OPEN_PR_STATUSES),
        )
        .first()
    )
    if existing:
        return None

    order_qty = max(1, reorder * 2 - int(part.stock_qty or 0))
    return create_purchase_request(
        db,
        items=[{
            "part_id": part.id, "part_name": part.name,
            "quantity": order_qty, "est_unit_cost": int(part.unit_cost or 0),
        }],
        outlet=part.outlet,
        source="auto_reorder",
        requested_by="system",
        notes=f"Auto-reorder: stok {part.stock_qty} ≤ batas {reorder}",
    )


# ---------------------------------------------------------------------------
# Purchase Orders
# ---------------------------------------------------------------------------

def create_po_from_pr(db: Session, pr: PurchaseRequest, vendor, created_by: str = "") -> PurchaseOrder:
    """Turn an APPROVED PR into a PO to a vendor."""
    if pr.status != "approved":
        raise HTTPException(status_code=409, detail=f"PR must be approved to order (is '{pr.status}')")

    po = PurchaseOrder(
        number=_next_number(db, "PO"),
        purchase_request_id=pr.id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        status="sent",
        outlet=pr.outlet,
        outlet_id=pr.outlet_id,
        created_by=created_by,
    )
    db.add(po)
    db.flush()

    total = 0
    for it in pr.items:
        line = it.quantity * it.est_unit_cost
        total += line
        db.add(PurchaseOrderItem(
            purchase_order_id=po.id,
            part_id=it.part_id,
            part_name=it.part_name,
            quantity_ordered=it.quantity,
            unit_cost=it.est_unit_cost,
            line_total=line,
        ))
    po.total = total
    pr.status = "ordered"

    write_audit(db, table_name="purchase_orders", record_id=str(po.id), action="create",
                new_value={"number": po.number, "vendor": vendor.name, "fromPR": pr.number})
    db.commit()
    db.refresh(po)
    return po


# ---------------------------------------------------------------------------
# Goods Receipt — the step that actually restocks
# ---------------------------------------------------------------------------

def part_price_history(db: Session, part_id) -> List[dict]:
    """Per-vendor purchasing history for a part (Tier 6.2) — so a buyer can pick
    the vendor with the best track record and price. Derived from PO items."""
    rows = (
        db.query(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .filter(PurchaseOrderItem.part_id == part_id)
        # Order by number, not created_at: within one request/transaction many POs
        # share the same NOW(), so the timestamp can't decide "most recent". PO
        # numbers are monotonic and zero-padded, so they sort reliably.
        .order_by(PurchaseOrder.number.desc())
        .all()
    )
    by_vendor: dict = {}
    for item, po in rows:
        key = str(po.vendor_id) if po.vendor_id else "__none__"
        agg = by_vendor.setdefault(key, {
            "vendorId": str(po.vendor_id) if po.vendor_id else None,
            "vendorName": po.vendor_name,
            "costs": [], "qty": 0, "count": 0, "last_at": None, "last_cost": None,
        })
        agg["costs"].append(int(item.unit_cost or 0))
        agg["qty"] += int(item.quantity_ordered or 0)
        agg["count"] += 1
        if agg["last_at"] is None and po.created_at:          # rows are newest-first
            agg["last_at"] = po.created_at
            agg["last_cost"] = int(item.unit_cost or 0)

    out = []
    for agg in by_vendor.values():
        costs = agg["costs"] or [0]
        out.append({
            "vendorId": agg["vendorId"],
            "vendorName": agg["vendorName"],
            "lastUnitCost": agg["last_cost"] or 0,
            "avgUnitCost": round(sum(costs) / len(costs)),
            "minUnitCost": min(costs),
            "timesOrdered": agg["count"],
            "totalQuantity": agg["qty"],
            "lastOrderedAt": agg["last_at"].isoformat() if agg["last_at"] else None,
        })
    # Cheapest average first — the most useful default ordering for a buyer.
    out.sort(key=lambda e: e["avgUnitCost"])
    return out


def receive_goods(db: Session, po: PurchaseOrder, lines: List[dict], received_by: str = "") -> GoodsReceipt:
    """Record a (possibly partial) receipt against a PO and top up stock.

    lines: [{purchase_order_item_id, quantity_received}]. Over-receiving beyond
    the outstanding quantity is rejected. Stock is incremented through the same
    parts service used for consumption, so inventory stays consistent.
    """
    if po.status in ("received", "cancelled"):
        raise HTTPException(status_code=409, detail=f"PO is already '{po.status}'")
    if not lines:
        raise HTTPException(status_code=422, detail="Nothing to receive")

    items_by_id = {str(i.id): i for i in po.items}
    grn = GoodsReceipt(
        number=_next_number(db, "GRN"),
        purchase_order_id=po.id,
        received_by=received_by,
    )
    db.add(grn)
    db.flush()

    for ln in lines:
        item = items_by_id.get(str(ln["purchase_order_item_id"]))
        if item is None:
            raise HTTPException(status_code=422, detail="Receipt line does not belong to this PO")
        qty = int(ln["quantity_received"])
        if qty <= 0:
            continue
        outstanding = item.quantity_ordered - item.quantity_received
        if qty > outstanding:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot receive {qty} of '{item.part_name}' — only {outstanding} outstanding",
            )
        item.quantity_received += qty
        db.add(GoodsReceiptItem(
            goods_receipt_id=grn.id,
            purchase_order_item_id=item.id,
            part_id=item.part_id,
            quantity_received=qty,
        ))
        if item.part_id:
            part = db.query(Part).filter(Part.id == item.part_id).first()
            if part:
                parts_service.restock(db, part, qty)

    # PO status: fully vs partially received.
    fully = all(i.quantity_received >= i.quantity_ordered for i in po.items)
    po.status = "received" if fully else "partially_received"
    if fully and po.purchase_request_id:
        pr = db.query(PurchaseRequest).filter(PurchaseRequest.id == po.purchase_request_id).first()
        if pr:
            pr.status = "received"

    write_audit(db, table_name="goods_receipts", record_id=str(grn.id), action="receive",
                new_value={"number": grn.number, "po": po.number, "poStatus": po.status})
    db.commit()
    db.refresh(grn)
    return grn
