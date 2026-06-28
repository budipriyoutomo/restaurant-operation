from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notification import Notification
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class NotificationResponse(BaseModel):
    id: str
    title: str
    message: str
    type: str
    entity_type: Optional[str]
    entity_id: Optional[str]
    read_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountResponse(BaseModel):
    count: int


def _to_response(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=str(n.id),
        title=n.title,
        message=n.message,
        type=n.type,
        entity_type=n.entity_type,
        entity_id=str(n.entity_id) if n.entity_id else None,
        read_at=n.read_at,
        created_at=n.created_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[NotificationResponse])
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """List notifications for the authenticated user, newest first."""
    q = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        q = q.filter(Notification.read_at == None)  # noqa: E711
    items = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [_to_response(n) for n in items]


@router.get("/unread-count", response_model=UnreadCountResponse)
def unread_count(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Return the number of unread notifications for the authenticated user."""
    count = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read_at == None)  # noqa: E711
        .count()
    )
    return UnreadCountResponse(count=count)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Mark a single notification as read."""
    n = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if not n:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(n)
    return _to_response(n)


@router.post("/read-all", status_code=204)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Mark all unread notifications for the authenticated user as read."""
    now = datetime.now(timezone.utc)
    (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read_at == None)  # noqa: E711
        .update({"read_at": now})
    )
    db.commit()
