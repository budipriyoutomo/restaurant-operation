from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.budget import Budget
from app.models.outlet import Outlet
from app.schemas.budget import (
    BudgetResponse,
    CreateBudgetRequest,
    UpdateBudgetRequest,
)
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/budgets", tags=["procurement"])


def _to_response(b: Budget) -> BudgetResponse:
    return BudgetResponse(
        id=str(b.id), outletId=str(b.outlet_id), outlet=b.outlet,
        period=b.period, amount=int(b.amount or 0),
        createdAt=b.created_at.isoformat() if b.created_at else "",
    )


@router.get("", response_model=List[BudgetResponse])
def list_budgets(
    period: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(Budget)
    if period:
        q = q.filter(Budget.period == period)
    return [_to_response(b) for b in q.order_by(Budget.period.desc()).all()]


@router.post("", response_model=BudgetResponse, status_code=201)
def create_budget(
    req: CreateBudgetRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    outlet = db.query(Outlet).filter(Outlet.id == req.outletId, Outlet.deleted_at.is_(None)).first()
    if not outlet:
        raise HTTPException(status_code=422, detail="Unknown outlet")
    if db.query(Budget).filter(Budget.outlet_id == req.outletId, Budget.period == req.period).first():
        raise HTTPException(status_code=409, detail=f"Budget for {outlet.name} {req.period} already exists")
    b = Budget(outlet_id=outlet.id, outlet=outlet.name, period=req.period, amount=req.amount)
    db.add(b)
    db.commit()
    db.refresh(b)
    return _to_response(b)


@router.patch("/{budget_id}", response_model=BudgetResponse)
def update_budget(
    budget_id: str,
    req: UpdateBudgetRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    b = db.query(Budget).filter(Budget.id == budget_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found")
    b.amount = req.amount
    b.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(b)
    return _to_response(b)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(
    budget_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    b = db.query(Budget).filter(Budget.id == budget_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found")
    db.delete(b)
    db.commit()
