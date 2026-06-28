from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.outlet import Outlet
from app.schemas.outlet import CreateOutletRequest, OutletResponse, UpdateOutletRequest
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/outlets", tags=["master-data"])


def _to_response(outlet: Outlet) -> OutletResponse:
    return OutletResponse(
        id=str(outlet.id),
        name=outlet.name,
        code=outlet.code,
        status=outlet.status.value if hasattr(outlet.status, "value") else str(outlet.status),
    )


def _active(db: Session):
    return db.query(Outlet).filter(Outlet.deleted_at.is_(None))


@router.get("", response_model=List[OutletResponse])
def list_outlets(
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    return [_to_response(o) for o in _active(db).order_by(Outlet.name).all()]


@router.post("", response_model=OutletResponse, status_code=201)
def create_outlet(req: CreateOutletRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    existing = _active(db).filter(Outlet.code == req.code.upper()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Outlet code '{req.code.upper()}' already exists")
    outlet = Outlet(name=req.name, code=req.code.upper(), status=req.status)
    db.add(outlet)
    db.flush()
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="create",
                new_value={"name": outlet.name, "code": outlet.code, "status": str(outlet.status)})
    db.commit()
    db.refresh(outlet)
    return _to_response(outlet)


@router.get("/{outlet_id}", response_model=OutletResponse)
def get_outlet(outlet_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    outlet = _active(db).filter(Outlet.id == outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    return _to_response(outlet)


@router.patch("/{outlet_id}", response_model=OutletResponse)
def update_outlet(outlet_id: str, req: UpdateOutletRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    outlet = _active(db).filter(Outlet.id == outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    old = {"name": outlet.name, "code": outlet.code, "status": str(outlet.status)}
    if req.name is not None:
        outlet.name = req.name
    if req.code is not None:
        outlet.code = req.code.upper()
    if req.status is not None:
        outlet.status = req.status
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="update",
                old_value=old,
                new_value={"name": outlet.name, "code": outlet.code, "status": str(outlet.status)})
    db.commit()
    db.refresh(outlet)
    return _to_response(outlet)


@router.delete("/{outlet_id}", status_code=204)
def delete_outlet(outlet_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    outlet = _active(db).filter(Outlet.id == outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    old = {"name": outlet.name, "code": outlet.code, "status": str(outlet.status)}
    outlet.deleted_at = datetime.now(timezone.utc)
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="delete", old_value=old)
    db.commit()
