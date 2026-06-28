from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.training_program import TrainingProgram
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/training-programs", tags=["training"])

VALID_STATUSES = {"scheduled", "ongoing", "completed", "cancelled"}


class TrainingProgramResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    target_role: str
    outlet: Optional[str]
    trainer: Optional[str]
    scheduled_date: Optional[str]
    duration_hours: Optional[float]
    status: str
    max_participants: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateTrainingProgramRequest(BaseModel):
    title: str
    description: Optional[str] = None
    target_role: str = "staff"
    outlet: Optional[str] = None
    trainer: Optional[str] = None
    scheduled_date: Optional[str] = None
    duration_hours: Optional[float] = None
    max_participants: Optional[int] = None


class UpdateTrainingProgramRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    target_role: Optional[str] = None
    outlet: Optional[str] = None
    trainer: Optional[str] = None
    scheduled_date: Optional[str] = None
    duration_hours: Optional[float] = None
    status: Optional[str] = None
    max_participants: Optional[int] = None


def _to_response(p: TrainingProgram) -> TrainingProgramResponse:
    return TrainingProgramResponse(
        id=str(p.id),
        title=p.title,
        description=p.description,
        target_role=p.target_role,
        outlet=p.outlet,
        trainer=p.trainer,
        scheduled_date=p.scheduled_date.isoformat() if p.scheduled_date else None,
        duration_hours=float(p.duration_hours) if p.duration_hours is not None else None,
        status=p.status,
        max_participants=p.max_participants,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=List[TrainingProgramResponse])
def list_programs(
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(TrainingProgram)
    if status:
        q = q.filter(TrainingProgram.status == status)
    if outlet:
        q = q.filter(TrainingProgram.outlet == outlet)
    return [_to_response(p) for p in q.order_by(TrainingProgram.created_at.desc()).all()]


@router.post("", response_model=TrainingProgramResponse, status_code=201)
def create_program(
    req: CreateTrainingProgramRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    from datetime import date
    p = TrainingProgram(
        title=req.title,
        description=req.description,
        target_role=req.target_role,
        outlet=req.outlet,
        trainer=req.trainer,
        scheduled_date=date.fromisoformat(req.scheduled_date) if req.scheduled_date else None,
        duration_hours=req.duration_hours,
        max_participants=req.max_participants,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_response(p)


@router.patch("/{program_id}", response_model=TrainingProgramResponse)
def update_program(
    program_id: str,
    req: UpdateTrainingProgramRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    from datetime import date
    p = db.query(TrainingProgram).filter(TrainingProgram.id == program_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Training program not found")
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
    for field, value in req.model_dump(exclude_unset=True).items():
        if field == "scheduled_date" and value:
            setattr(p, field, date.fromisoformat(value))
        else:
            setattr(p, field, value)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(p)
    return _to_response(p)


@router.delete("/{program_id}", status_code=204)
def delete_program(
    program_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    p = db.query(TrainingProgram).filter(TrainingProgram.id == program_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Training program not found")
    db.delete(p)
    db.commit()
