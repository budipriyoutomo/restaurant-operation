"""Notification service — create and fan-out notifications to users.

Single exit point for every notification. A notification goes through every
channel in CHANNELS (Todo-Pilot §4):

  InAppChannel     always — the Notification row the bell/inbox shows
  EmailChannel     when the notification carries an `event` the user has on
                   for email (sent after commit — see email_service)
  WhatsAppChannel  when the user opted in to WhatsApp for that `event`
                   (outbox, sent after commit — see whatsapp_service)

Outlet scoping happens before any channel runs (notify_roles), so every
channel reaches the same recipients.
"""

from dataclasses import dataclass
from typing import List, Optional, Protocol

from sqlalchemy.orm import Session

from app.config import settings
from app.models.notification import Notification
from app.models.user import User
from app.services.email_service import queue_email
from app.services.whatsapp_service import format_body, queue_whatsapp


# Notification.type values (String column; frontend NotificationType).
NOTIFICATION_TYPES = ("info", "warning", "critical", "success")

# Events that may also go out by email, mapped to the user-preference key that
# switches them on/off (users.preferences, see Settings page). Default: on.
EMAIL_EVENT_PREFS = {
    "approval_pending":    "emailApprovalPending",
    "approval_escalated":  "emailApprovalEscalated",
    "approval_decided":    "emailApprovalDecided",
    "work_order_assigned": "emailWorkOrderAssigned",
}


def wants_email(preferences: Optional[dict], event: Optional[str]) -> bool:
    """Pure: does a user with these preferences want this event by email?"""
    if event not in EMAIL_EVENT_PREFS:
        return False
    return bool((preferences or {}).get(EMAIL_EVENT_PREFS[event], True))


