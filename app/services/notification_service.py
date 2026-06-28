"""Notification service — create and fan-out notifications to users."""

from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User


def _create(
    db: Session,
    user_id,
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
) -> None:
    db.add(Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=ntype,
        entity_type=entity_type,
        entity_id=entity_id,
    ))


def notify_roles(
    db: Session,
    roles: List[str],
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
) -> None:
    """Fan-out a notification to all active users that have one of the given roles."""
    users = db.query(User).filter(User.role.in_(roles), User.is_active == True).all()  # noqa: E712
    for u in users:
        _create(db, u.id, title, message, ntype, entity_type, entity_id)


def notify_by_name(
    db: Session,
    name: str,
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
) -> None:
    """Best-effort: notify a user matched by display name (case-insensitive)."""
    user = db.query(User).filter(User.name.ilike(name), User.is_active == True).first()  # noqa: E712
    if user:
        _create(db, user.id, title, message, ntype, entity_type, entity_id)


# ---------------------------------------------------------------------------
# Domain-specific helpers called from services
# ---------------------------------------------------------------------------

def notify_issue_created(db: Session, issue_number: str, title: str, outlet: str, issue_id) -> None:
    notify_roles(
        db,
        roles=["manager", "admin"],
        title=f"New Issue: {issue_number}",
        message=f'"{title}" reported at {outlet}.',
        ntype="info",
        entity_type="issues",
        entity_id=issue_id,
    )


def notify_issue_status_changed(
    db: Session, issue_number: str, title: str, old_status: str, new_status: str, issue_id
) -> None:
    ntype = "success" if new_status in ("resolved", "closed") else "warning" if new_status == "waiting" else "info"
    notify_roles(
        db,
        roles=["manager", "admin"],
        title=f"Issue {issue_number} → {new_status}",
        message=f'"{title}" changed from {old_status} to {new_status}.',
        ntype=ntype,
        entity_type="issues",
        entity_id=issue_id,
    )


def notify_approval_decided(
    db: Session,
    approval_number: str,
    issue_number: str,
    decision: str,
    requester_name: str,
    approval_id,
) -> None:
    ntype = "success" if decision == "approved" else "critical"
    label = "Approved" if decision == "approved" else "Rejected"

    # Notify the requester (best-effort by name)
    notify_by_name(
        db,
        name=requester_name,
        title=f"Approval {label}: {approval_number}",
        message=f"Your request linked to {issue_number} was {decision}.",
        ntype=ntype,
        entity_type="approvals",
        entity_id=approval_id,
    )
    # Also notify all managers/admins
    notify_roles(
        db,
        roles=["manager", "admin"],
        title=f"Approval {label}: {approval_number}",
        message=f"Request linked to {issue_number} was {decision} by approver.",
        ntype=ntype,
        entity_type="approvals",
        entity_id=approval_id,
    )
