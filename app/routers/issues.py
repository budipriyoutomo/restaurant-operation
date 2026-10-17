import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.issue import Issue
from app.services.outlet_scope_service import assert_can_access, assert_can_write_outlet, resolve_outlet_id
from app.schemas.issue import (
    BulkCloseFailure,
    BulkCloseRequest,
    BulkCloseResponse,
    CancelIssueRequest,
    CreateIssueCommentRequest,
    CreateIssueRequest,
    IssueCommentResponse,
    IssuePhotoResponse,
    IssueResponse,
    ReopenIssueRequest,
    ReviseApprovalRequest,
    UpdateIssueRequest,
)
from app.services import idempotency_service, issue_comment_service, issue_service, storage_service
from app.models.issue_photo import IssuePhoto
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_permission
from app.services.pagination import Page, paginate

router = APIRouter(prefix="/api/issues", tags=["issues"])

_ISSUE_VIEW = require_permission("issues", "view", ("dashboard", "view"), ("maintenance", "view"), ("qa", "view"), ("guest-service", "view"), ("it-support", "view"))


def _get_issue_or_404(db: Session, issue_id: str, user) -> Issue:
    """404 both when missing and when outside the caller's outlets."""
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    assert_can_access(db, issue, user)
    return issue


@router.post("", response_model=IssueResponse, status_code=201)
def create_issue(
    req: CreateIssueRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
    idempotency_key: Optional[str] = Header(None),
):
    """Create a new Issue and auto-generate Task/Approval based on toggles (FR-6).

    Up to four records come out of one call, so a retried/double-tapped submit
    carrying the same Idempotency-Key gets the first response back instead of
    a second set of records (Todo-Pilot §3).
    """
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    # A non-admin may only file issues for outlets they are assigned to.
    assert_can_write_outlet(db, resolve_outlet_id(db, req.outlet), current_user)
    resp = issue_service.create_issue(db, req)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.get("", response_model=List[IssueResponse])
def list_issues(
    response: Response,
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    module: Optional[str] = Query(None, description="Category module, e.g. maintenance — spans every category in it"),
    outlet: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search number, title or outlet"),
    active: bool = Query(False, description="Only issues not resolved/closed/cancelled"),
    ready: bool = Query(False, description="Only issues ready to close (Todo-Pilot §18)"),
    late: bool = Query(False, description="Only open issues past their due date"),
    page: Page = Depends(),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
):
    """List Issues with optional filters; `limit`/`offset` page it (Todo-Pilot §22)."""
    query = issue_service.issues_query(db, status, category, outlet, priority, q, user=current_user,
                                       active=active, ready=ready, late=late, module=module)
    return [issue_service._issue_to_response(i) for i in paginate(query, page, response, *issue_service.ISSUE_ORDER)]


@router.get("/{issue_id}", response_model=IssueResponse)
def get_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
):
    """Get a single Issue with its linked Task/Approval summaries (FR-7)."""
    _get_issue_or_404(db, issue_id, current_user)
    return issue_service.get_issue(db, issue_id)


