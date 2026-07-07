from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.part import Part
from app.schemas.part import CreatePartRequest, PartResponse, UpdatePartRequest
from app.services import parts_service as parts_svc
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/parts", tags=["cmms"])


@router.get("", response_model=List[PartResponse])
def list_parts(
    outlet: Optional[str] = Query(None),
    low_stock: bool = Query(False),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(Part).filter(Part.deleted_at.is_(None))
    if active_only:
        q = q.filter(Part.is_active.is_(True))
    if outlet:
        q = q.filter(Part.outlet == outlet)
    parts = q.order_by(Part.name).all()
    resp = [parts_svc.part_to_response(p) for p in parts]
    if low_stock:
        resp = [r for r in resp if r.lowStock]
    return resp


@router.post("", response_model=PartResponse, status_code=201)
def create_part(
    req: CreatePartRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    if db.query(Part).filter(Part.sku == req.sku, Part.deleted_at.is_(None)).first():
        raise HTTPException(status_code=409, detail=f"SKU '{req.sku}' already exists")
    part = Part(
        sku=req.sku, name=req.name, category=req.category, unit=req.unit,
        unit_cost=req.unitCost, stock_qty=req.stockQty, reorder_level=req.reorderLevel,
        outlet=req.outlet, is_active=req.isActive,
    )
    db.add(part)
    db.flush()
    write_audit(db, table_name="parts", record_id=str(part.id), action="create",
                new_value={"sku": part.sku, "name": part.name, "stock": part.stock_qty})
    db.commit()
    db.refresh(part)
    return parts_svc.part_to_response(part)


@router.get("/{part_id}", response_model=PartResponse)
def get_part(part_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    part = db.query(Part).filter(Part.id == part_id, Part.deleted_at.is_(None)).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return parts_svc.part_to_response(part)


@router.patch("/{part_id}", response_model=PartResponse)
def update_part(
    part_id: str,
    req: UpdatePartRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    part = db.query(Part).filter(Part.id == part_id, Part.deleted_at.is_(None)).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if req.name is not None:          part.name = req.name
    if req.category is not None:      part.category = req.category
    if req.unit is not None:          part.unit = req.unit
    if req.unitCost is not None:      part.unit_cost = req.unitCost
    if req.stockQty is not None:      part.stock_qty = req.stockQty
    if req.reorderLevel is not None:  part.reorder_level = req.reorderLevel
    if req.outlet is not None:        part.outlet = req.outlet
    if req.isActive is not None:      part.is_active = req.isActive
    part.updated_at = datetime.now(timezone.utc)
    write_audit(db, table_name="parts", record_id=str(part.id), action="update",
                new_value={"sku": part.sku, "stock": part.stock_qty})
    db.commit()
    db.refresh(part)
    return parts_svc.part_to_response(part)


@router.delete("/{part_id}", status_code=204)
def delete_part(
    part_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    part = db.query(Part).filter(Part.id == part_id, Part.deleted_at.is_(None)).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    part.deleted_at = datetime.now(timezone.utc)
    part.is_active = False
    write_audit(db, table_name="parts", record_id=str(part.id), action="delete",
                old_value={"sku": part.sku})
    db.commit()
