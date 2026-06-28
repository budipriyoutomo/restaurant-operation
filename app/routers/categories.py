from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryResponse, CreateCategoryRequest, UpdateCategoryRequest
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/categories", tags=["master-data"])


def _to_response(cat: Category) -> CategoryResponse:
    return CategoryResponse(
        id=str(cat.id),
        name=cat.name,
        description=cat.description or "",
        type=cat.type.value if hasattr(cat.type, "value") else str(cat.type),
    )


def _active(db: Session):
    return db.query(Category).filter(Category.deleted_at.is_(None))


@router.get("", response_model=List[CategoryResponse])
def list_categories(db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    return [_to_response(c) for c in _active(db).order_by(Category.name).all()]


@router.post("", response_model=CategoryResponse, status_code=201)
def create_category(req: CreateCategoryRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    cat = Category(name=req.name, description=req.description, type=req.type)
    db.add(cat)
    db.flush()
    write_audit(db, table_name="categories", record_id=str(cat.id), action="create",
                new_value={"name": cat.name, "description": cat.description, "type": str(cat.type)})
    db.commit()
    db.refresh(cat)
    return _to_response(cat)


@router.get("/{category_id}", response_model=CategoryResponse)
def get_category(category_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    cat = _active(db).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return _to_response(cat)


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(category_id: str, req: UpdateCategoryRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    cat = _active(db).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    old = {"name": cat.name, "description": cat.description, "type": str(cat.type)}
    if req.name is not None:
        cat.name = req.name
    if req.description is not None:
        cat.description = req.description
    if req.type is not None:
        cat.type = req.type
    write_audit(db, table_name="categories", record_id=str(cat.id), action="update",
                old_value=old,
                new_value={"name": cat.name, "description": cat.description, "type": str(cat.type)})
    db.commit()
    db.refresh(cat)
    return _to_response(cat)


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("admin"))):
    cat = _active(db).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    old = {"name": cat.name, "description": cat.description, "type": str(cat.type)}
    cat.deleted_at = datetime.now(timezone.utc)
    write_audit(db, table_name="categories", record_id=str(cat.id), action="delete", old_value=old)
    db.commit()