@router.post("/bulk-close", response_model=BulkCloseResponse)
def bulk_close_issues(
    req: BulkCloseRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """Close many "Siap ditutup" Issues at once (Todo-Pilot §18 / UX-5).

    Each Issue is checked and closed on its own: one that is not ready, missing
    or outside the caller's outlets is reported in `failed` and does not stop
    the others.
    """
    closed: List[IssueResponse] = []
    failed: List[BulkCloseFailure] = []
    for issue_id in dict.fromkeys(req.ids):           # de-duplicate, keep order
        try:
            uuid.UUID(issue_id)
            issue = _get_issue_or_404(db, issue_id, current_user)
        except (ValueError, HTTPException):
            failed.append(BulkCloseFailure(id=issue_id, reason="Issue not found"))
            continue
        try:
            closed.append(issue_service.close_ready_issue(db, issue, actor=current_user.email))
        except ValueError as e:
            failed.append(BulkCloseFailure(id=issue_id, number=issue.number, reason=str(e)))
    return BulkCloseResponse(closed=closed, failed=failed)


@router.patch("/{issue_id}", response_model=IssueResponse)
def update_issue(
    issue_id: str,
    req: UpdateIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """Update Issue fields. Requires manager or admin role.

    Moving to resolved/closed is refused (409, with `blockers`) while any
    Task / Work Order / Approval of the Issue is still open (Todo-Pilot §1).
    """
    _get_issue_or_404(db, issue_id, current_user)
    return issue_service.update_issue(db, issue_id, req, actor=current_user.email)


@router.post("/{issue_id}/cancel", response_model=IssueResponse)
def cancel_issue(
    issue_id: str,
    req: CancelIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """Cancel the Issue with its open Tasks/WOs; a pending approval is rejected."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.cancel_issue(db, issue, req.reason, actor=current_user.email)


@router.post("/{issue_id}/reopen", response_model=IssueResponse)
def reopen_issue(
    issue_id: str,
    req: ReopenIssueRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """resolved → in-progress; a reason is required."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.reopen_issue(db, issue, req.reason, actor=current_user.email)


@router.post("/{issue_id}/revise-approval", response_model=IssueResponse)
def revise_approval(
    issue_id: str,
    req: ReviseApprovalRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("issues", "manage")),
):
    """After a rejection: resubmit a revised amount through the same approval."""
    issue = _get_issue_or_404(db, issue_id, current_user)
    return issue_service.revise_approval(db, issue, req.amount, req.reason, actor=current_user.email)


# ---------------------------------------------------------------------------
# Photos (Todo-Pilot §20 / UX-7) — same pipeline as work-order photos
# ---------------------------------------------------------------------------

@router.get("/{issue_id}/comments", response_model=List[IssueCommentResponse])
def list_issue_comments(issue_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_ISSUE_VIEW)):
    """Comments on an Issue, oldest first (Todo-Ops-Checklist P3)."""
    return issue_comment_service.list_comments(db, _get_issue_or_404(db, issue_id, current_user))


@router.post("/{issue_id}/comments", response_model=IssueCommentResponse, status_code=201)
def add_issue_comment(
    issue_id: str,
    req: CreateIssueCommentRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
    idempotency_key: Optional[str] = Header(None),
):
    """Comment on an Issue. Anyone who may see it may comment; the assignee is
    notified. Idempotent for the offline queue."""
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    issue = _get_issue_or_404(db, issue_id, current_user)
    comment = issue_comment_service.add_comment(
        db, issue, req.body, kind="user", author_user_id=current_user.id, author_name=current_user.name,
        actor=current_user.email)
    resp = issue_comment_service.to_response(comment)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


def _photo_response(issue_id, p: IssuePhoto) -> IssuePhotoResponse:
    base = f"/api/issues/{issue_id}/photos/{p.id}"
    return IssuePhotoResponse(
        id=str(p.id), caption=p.caption, fileUrl=f"{base}/file", thumbnailUrl=f"{base}/thumbnail",
        mimeType=p.mime_type, createdAt=p.created_at.isoformat() if p.created_at else "",
    )


@router.post("/{issue_id}/photos", response_model=IssuePhotoResponse, status_code=201)
def upload_issue_photo(
    issue_id: str,
    request: Request,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(_ISSUE_VIEW),
    idempotency_key: Optional[str] = Header(None),
):
    """Attach a photo to an Issue. Anyone who may see the Issue may add one —
    the reporter usually takes it. Idempotent for the offline queue; the image
    is validated, EXIF-stripped (no employee GPS) and thumbnailed."""
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    issue = _get_issue_or_404(db, issue_id, current_user)
    stored = storage_service.save_image(file.file.read(), file.content_type or "")
    photo = IssuePhoto(
        issue_id=issue.id, caption=caption, uploaded_by=uuid.UUID(current_user.id),
        storage_key=stored.storage_key, thumbnail_key=stored.thumbnail_key,
        mime_type=stored.mime_type, size_bytes=stored.size_bytes,
    )
    db.add(photo)
    write_audit(db, table_name="issue_photos", record_id=str(issue.id), action="upload",
                new_value={"issue": issue.number, "mime": stored.mime_type, "bytes": stored.size_bytes},
                performed_by=current_user.email)
    db.commit()
    db.refresh(photo)
    resp = _photo_response(issue.id, photo)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.get("/{issue_id}/photos", response_model=List[IssuePhotoResponse])
def list_issue_photos(issue_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_ISSUE_VIEW)):
    issue = _get_issue_or_404(db, issue_id, current_user)
    return [_photo_response(issue.id, p) for p in issue.photos]


def _photo_or_404(db: Session, issue_id: str, photo_id: str, user) -> IssuePhoto:
    _get_issue_or_404(db, issue_id, user)   # outlet scoping on the parent issue
    try:
        uuid.UUID(photo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Photo not found")
    p = db.query(IssuePhoto).filter(IssuePhoto.id == photo_id, IssuePhoto.issue_id == issue_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Photo not found")
    return p


@router.get("/{issue_id}/photos/{photo_id}/file")
def serve_issue_photo(issue_id: str, photo_id: str, db: Session = Depends(get_db),
                      current_user: UserResponse = Depends(_ISSUE_VIEW)):
    p = _photo_or_404(db, issue_id, photo_id, current_user)
    return StreamingResponse(storage_service.open_stream(p.storage_key), media_type=p.mime_type or "application/octet-stream")


@router.get("/{issue_id}/photos/{photo_id}/thumbnail")
def serve_issue_photo_thumbnail(issue_id: str, photo_id: str, db: Session = Depends(get_db),
                                current_user: UserResponse = Depends(_ISSUE_VIEW)):
    p = _photo_or_404(db, issue_id, photo_id, current_user)
    return StreamingResponse(storage_service.open_stream(p.thumbnail_key or p.storage_key),
                             media_type=p.mime_type or "application/octet-stream")
