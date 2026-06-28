from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.vendor import Vendor
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/vendors", tags=["procurement"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class VendorResponse(BaseModel):
    id: str
    name: str
    category: str
    contact_name: Optional[str]
    contact_phone: Optional[str]
    contact_email: Optional[str]
    address: Optional[str]
    outlet: Optional[str]
    is_active: bool
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateVendorRequest(BaseModel):
    name: str
    category: str = "General"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    outlet: Optional[str] = None
    notes: Optional[str] = None


class UpdateVendorRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    outlet: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


def _to_response(v: Vendor) -> VendorResponse:
    return VendorResponse(
        id=str(v.id),
        name=v.name,
        category=v.category,
        contact_name=v.contact_name,
        contact_phone=v.contact_phone,
        contact_email=v.contact_email,
        address=v.address,
        outlet=v.outlet,
        is_active=v.is_active,
        notes=v.notes,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[VendorResponse])
def list_vendors(
    category: Optional[str] = Query(None),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active == True)  # noqa: E712
    if category:
        q = q.filter(Vendor.category == category)
    return [_to_response(v) for v in q.order_by(Vendor.name).all()]


@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(
    req: CreateVendorRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    vendor = Vendor(**req.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return _to_response(vendor)


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(
    vendor_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return _to_response(v)


@router.patch("/{vendor_id}", response_model=VendorResponse)
def update_vendor(
    vendor_id: str,
    req: UpdateVendorRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(v, field, value)
    v.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(v)
    return _to_response(v)


@router.delete("/{vendor_id}", status_code=204)
def delete_vendor(
    vendor_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    v.is_active = False
    v.updated_at = datetime.now(timezone.utc)
    db.commit()
