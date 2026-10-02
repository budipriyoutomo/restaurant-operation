from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.campaign import Campaign
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, require_permission
from app.services.campaign_service import campaign_metrics, results_error
from app.services.outlet_scope_service import (
    assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query,
)

router = APIRouter(prefix="/api/campaigns", tags=["marketing"])

VALID_STATUSES = {"draft", "active", "completed", "cancelled"}
VALID_TYPES    = {"promotion", "event", "social-media", "email", "other"}

_Money = Optional[int]


class CampaignMetrics(BaseModel):
    budget_used_pct: Optional[float]
    over_budget: bool
    cost_per_transaction: Optional[int]
    revenue_per_rupiah: Optional[float]
    revenue_uplift_pct: Optional[float]
    transaction_uplift_pct: Optional[float]


class CampaignResponse(BaseModel):
    id: str
    title: str
    type: str
    description: Optional[str]
    outlet: Optional[str]
    budget: Optional[int]                    # integer major units (Todo-Pilot §10)
    currency: str
    budget_legacy: Optional[str]             # old free text that could not be read
    start_date: Optional[str]
    end_date: Optional[str]
    status: str
    pic: Optional[str]
    actual_cost: Optional[int]
    result_transactions: Optional[int]
    result_revenue: Optional[int]
    baseline_transactions: Optional[int]
    baseline_revenue: Optional[int]
    metrics: CampaignMetrics
    created_at: datetime
    updated_at: datetime


class CreateCampaignRequest(BaseModel):
    title: str
    type: str = "other"
    description: Optional[str] = None
    outlet: Optional[str] = None
    budget: Optional[int] = Field(default=None, ge=0)
    currency: str = Field(default="IDR", pattern=r"^[A-Za-z]{3}$")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    pic: Optional[str] = None


class UpdateCampaignRequest(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    outlet: Optional[str] = None
    budget: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    pic: Optional[str] = None


class CampaignResultsRequest(BaseModel):
    """Only the fields sent are changed. Money in the campaign's currency."""
    actual_cost: _Money = Field(default=None, ge=0)
    result_transactions: Optional[int] = Field(default=None, ge=0)
    result_revenue: _Money = Field(default=None, ge=0)
    baseline_transactions: Optional[int] = Field(default=None, ge=0)
    baseline_revenue: _Money = Field(default=None, ge=0)


class CurrencyTotals(BaseModel):
    budget: int
    actual_cost: int
    revenue: int


class CampaignSummaryResponse(BaseModel):
    campaigns: int
    over_budget: int
    by_currency: Dict[str, CurrencyTotals]


def _metrics(c: Campaign) -> dict:
    return campaign_metrics(c.budget, c.actual_cost, c.result_transactions, c.result_revenue,
                            c.baseline_transactions, c.baseline_revenue)


def _to_response(c: Campaign) -> CampaignResponse:
    return CampaignResponse(
        id=str(c.id),
        title=c.title,
        type=c.type,
        description=c.description,
        outlet=c.outlet,
        budget=c.budget,
        currency=c.currency,
        budget_legacy=c.budget_legacy,
        start_date=c.start_date.isoformat() if c.start_date else None,
        end_date=c.end_date.isoformat() if c.end_date else None,
        status=c.status,
        pic=c.pic,
        actual_cost=c.actual_cost,
        result_transactions=c.result_transactions,
        result_revenue=c.result_revenue,
        baseline_transactions=c.baseline_transactions,
        baseline_revenue=c.baseline_revenue,
        metrics=CampaignMetrics(**_metrics(c)),
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _parse_date(value: Optional[str], field: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be an ISO date (YYYY-MM-DD)")


def _check_period(start: Optional[date], end: Optional[date]) -> None:
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="end_date cannot be before start_date")


def _get_campaign_or_404(db: Session, campaign_id: str, user) -> Campaign:
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_can_access(db, c, user)       # 404 for other outlets' campaigns
    return c


@router.get("/summary", response_model=CampaignSummaryResponse)
def campaign_summary(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "view", ("analytics", "view"))),
):
    """Budget / spend / revenue totals per currency (never summed across currencies)."""
    rows = scoped_query(db, Campaign, current_user).filter(Campaign.status != "cancelled").all()
    totals: dict = defaultdict(lambda: {"budget": 0, "actual_cost": 0, "revenue": 0})
    for c in rows:
        t = totals[c.currency]
        t["budget"] += c.budget or 0
        t["actual_cost"] += c.actual_cost or 0
        t["revenue"] += c.result_revenue or 0
    return CampaignSummaryResponse(
        campaigns=len(rows),
        over_budget=sum(1 for c in rows if _metrics(c)["over_budget"]),
        by_currency={cur: CurrencyTotals(**t) for cur, t in totals.items()},
    )


