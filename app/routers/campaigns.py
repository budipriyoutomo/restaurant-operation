from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.campaign import Campaign
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/campaigns", tags=["marketing"])

VALID_STATUSES = {"draft", "active", "completed", "cancelled"}
VALID_TYPES    = {"promotion", "event", "social-media", "email", "other"}


class CampaignResponse(BaseModel):
    id: str
    title: str
    type: str
    description: Optional[str]
    outlet: Optional[str]
    budget: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    status: str
    pic: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateCampaignRequest(BaseModel):
    title: str
    type: str = "other"
    description: Optional[str] = None
    outlet: Optional[str] = None
    budget: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    pic: Optional[str] = None


class UpdateCampaignRequest(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    outlet: Optional[str] = None
    budget: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    pic: Optional[str] = None


def _to_response(c: Campaign) -> CampaignResponse:
    return CampaignResponse(
        id=str(c.id),
        title=c.title,
        type=c.type,
        description=c.description,
        outlet=c.outlet,
        budget=c.budget,
        start_date=c.start_date.isoformat() if c.start_date else None,
        end_date=c.end_date.isoformat() if c.end_date else None,
        status=c.status,
        pic=c.pic,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("", response_model=List[CampaignResponse])
def list_campaigns(
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(Campaign)
    if status:
        q = q.filter(Campaign.status == status)
    if outlet:
        q = q.filter(Campaign.outlet == outlet)
    return [_to_response(c) for c in q.order_by(Campaign.created_at.desc()).all()]


@router.post("", response_model=CampaignResponse, status_code=201)
def create_campaign(
    req: CreateCampaignRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    from datetime import date
    if req.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type. Must be one of: {', '.join(VALID_TYPES)}")
    c = Campaign(
        title=req.title,
        type=req.type,
        description=req.description,
        outlet=req.outlet,
        budget=req.budget,
        start_date=date.fromisoformat(req.start_date) if req.start_date else None,
        end_date=date.fromisoformat(req.end_date) if req.end_date else None,
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
    _: UserResponse = Depends(get_current_user),
):
    from datetime import date
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
    if req.type and req.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type. Must be one of: {', '.join(VALID_TYPES)}")
    for field, value in req.model_dump(exclude_unset=True).items():
        if field in ("start_date", "end_date") and value:
            setattr(c, field, date.fromisoformat(value))
        else:
            setattr(c, field, value)
    c.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return _to_response(c)


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(c)
    db.commit()
