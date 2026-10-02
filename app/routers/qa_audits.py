"""QA audit checklist endpoints (Todo-Pilot §7). Logic lives in qa_audit_service."""

import uuid
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.qa_audit import QAAuditFinding, QAAuditPhoto, QAAuditSession, QAAuditTemplate
from app.schemas.qa_audit import (
    AuditDetailResponse,
    AuditSummaryResponse,
    CreateTemplateRequest,
    FindingResponse,
    PhotoResponse,
    ScoresResponse,
    StartAuditRequest,
    TemplateResponse,
    UpdateFindingRequest,
    UpdateTemplateRequest,
)
from app.services import idempotency_service, storage_service
from app.services import qa_audit_service as svc
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, require_permission
from app.services.outlet_scope_service import (
    assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query,
)

templates_router = APIRouter(prefix="/api/qa-audit-templates", tags=["qa-audits"])
router = APIRouter(prefix="/api/qa-audits", tags=["qa-audits"])

_VIEW = require_permission("qa", "view")
_MANAGE = require_permission("qa", "manage")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _get_template_or_404(db: Session, template_id: str) -> QAAuditTemplate:
    t = db.query(QAAuditTemplate).filter(QAAuditTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Audit template not found")
    return t


@templates_router.get("", response_model=List[TemplateResponse])
def list_templates(
    include_inactive: bool = Query(False, alias="includeInactive"),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(_VIEW),
):
    q = db.query(QAAuditTemplate)
    if not include_inactive:
        q = q.filter(QAAuditTemplate.is_active.is_(True))
    return [svc.template_to_response(t) for t in q.order_by(QAAuditTemplate.name).all()]


@templates_router.post("", response_model=TemplateResponse, status_code=201)
def create_template(req: CreateTemplateRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.create_template(db, req, actor=current_user.email)


@templates_router.patch("/{template_id}", response_model=TemplateResponse)
def update_template(template_id: str, req: UpdateTemplateRequest, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_MANAGE)):
    return svc.update_template(db, _get_template_or_404(db, template_id), req, actor=current_user.email)


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------

def _get_session_or_404(db: Session, audit_id: str, user) -> QAAuditSession:
    s = db.query(QAAuditSession).filter(QAAuditSession.id == audit_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Audit not found")
    assert_can_access(db, s, user)      # 404 for other outlets' audits
    return s


def _get_finding_or_404(db: Session, s: QAAuditSession, finding_id: str) -> QAAuditFinding:
    f = next((f for f in s.findings if str(f.id) == finding_id), None)
    if f is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return f


@router.get("/scores", response_model=ScoresResponse)
def get_scores(
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("qa", "view", ("analytics", "view"))),
):
    """Per-outlet latest score + monthly trend over the last `months` months (submitted audits)."""
    today = date.today()
    first = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    sessions = (
        scoped_query(db, QAAuditSession, current_user)
        .filter(QAAuditSession.status == "submitted", QAAuditSession.audit_date >= first)
        .all()
    )
    return svc.scores(sessions)


@router.get("", response_model=List[AuditSummaryResponse])
def list_audits(
    outlet: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_VIEW),
):
    q = scoped_query(db, QAAuditSession, current_user)
    if outlet:
        q = q.filter(QAAuditSession.outlet == outlet)
    if status:
        q = q.filter(QAAuditSession.status == status)
    rows = q.order_by(QAAuditSession.audit_date.desc(), QAAuditSession.created_at.desc()).all()
    return [svc.session_to_summary(s) for s in rows]


@router.post("", response_model=AuditDetailResponse, status_code=201)
def start_audit(
    req: StartAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_MANAGE),
    idempotency_key: Optional[str] = Header(None),
):
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    template = _get_template_or_404(db, req.templateId)
    outlet_id = resolve_outlet_id(db, req.outlet)
    assert_can_write_outlet(db, outlet_id, current_user)
    try:
        audit_date = date.fromisoformat(req.auditDate) if req.auditDate else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="auditDate must be an ISO date (YYYY-MM-DD)")
    resp = svc.start_audit(db, template, req.outlet, outlet_id, audit_date,
                           auditor_id=uuid.UUID(current_user.id), auditor_name=current_user.name)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.get("/{audit_id}", response_model=AuditDetailResponse)