def _email_body(message: str) -> str:
    lines = [message]
    if settings.APP_URL:
        lines += ["", f"Buka RestaurantOps: {settings.APP_URL.rstrip('/')}"]
    lines += ["", "—", "Email otomatis dari RestaurantOps. Atur notifikasi email di menu Settings."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutgoingNotification:
    title: str
    message: str
    ntype: str = "info"
    entity_type: Optional[str] = None
    entity_id: object = None
    event: Optional[str] = None      # key for the opt-in channels (email, WhatsApp)


class NotificationChannel(Protocol):
    def deliver(self, db: Session, user: User, note: OutgoingNotification) -> None: ...


class InAppChannel:
    def deliver(self, db: Session, user: User, note: OutgoingNotification) -> None:
        db.add(Notification(
            user_id=user.id,
            title=note.title,
            message=note.message,
            type=note.ntype,
            entity_type=note.entity_type,
            entity_id=note.entity_id,
        ))


class EmailChannel:
    def deliver(self, db: Session, user: User, note: OutgoingNotification) -> None:
        if note.event and wants_email(user.preferences, note.event):
            queue_email(db, user.email, f"[RestaurantOps] {note.title}", _email_body(note.message))


class WhatsAppChannel:
    def deliver(self, db: Session, user: User, note: OutgoingNotification) -> None:
        if note.event:   # preferences, dedup and rate limit are checked in queue_whatsapp
            queue_whatsapp(db, user, note.event, format_body(note.title, note.message),
                           note.entity_type, note.entity_id)


CHANNELS: List[NotificationChannel] = [InAppChannel(), EmailChannel(), WhatsAppChannel()]


def _create(
    db: Session,
    user: User,
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
    event: Optional[str] = None,
) -> None:
    note = OutgoingNotification(title, message, ntype, entity_type, entity_id, event)
    for channel in CHANNELS:
        channel.deliver(db, user, note)


def notify_roles(
    db: Session,
    roles: List[str],
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
    outlet_id=None,
    event: Optional[str] = None,
) -> None:
    """Fan-out a notification to all active users whose role acts at one of the
    given approval tiers (staff | manager | admin — see roles.approval_tier).

    When `outlet_id` is given, recipients are limited to users who can see that
    outlet (Tier 4.2b) — otherwise every manager in the company would be paged
    about a single branch's issue. All-outlet users always receive it, and an
    outlet_id of None (a shared/global record) fans out to everyone as before.
    """
    from app.models.role import Role
    from app.services.outlet_scope_service import allowed_outlet_ids

    users = (
        db.query(User)
        .join(Role, (Role.key == User.role) & (Role.company_id == User.company_id))
        .filter(Role.approval_tier.in_(roles), User.is_active == True)  # noqa: E712
        .all()
    )
    for u in users:
        if outlet_id is not None:
            allowed = allowed_outlet_ids(db, u)
            if allowed is not None and outlet_id not in allowed:
                continue
        _create(db, u, title, message, ntype, entity_type, entity_id, event)


def notify_by_name(
    db: Session,
    name: str,
    title: str,
    message: str,
    ntype: str = "info",
    entity_type: Optional[str] = None,
    entity_id=None,
    event: Optional[str] = None,
) -> None:
    """Best-effort: notify a user matched by display name (case-insensitive)."""
    if not name or name.lower() == "unassigned":
        return
    user = db.query(User).filter(User.name.ilike(name), User.is_active == True).first()  # noqa: E712
    if user:
        _create(db, user, title, message, ntype, entity_type, entity_id, event)


# ---------------------------------------------------------------------------
# Domain-specific helpers called from services
# ---------------------------------------------------------------------------

def notify_issue_created(db: Session, issue_number: str, title: str, outlet: str, issue_id,
                         outlet_id=None) -> None:
    notify_roles(
        db,
        roles=["manager", "admin"],
        title=f"New Issue: {issue_number}",
        message=f'"{title}" reported at {outlet}.',
        ntype="info",
        entity_type="issues",
        entity_id=issue_id,
        outlet_id=outlet_id,
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


def notify_next_approver(
    db: Session,
    approval_number: str,
    issue_number: str,
    approver_role: str,
    approval_id,
    approver_user_id=None,
    outlet_id=None,
) -> None:
    """Notify whoever must decide the newly-active approval step.

    If the step is pinned to a specific user (`approver_user_id`), notify only
    them; otherwise fan out to every active user holding `approver_role`.
    Called when an approval is created (step 1), advances to its next step
    (Tier 2.3) or is delegated. `issue_number` may be any subject label (a
    purchase-request approval has no issue).
    """
    title = f"Approval menunggu Anda: {approval_number}"
    message = f"Langkah approval untuk {issue_number} menunggu keputusan Anda."

    if approver_user_id is not None:
        user = db.query(User).filter(User.id == approver_user_id, User.is_active == True).first()  # noqa: E712
        if user:
            _create(db, user, title, message, "warning", "approvals", approval_id, "approval_pending")
            return

    notify_roles(
        db,
        roles=[approver_role],
        title=title,
        message=message,
        ntype="warning",
        entity_type="approvals",
        entity_id=approval_id,
        outlet_id=outlet_id,
        event="approval_pending",
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
        event="approval_decided",   # requester only; the role fan-out below stays in-app
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


def notify_work_order_assigned(db: Session, wo) -> None:
    """Notify the assignee of a work order (matched by display name).

    The WO assignee is a free-text name, so this is best-effort like
    notify_by_name: no matching active user, no notification.
    """
    notify_by_name(
        db,
        name=wo.assignee or "",
        title=f"Work order ditugaskan: {wo.number}",
        message=f'Anda ditugaskan ke "{wo.title}" ({wo.asset_name or "-"}, {wo.outlet or "-"}).',
        ntype="info",
        entity_type="work_orders",
        entity_id=wo.id,
        event="work_order_assigned",
    )


def notify_issue_ready_to_close(db: Session, issue) -> None:
    """Every Task / WO / Approval of an Issue is done — the Manager decides
    whether to close it (Todo-Pilot §1). Scoped to the Issue's outlet."""
    notify_roles(
        db,
        roles=["manager", "admin"],
        title=f"Issue siap ditutup: {issue.number}",
        message=f'Semua pekerjaan turunan "{issue.title}" sudah selesai. Tinjau dan tutup Issue ini.',
        ntype="success",
        entity_type="issues",
        entity_id=issue.id,
        outlet_id=issue.outlet_id,
        event="issue_ready_to_close",
    )