@router.get("", response_model=List[CampaignResponse])
def list_campaigns(
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "view")),
):
    q = scoped_query(db, Campaign, current_user)
    if status:
        if status not in VALID_STATUSES:     # native enum: a bad value would be a DB error
            raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}")
        q = q.filter(Campaign.status == status)
    if outlet:
        q = q.filter(Campaign.outlet == outlet)
    return [_to_response(c) for c in q.order_by(Campaign.created_at.desc()).all()]


@router.post("", response_model=CampaignResponse, status_code=201)
def create_campaign(
    req: CreateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "manage")),
):
    if req.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type. Must be one of: {', '.join(VALID_TYPES)}")
    start, end = _parse_date(req.start_date, "start_date"), _parse_date(req.end_date, "end_date")
    _check_period(start, end)
    outlet_id = resolve_outlet_id(db, req.outlet)
    assert_can_write_outlet(db, outlet_id, current_user)
    c = Campaign(
        title=req.title,
        type=req.type,
        description=req.description,
        outlet=req.outlet,
        outlet_id=outlet_id,
        budget=req.budget,
        currency=req.currency.upper(),
        start_date=start,
        end_date=end,
        pic=req.pic,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_response(c)


@router.patch("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: str,
    req: UpdateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "manage")),
):
    c = _get_campaign_or_404(db, campaign_id, current_user)
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
    if req.type and req.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type. Must be one of: {', '.join(VALID_TYPES)}")
    changes = req.model_dump(exclude_unset=True)
    start = _parse_date(changes["start_date"], "start_date") if "start_date" in changes else c.start_date
    end = _parse_date(changes["end_date"], "end_date") if "end_date" in changes else c.end_date
    _check_period(start, end)
    if "outlet" in changes:
        outlet_id = resolve_outlet_id(db, changes["outlet"])
        assert_can_write_outlet(db, outlet_id, current_user)
        c.outlet_id = outlet_id
    for field, value in changes.items():
        if field == "start_date":
            c.start_date = start
        elif field == "end_date":
            c.end_date = end
        elif field == "currency" and value:
            c.currency = value.upper()
        else:
            setattr(c, field, value)
    c.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return _to_response(c)


@router.patch("/{campaign_id}/results", response_model=CampaignResponse)
def record_results(
    campaign_id: str,
    req: CampaignResultsRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "manage")),
):
    """Spend (any time) and results — transactions / revenue in the campaign period,
    optionally a baseline period — once the campaign is active."""
    c = _get_campaign_or_404(db, campaign_id, current_user)
    changes = req.model_dump(exclude_unset=True)
    err = results_error(c.status, has_results=any(k != "actual_cost" for k in changes))
    if err:
        raise HTTPException(status_code=409, detail=err)
    old = {k: getattr(c, k) for k in changes}
    for field, value in changes.items():
        setattr(c, field, value)
    write_audit(db, table_name="campaigns", record_id=str(c.id), action="results",
                old_value=old, new_value={"title": c.title, **changes}, performed_by=current_user.email)
    c.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return _to_response(c)


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("marketing", "manage")),
):
    c = _get_campaign_or_404(db, campaign_id, current_user)
    db.delete(c)
    db.commit()
