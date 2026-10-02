"""Guest Service endpoints (Todo-Pilot §8). Logic lives in guest_service."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest_case import GuestCase
from app.models.issue import Issue
from app.schemas.guest_case import (
    CreateGuestCaseRequest,
    GuestCaseResponse,
    GuestKpiResponse,
    RecoveryRequest,
    RespondRequest,
    UpdateGuestCaseRequest,
)
from app.schemas.issue import CreateIssueRequest
from app.services import guest_service as svc
from app.services import idempotency_service
from app.services.auth_service import UserResponse, require_permission
from app.services.issue_service import create_issue
from app.services.outlet_scope_service import (
    assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query,
)

router = APIRouter(prefix="/api/guest-cases", tags=["guest-service"])

_VIEW = require_permission("guest-service", "view")
_MANAGE = require_permission("guest-service", "manage")


def _get_case_or_404(db: Session, case_id: str, user) -> GuestCase:
    c = db.query(GuestCase).filter(GuestCase.id == case_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Guest case not found")
    assert_can_access(db, c, user)
    return c


@router.get("/kpis", response_model=GuestKpiResponse)
def get_kpis(
    months: int = Query(3, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("guest-service", "view", ("analytics", "view"))),
):
    """First-response / resolution KPIs over complaints reported in the last `months` months."""
    since = datetime.now(timezone.utc) - timedelta(days=31 * months)
    cases = scoped_query(db, GuestCase, current_user).filter(GuestCase.reported_at >= since).all()
    return svc.kpis(cases)


@router.get("", response_model=List[GuestCaseResponse])
def list_cases(
    channel: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    open_only: bool = Query(False, alias="open"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
):
    q = scoped_query(db, GuestCase, current_user).join(Issue, Issue.id == GuestCase.issue_id)
    if channel:
        q = q.filter(GuestCase.channel == channel)
    if outlet:
        q = q.filter(GuestCase.outlet == outlet)
    if open_only:
        q = q.filter(Issue.status.notin_(["resolved", "closed", "cancelled"]))
    return [svc.case_to_response(c) for c in q.order_by(GuestCase.reported_at.desc()).all()]


@router.post("", response_model=GuestCaseResponse, status_code=201)
def create_case(
    req: CreateGuestCaseRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
    idempotency_key: Optional[str] = Header(None),
):
    """Log a guest complaint: a Guest Service Issue (+Task) and its guest case, in one transaction."""
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    assert_can_write_outlet(db, resolve_outlet_id(db, req.outlet), current_user)
    reported_at = svc.parse_reported_at(req.reportedAt)
    issue = create_issue(db, CreateIssueRequest(
        title=req.title, description=req.description, outlet=req.outlet,
        category=svc.GUEST_CATEGORY, priority=req.priority, assignee=req.assignee,
        dueDate=req.dueDate, generateTask=True,
    ), commit=False)
    case = db.query(GuestCase).filter(GuestCase.issue_id == issue.id).one()
    case.guest_name, case.guest_contact = req.guestName, req.guestContact
    case.channel, case.reported_at = req.channel, reported_at
    db.commit()
    db.refresh(case)
    resp = svc.case_to_response(case)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.get("/{case_id}", response_model=GuestCaseResponse)
def get_case(case_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW)):
    return svc.case_to_response(_get_case_or_404(db, case_id, current_user))


@router.patch("/{case_id}", response_model=GuestCaseResponse)
def update_case(case_id: str, req: UpdateGuestCaseRequest, db: Session = Depends(get_db),
                current_user: UserResponse = Depends(_VIEW)):
    c = _get_case_or_404(db, case_id, current_user)
    if req.guestName is not None:
        c.guest_name = req.guestName
    if req.guestContact is not None:
        c.guest_contact = req.guestContact
    if req.channel is not None:
        c.channel = req.channel
    if req.reportedAt is not None:
        c.reported_at = svc.parse_reported_at(req.reportedAt)
    db.commit()
    db.refresh(c)
    return svc.case_to_response(c)


@router.post("/{case_id}/respond", response_model=GuestCaseResponse)
def respond(case_id: str, req: RespondRequest, db: Session = Depends(get_db),
            current_user: UserResponse = Depends(_VIEW)):
    """Mark that staff responded to the guest. Only the first response counts."""
    return svc.respond(db, _get_case_or_404(db, case_id, current_user), req.note, current_user.name)


@router.patch("/{case_id}/recovery", response_model=GuestCaseResponse)
def record_recovery(case_id: str, req: RecoveryRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    """Compensation given to the guest (integer IDR). guest-service:manage."""
    return svc.record_recovery(db, _get_case_or_404(db, case_id, current_user), req, current_user.email)
