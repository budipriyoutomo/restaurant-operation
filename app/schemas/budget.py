from typing import Optional

from pydantic import BaseModel, Field


class BudgetResponse(BaseModel):
    id: str
    outletId: str
    outlet: Optional[str] = None
    period: str            # 'YYYY-MM'
    amount: int
    createdAt: str


class CreateBudgetRequest(BaseModel):
    outletId: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: int = Field(ge=0)


class UpdateBudgetRequest(BaseModel):
    amount: int = Field(ge=0)


class BudgetStatusEntry(BaseModel):
    budgetId: str
    outletId: str
    outlet: Optional[str] = None
    period: str
    amount: int
    spentWorkOrders: int
    spentPurchaseOrders: int
    spent: int
    remaining: int
    pct: float
    overBudget: bool
    warning: bool
