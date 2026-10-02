import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.training_enrollment import TrainingEnrollment
from app.models.training_program import TrainingProgram
from app.services import training_service as svc
from app.services.auth_service import UserResponse, require_permission
from app.services.outlet_scope_service import (
    assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query,
)

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
    # Todo-Pilot §9
    enrolled_count: int = 0
    attended_count: int = 0
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
    max_participants: Optional[int] = Field(default=None, ge=1)


class UpdateTrainingProgramRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    target_role: Optional[str] = None
    outlet: Optional[str] = None
    trainer: Optional[str] = None
    scheduled_date: Optional[str] = None
    duration_hours: Optional[float] = None
    status: Optional[str] = None
    max_participants: Optional[int] = Field(default=None, ge=1)


class EnrollRequest(BaseModel):
    user_ids: List[uuid.UUID] = Field(min_length=1)


class UpdateEnrollmentRequest(BaseModel):
    status: Optional[str] = None             # registered | attended | no-show
    score: Optional[int] = None              # 0–100, attended only
    notes: Optional[str] = Field(default=None, max_length=2000)


class EnrollmentResponse(BaseModel):
    id: str
    program_id: str
    user_id: str
    user_name: str
    role_name: str
    outlet: str
    status: str
    score: Optional[int]
    notes: str
    enrolled_at: str
    marked_at: Optional[str]


class RecapBucket(BaseModel):
    enrolled: int
    attended: int
    no_show: int
    pending: int
    attendance_rate: Optional[float]


class RecapRow(RecapBucket):
    key: str


class AttendanceRecapResponse(BaseModel):
    totals: RecapBucket
    by_outlet: List[RecapRow]
    by_role: List[RecapRow]


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
        enrolled_count=len(p.enrollments),
        attended_count=sum(1 for e in p.enrollments if e.status == "attended"),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _enrollment_response(e: TrainingEnrollment) -> EnrollmentResponse:
    return EnrollmentResponse(
        id=str(e.id), program_id=str(e.program_id), user_id=str(e.user_id), user_name=e.user_name,
        role_name=e.role_name, outlet=e.outlet, status=e.status, score=e.score, notes=e.notes or "",
        enrolled_at=e.enrolled_at.isoformat() if e.enrolled_at else "",
        marked_at=e.marked_at.isoformat() if e.marked_at else None,
    )


def _get_program_or_404(db: Session, program_id: str, user) -> TrainingProgram:
    p = db.query(TrainingProgram).filter(TrainingProgram.id == program_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Training program not found")
    assert_can_access(db, p, user)       # 404 for other outlets' programs
    return p


def _get_enrollment_or_404(p: TrainingProgram, enrollment_id: str) -> TrainingEnrollment:
    e = next((e for e in p.enrollments if str(e.id) == enrollment_id), None)
    if e is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return e


@router.get("/attendance", response_model=AttendanceRecapResponse)
def attendance_recap(
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("training", "view")),
):
    """Attendance by outlet and by role over programs scheduled in the last `months` months
    (undated programs included)."""
    since = date.today() - timedelta(days=31 * months)
    programs = scoped_query(db, TrainingProgram, current_user).filter(
        (TrainingProgram.scheduled_date >= since) | (TrainingProgram.scheduled_date.is_(None))
    ).all()
    rows = [(e.outlet, e.role_name, e.status) for p in programs for e in p.enrollments]
    return svc.attendance_recap(rows)


@router.get("", response_model=List[TrainingProgramResponse])
def list_programs(
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("training", "view")),
):
    q = scoped_query(db, TrainingProgram, current_user)
    if status:
        if status not in VALID_STATUSES:     # native enum: a bad value would be a DB error
            raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}")
        q = q.filter(TrainingProgram.status == status)
    if outlet:
        q = q.filter(TrainingProgram.outlet == outlet)
    return [_to_response(p) for p in q.order_by(TrainingProgram.created_at.desc()).all()]


@router.post("", response_model=TrainingProgramResponse, status_code=201)
def create_program(
    req: CreateTrainingProgramRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("training", "manage")),
):
    outlet_id = resolve_outlet_id(db, req.outlet)
    assert_can_write_outlet(db, outlet_id, current_user)
    p = TrainingProgram(
        title=req.title,
        description=req.description,
        target_role=req.target_role,
        outlet=req.outlet,
        outlet_id=outlet_id,
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
    current_user: UserResponse = Depends(require_permission("training", "manage")),
):
    p = _get_program_or_404(db, program_id, current_user)
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
    changes = req.model_dump(exclude_unset=True)
    if changes.get("max_participants") is not None and changes["max_participants"] < len(p.enrollments):
        raise HTTPException(status_code=409, detail=(
            f"{len(p.enrollments)} people are already enrolled — unenroll some before lowering the limit"))
    if "outlet" in changes:
        outlet_id = resolve_outlet_id(db, changes["outlet"])
        assert_can_write_outlet(db, outlet_id, current_user)
        p.outlet_id = outlet_id
    for field, value in changes.items():
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
    current_user: UserResponse = Depends(require_permission("training", "manage")),
):
    p = _get_program_or_404(db, program_id, current_user)
    db.delete(p)
    db.commit()


# ── Enrollment & attendance (Todo-Pilot §9) ──────────────────────────────────

@router.get("/{program_id}/enrollments", response_model=List[EnrollmentResponse])
def list_enrollments(program_id: str, db: Session = Depends(get_db),
                     current_user: UserResponse = Depends(require_permission("training", "view"))):
    return [_enrollment_response(e) for e in _get_program_or_404(db, program_id, current_user).enrollments]


@router.post("/{program_id}/enrollments", response_model=List[EnrollmentResponse], status_code=201)
def enroll(program_id: str, req: EnrollRequest, db: Session = Depends(get_db),
           current_user: UserResponse = Depends(require_permission("training", "manage"))):
    """Enroll participants (all or nothing): 409 when over max_participants or already enrolled."""
    p = _get_program_or_404(db, program_id, current_user)
    return [_enrollment_response(e) for e in svc.enroll(db, p, req.user_ids, actor=current_user.email)]


@router.patch("/{program_id}/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
def update_enrollment(program_id: str, enrollment_id: str, req: UpdateEnrollmentRequest,
                      db: Session = Depends(get_db),
                      current_user: UserResponse = Depends(require_permission("training", "manage"))):
    """Mark attended / no-show (from the training day), score 0–100 for attended."""
    p = _get_program_or_404(db, program_id, current_user)
    e = _get_enrollment_or_404(p, enrollment_id)
    e = svc.mark(db, p, e, req.status, req.score, req.notes,
                 score_sent="score" in req.model_fields_set, actor=current_user.email)
    return _enrollment_response(e)


@router.delete("/{program_id}/enrollments/{enrollment_id}", status_code=204)
def unenroll(program_id: str, enrollment_id: str, db: Session = Depends(get_db),
             current_user: UserResponse = Depends(require_permission("training", "manage"))):
    p = _get_program_or_404(db, program_id, current_user)
    e = _get_enrollment_or_404(p, enrollment_id)
    if e.status != "registered":
        raise HTTPException(status_code=409, detail="Attendance is already recorded — set it back to registered first")
    db.delete(e)
    db.commit()
