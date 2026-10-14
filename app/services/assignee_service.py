"""Assignees are users, not free text (Todo-Pilot §17 / UX-4).

Issues, Tasks and Work Orders keep `assignee` (display name) next to
`assignee_user_id`. Writes accept either:

  assigneeUserId  the picker's choice — must be an active user of the company
                  who can work at the record's outlet, else 422
  assignee        legacy free text — linked to a user only when exactly one
                  active user has that name; otherwise stored unlinked (the UI
                  warns that nobody will be notified)

Notifications go to assignee_user_id, never to a name match.
"""

from __future__ import annotations

import uuid
from typing import Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User

UNASSIGNED = "Unassigned"


def is_unassigned(name: Optional[str]) -> bool:
    return not name or not name.strip() or name.strip().lower() == UNASSIGNED.lower()


def match_user_by_name(users: Iterable, name: Optional[str]):
    """Pure: id of the single active user called `name`, else None."""
    if is_unassigned(name):
        return None
    key = name.strip().lower()
    hits = [u.id for u in users if u.is_active and (u.name or "").strip().lower() == key]
    return hits[0] if len(hits) == 1 else None


def can_work_at(db: Session, user: User, outlet_id) -> bool:
    """Can `user` see records of this outlet? Shared records (no outlet): anyone."""
    from app.services.outlet_scope_service import allowed_outlet_ids

    if outlet_id is None:
        return True
    allowed = allowed_outlet_ids(db, user)
    return allowed is None or outlet_id in allowed


def assignable_users(db: Session, outlet_id) -> List[User]:
    users = db.query(User).filter(User.is_active == True).order_by(User.name).all()  # noqa: E712
    return [u for u in users if can_work_at(db, u, outlet_id)]


def resolve_assignee(
    db: Session,
    *,
    user_id: Optional[str],
    name: Optional[str],
    outlet_id,
) -> Tuple[str, Optional[uuid.UUID]]:
    """(display name, user id) to store. `user_id` wins over `name`."""
    if user_id:
        try:
            uid = uuid.UUID(str(user_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="Unknown assignee.")
        user = db.query(User).filter(User.id == uid).first()   # tenant-scoped: other companies are invisible
        if user is None or not user.is_active:
            raise HTTPException(status_code=422, detail="Unknown or inactive assignee.")
        if not can_work_at(db, user, outlet_id):
            raise HTTPException(status_code=422, detail=f"{user.name} has no access to this outlet.")
        return user.name, user.id

    if is_unassigned(name):
        return UNASSIGNED, None
    active = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    matched = match_user_by_name(active, name)
    if matched is None:
        return name.strip(), None
    user = next(u for u in active if u.id == matched)
    return user.name, user.id


def apply_assignee(db: Session, record, *, user_id: Optional[str], name: Optional[str]) -> bool:
    """Set record.assignee / assignee_user_id from a create/update request.

    Returns True when the assigned *user* changed to someone (→ notify them).
    """
    new_name, new_uid = resolve_assignee(db, user_id=user_id, name=name, outlet_id=record.outlet_id)
    changed = new_uid is not None and new_uid != record.assignee_user_id
    record.assignee = new_name
    record.assignee_user_id = new_uid
    return changed
