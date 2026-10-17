import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryResponse, CreateCategoryRequest, UpdateCategoryRequest
from app.services import category_service as svc
from app.services.auth_service import UserResponse, get_current_user, require_permission

router = APIRouter(prefix="/api/categories", tags=["master-data"])

_MANAGE = require_permission("master-data", "manage")


def _get_or_404(db: Session, category_id: str) -> Category:
    try:
        cid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Category not found")
    cat = svc.active_query(db).filter(Category.id == cid).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@router.get("", response_model=List[CategoryResponse])
def list_categories(db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    """All non-deleted categories, inactive included (the Issue form shows active ones)."""
    return [svc.to_response(c) for c in svc.active_query(db).order_by(Category.name).all()]


@router.post("", response_model=CategoryResponse, status_code=201)
def create_category(req: CreateCategoryRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.create_category(db, req, actor=current_user.email)


@router.get("/{category_id}", response_model=CategoryResponse)
def get_category(category_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    return svc.to_response(_get_or_404(db, category_id))


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(category_id: str, req: UpdateCategoryRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.update_category(db, _get_or_404(db, category_id), req, actor=current_user.email)


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: str, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    svc.delete_category(db, _get_or_404(db, category_id), actor=current_user.email)
