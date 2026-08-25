from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.part import Part
from app.models.procurement import GoodsReceipt, PurchaseOrder, PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.procurement import (
    CreatePurchaseRequestRequest,
    GoodsReceiptResponse,
    OrderPurchaseRequestRequest,
    PurchaseOrderItemResponse,
    PurchaseOrderResponse,
    PurchaseRequestItemResponse,
    PurchaseRequestResponse,
    ReceiveGoodsRequest,
    ScanLowStockResponse,
)
from app.services import procurement_service
from app.services.auth_service import UserResponse, get_current_user, require_roles
from app.services.outlet_scope_service import assert_can_access, scoped_query

# ── Response mappers ────────────────────────────────────────────────────────

def _pr_to_response(pr: PurchaseRequest) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(
        id=str(pr.id),
        number=pr.number,
        status=pr.status,
        source=pr.source,
        outlet=pr.outlet,
        requestedBy=pr.requested_by,
        notes=pr.notes,
        totalEst=int(pr.total_est or 0),
        approvalId=str(pr.approval.id) if pr.approval else None,
        items=[
            PurchaseRequestItemResponse(
                id=str(i.id), partId=str(i.part_id) if i.part_id else None,
                partName=i.part_name, quantity=i.quantity,
                estUnitCost=int(i.est_unit_cost or 0), lineTotal=int(i.line_total or 0),
            ) for i in (pr.items or [])
        ],
        createdAt=pr.created_at.isoformat() if pr.created_at else "",
    )


def _po_to_response(po: PurchaseOrder) -> PurchaseOrderResponse:
    return PurchaseOrderResponse(
        id=str(po.id),
        number=po.number,
        status=po.status,
        purchaseRequestId=str(po.purchase_request_id) if po.purchase_request_id else None,
        vendorId=str(po.vendor_id) if po.vendor_id else None,
        vendorName=po.vendor_name,
        outlet=po.outlet,
        total=int(po.total or 0),
        items=[
            PurchaseOrderItemResponse(
                id=str(i.id), partId=str(i.part_id) if i.part_id else None,
                partName=i.part_name, quantityOrdered=i.quantity_ordered,
                quantityReceived=i.quantity_received, unitCost=int(i.unit_cost or 0),
                lineTotal=int(i.line_total or 0),
            ) for i in (po.items or [])
        ],
        createdAt=po.created_at.isoformat() if po.created_at else "",
    )


def _grn_to_response(grn: GoodsReceipt) -> GoodsReceiptResponse:
    return GoodsReceiptResponse(
        id=str(grn.id), number=grn.number,
        purchaseOrderId=str(grn.purchase_order_id),
        receivedBy=grn.received_by,
        createdAt=grn.created_at.isoformat() if grn.created_at else "",
    )


# ── Purchase Requests ───────────────────────────────────────────────────────

pr_router = APIRouter(prefix="/api/purchase-requests", tags=["procurement"])


@pr_router.get("", response_model=List[PurchaseRequestResponse])
def list_prs(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    q = scoped_query(db, PurchaseRequest, current_user)
    if status:
        q = q.filter(PurchaseRequest.status == status)
    return [_pr_to_response(pr) for pr in q.order_by(PurchaseRequest.created_at.desc()).all()]


@pr_router.post("", response_model=PurchaseRequestResponse, status_code=201)
def create_pr(
    req: CreatePurchaseRequestRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    pr = procurement_service.create_purchase_request(
        db,
        items=[{"part_id": i.partId, "part_name": i.partName,
                "quantity": i.quantity, "est_unit_cost": i.estUnitCost} for i in req.items],
        outlet=req.outlet,
        source="manual",
        requested_by=current_user.name,
        notes=req.notes,
    )
    return _pr_to_response(pr)


@pr_router.post("/scan-low-stock", response_model=ScanLowStockResponse)
def scan_low_stock(
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    """Raise auto-reorder PRs for every part at/under its reorder level (idempotent)."""
    parts = db.query(Part).filter(Part.deleted_at.is_(None), Part.is_active.is_(True)).all()
    created = []
    for p in parts:
        pr = procurement_service.maybe_auto_reorder(db, p)
        if pr:
            created.append(pr)
    return ScanLowStockResponse(
        created=len(created),
        purchaseRequestIds=[str(pr.id) for pr in created],
        partsScanned=len(parts),
    )


@pr_router.get("/{pr_id}", response_model=PurchaseRequestResponse)
def get_pr(pr_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    pr = db.query(PurchaseRequest).filter(PurchaseRequest.id == pr_id).first()
    if not pr:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    assert_can_access(db, pr, current_user)
    return _pr_to_response(pr)


@pr_router.post("/{pr_id}/order", response_model=PurchaseOrderResponse, status_code=201)
def order_pr(
    pr_id: str,
    req: OrderPurchaseRequestRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Turn an approved PR into a PO to a vendor."""
    pr = db.query(PurchaseRequest).filter(PurchaseRequest.id == pr_id).first()
    if not pr:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    assert_can_access(db, pr, current_user)
    vendor = db.query(Vendor).filter(Vendor.id == req.vendorId).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    po = procurement_service.create_po_from_pr(db, pr, vendor, created_by=current_user.name)
    return _po_to_response(po)


# ── Purchase Orders ─────────────────────────────────────────────────────────

po_router = APIRouter(prefix="/api/purchase-orders", tags=["procurement"])


@po_router.get("", response_model=List[PurchaseOrderResponse])
def list_pos(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    q = scoped_query(db, PurchaseOrder, current_user)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    return [_po_to_response(po) for po in q.order_by(PurchaseOrder.created_at.desc()).all()]


@po_router.get("/{po_id}", response_model=PurchaseOrderResponse)
def get_po(po_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    assert_can_access(db, po, current_user)
    return _po_to_response(po)


@po_router.post("/{po_id}/receive", response_model=GoodsReceiptResponse, status_code=201)
def receive_po(
    po_id: str,
    req: ReceiveGoodsRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Record a (possibly partial) goods receipt — this is what tops up stock."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    assert_can_access(db, po, current_user)
    grn = procurement_service.receive_goods(
        db, po,
        lines=[{"purchase_order_item_id": l.purchaseOrderItemId,
                "quantity_received": l.quantityReceived} for l in req.lines],
        received_by=current_user.name,
    )
    return _grn_to_response(grn)
