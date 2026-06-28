from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.category import Category
from app.models.pic import PIC
from app.schemas.pic import CreatePICRequest, PICResponse, UpdatePICRequest
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/pics", tags=["master-data"])


def _to_response(pic: PIC) -> PICResponse:
    return PICResponse(
        id=str(pic.id),
        name=pic.name,
        email=pic.email,
        phone=pic.phone,
        department=pic.department,
        categories=[str(c.id) for c in pic.categories],
    )


def _active_pics(db: Session):
    return db.query(PIC).filter(PIC.deleted_at.is_(None))


def _resolve_categories(db: Session, category_ids: List[str]) -> List[Category]:
    if not category_ids:
        return []
    cats = db.query(Category).filter(Category.id.in_(category_ids), Category.deleted_at.is_(None)).all()
    if len(cats) != len(category_ids):
        found_ids = {str(c.id) for c in cats}
        missing = [cid for cid in category_ids if cid not in found_ids]
        raise HTTPException(status_code=422, detail=f"Category IDs not found: {missing}")
    return cats


@router.get("", response_model=List[PICResponse])
def list_pics(db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    return [_to_response(p) for p in _active_pics(db).order_by(PIC.name).all()]


@router.post("", response_model=PICResponse, status_code=201)
def create_pic(req: CreatePICRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    existing = _active_pics(db).filter(PIC.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Email '{req.email}' already exists")
    cats = _resolve_categories(db, req.categories)
    pic = PIC(name=req.name, email=req.email, phone=req.phone, department=req.department)
    pic.categories = cats
    db.add(pic)
    db.flush()
    write_audit(db, table_name="pics", record_id=str(pic.id), action="create",
                new_value={"name": pic.name, "email": pic.email, "department": pic.department})
    db.commit()
    db.refresh(pic)
    return _to_response(pic)


@router.get("/{pic_id}", response_model=PICResponse)
def get_pic(pic_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    pic = _active_pics(db).filter(PIC.id == pic_id).first()
    if not pic:
        raise HTTPException(status_code=404, detail="PIC not found")
    return _to_response(pic)


@router.patch("/{pic_id}", response_model=PICResponse)
def update_pic(pic_id: str, req: UpdatePICRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    pic = _active_pics(db).filter(PIC.id == pic_id).first()
    if not pic:
        raise HTTPException(status_code=404, detail="PIC not found")
    old = {"name": pic.name, "email": pic.email, "department": pic.department}
    if req.name is not None:
        pic.name = req.name
    if req.email is not None:
        pic.email = req.email
    if req.phone is not None:
        pic.phone = req.phone
    if req.department is not None:
        pic.department = req.department
    if req.categories is not None:
        pic.categories = _resolve_categories(db, req.categories)
    write_audit(db, table_name="pics", record_id=str(pic.id), action="update",
                old_value=old,
                new_value={"name": pic.name, "email": pic.email, "department": pic.department})
    db.commit()
    db.refresh(pic)
    return _to_response(pic)


@router.delete("/{pic_id}", status_code=204)
def delete_pic(pic_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    pic = _active_pics(db).filter(PIC.id == pic_id).first()
    if not pic:
        raise HTTPException(status_code=404, detail="PIC not found")
    old = {"name": pic.name, "email": pic.email, "department": pic.department}
    pic.deleted_at = datetime.now(timezone.utc)
    write_audit(db, table_name="pics", record_id=str(pic.id), action="delete", old_value=old)
    db.commit()
