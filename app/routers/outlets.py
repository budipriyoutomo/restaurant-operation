from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.outlet import Outlet
from app.schemas.outlet import CreateOutletRequest, OutletResponse, UpdateOutletRequest
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_permission
from app.services.work_order_service import APPROVAL_THRESHOLD

router = APIRouter(prefix="/api/outlets", tags=["master-data"])


def _to_response(outlet: Outlet) -> OutletResponse:
    return OutletResponse(
        id=str(outlet.id),
        name=outlet.name,
        code=outlet.code,
        status=outlet.status.value if hasattr(outlet.status, "value") else str(outlet.status),
        approvalThreshold=outlet.approval_threshold,
        approvalThresholdDefault=APPROVAL_THRESHOLD,
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
def create_outlet(req: CreateOutletRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_permission("master-data", "manage"))):
    existing = _active(db).filter(Outlet.code == req.code.upper()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Outlet code '{req.code.upper()}' already exists")
    outlet = Outlet(name=req.name, code=req.code.upper(), status=req.status,
                    approval_threshold=req.approvalThreshold)
    db.add(outlet)
    db.flush()
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="create",
                new_value={"name": outlet.name, "code": outlet.code, "status": str(outlet.status),
                           "approval_threshold": outlet.approval_threshold})
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
def update_outlet(outlet_id: str, req: UpdateOutletRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_permission("master-data", "manage"))):
    outlet = _active(db).filter(Outlet.id == outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    old = {"name": outlet.name, "code": outlet.code, "status": str(outlet.status),
           "approval_threshold": outlet.approval_threshold}
    if req.name is not None:
        outlet.name = req.name
    if req.code is not None:
        outlet.code = req.code.upper()
    if req.status is not None:
        outlet.status = req.status
    if "approvalThreshold" in req.model_fields_set:   # null clears the override
        outlet.approval_threshold = req.approvalThreshold
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="update",
                old_value=old,
                new_value={"name": outlet.name, "code": outlet.code, "status": str(outlet.status),
                           "approval_threshold": outlet.approval_threshold})
    db.commit()
    db.refresh(outlet)
    return _to_response(outlet)


@router.delete("/{outlet_id}", status_code=204)
def delete_outlet(outlet_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(require_permission("master-data", "manage"))):
    outlet = _active(db).filter(Outlet.id == outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    old = {"name": outlet.name, "code": outlet.code, "status": str(outlet.status)}
    outlet.deleted_at = datetime.now(timezone.utc)
    write_audit(db, table_name="outlets", record_id=str(outlet.id), action="delete", old_value=old)
    db.commit()