def get_audit(audit_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_VIEW)):
    return svc.session_to_detail(_get_session_or_404(db, audit_id, current_user))


@router.delete("/{audit_id}", status_code=204)
def discard_audit(audit_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_MANAGE)):
    """Throw away an audit that was started by mistake. Submitted audits are history."""
    s = _get_session_or_404(db, audit_id, current_user)
    svc.assert_editable(s)
    keys = [k for f in s.findings for p in f.photos for k in (p.storage_key, p.thumbnail_key)]
    write_audit(db, table_name="qa_audit_sessions", record_id=str(s.id), action="delete",
                old_value={"number": s.number, "outlet": s.outlet}, performed_by=current_user.email)
    db.delete(s)
    db.commit()
    for k in keys:
        storage_service.delete(k)


@router.patch("/{audit_id}/findings/{finding_id}", response_model=FindingResponse)
def update_finding(audit_id: str, finding_id: str, req: UpdateFindingRequest,
                   db: Session = Depends(get_db), current_user: UserResponse = Depends(_MANAGE)):
    s = _get_session_or_404(db, audit_id, current_user)
    svc.assert_editable(s)
    f = _get_finding_or_404(db, s, finding_id)
    if req.result is not None:
        f.result = req.result
    if req.notes is not None:
        f.notes = req.notes
    db.commit()
    db.refresh(f)
    return svc.finding_to_response(f)


@router.post("/{audit_id}/findings/{finding_id}/photos", response_model=PhotoResponse, status_code=201)
def upload_photo(
    audit_id: str,
    finding_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_MANAGE),
    idempotency_key: Optional[str] = Header(None),
):
    """Evidence photo (EXIF stripped, thumbnailed). A retried offline upload with
    the same Idempotency-Key returns the first result."""
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    s = _get_session_or_404(db, audit_id, current_user)
    svc.assert_editable(s)
    f = _get_finding_or_404(db, s, finding_id)
    stored = storage_service.save_image(file.file.read(), file.content_type or "")
    photo = QAAuditPhoto(
        finding_id=f.id, storage_key=stored.storage_key, thumbnail_key=stored.thumbnail_key,
        mime_type=stored.mime_type, size_bytes=stored.size_bytes, uploaded_by=uuid.UUID(current_user.id),
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    resp = svc.photo_to_response(s.id, f.id, photo)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


def _serve(db: Session, audit_id: str, finding_id: str, photo_id: str, user, thumbnail: bool):
    s = _get_session_or_404(db, audit_id, user)
    f = _get_finding_or_404(db, s, finding_id)
    p = next((p for p in f.photos if str(p.id) == photo_id), None)
    if p is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    key = (p.thumbnail_key or p.storage_key) if thumbnail else p.storage_key
    return StreamingResponse(storage_service.open_stream(key), media_type=p.mime_type)


@router.get("/{audit_id}/findings/{finding_id}/photos/{photo_id}/file")
def serve_photo(audit_id: str, finding_id: str, photo_id: str, db: Session = Depends(get_db),
                current_user: UserResponse = Depends(_VIEW)):
    return _serve(db, audit_id, finding_id, photo_id, current_user, thumbnail=False)


@router.get("/{audit_id}/findings/{finding_id}/photos/{photo_id}/thumbnail")
def serve_thumbnail(audit_id: str, finding_id: str, photo_id: str, db: Session = Depends(get_db),
                    current_user: UserResponse = Depends(_VIEW)):
    return _serve(db, audit_id, finding_id, photo_id, current_user, thumbnail=True)


@router.post("/{audit_id}/submit", response_model=AuditDetailResponse)
def submit_audit(audit_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_MANAGE)):
    """Score it, flag repeats, raise a Compliance Issue (+Task) per failed item."""
    return svc.submit_audit(db, _get_session_or_404(db, audit_id, current_user), actor=current_user.email)
