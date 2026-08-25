"""Outlet scoping — the single enforcement point for multi-outlet access (Tier 4.2).

Everything outlet-related lives here on purpose. Scoping spread across routers is
how one endpoint gets forgotten, and a forgotten endpoint is a cross-outlet data
leak, not a cosmetic bug.

Model (see migration 024):
  - `outlet`    : denormalised name, for display
  - `outlet_id` : FK to outlets, for integrity + filtering. NULL means
                  "not tied to a single outlet" (shared / All Outlets).

Visibility rule for a non-admin:
    outlet_id IS NULL  OR  outlet_id IN (the user's outlets)

Admins bypass scoping entirely. A non-admin with no outlet assignment therefore
sees only shared rows — never everything.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.outlet import Outlet
from app.models.user import User

# Sentinel meaning "not tied to a single outlet" (present in existing data).
ALL_OUTLETS = "All Outlets"


# ---------------------------------------------------------------------------
# Write path — turn a denormalised outlet name into a real FK
# ---------------------------------------------------------------------------

def resolve_outlet_id(db: Session, outlet_name: Optional[str]) -> Optional[uuid.UUID]:
    """Resolve an outlet name to its id.

    Empty / None / the 'All Outlets' sentinel -> None ("shared", visible to all).
    An unknown name raises 422 rather than silently becoming NULL: a typo that
    resolved to NULL would make the record visible to *every* outlet, which is
    exactly the leak this tier exists to prevent.
    """
    if not outlet_name or outlet_name == ALL_OUTLETS:
        return None
    outlet = (
        db.query(Outlet)
        .filter(Outlet.name == outlet_name, Outlet.deleted_at.is_(None))
        .first()
    )
    if outlet is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown outlet {outlet_name!r}. Register it in master data first.",
        )
    return outlet.id


# ---------------------------------------------------------------------------
# Read path — scoping
# ---------------------------------------------------------------------------

def is_admin(user) -> bool:
    role = getattr(user, "role", None)
    return role == "admin"


def user_outlet_ids(db: Session, user) -> List[uuid.UUID]:
    """Outlet ids the user is assigned to. Empty list = assigned to none."""
    row = db.query(User).filter(User.id == user.id).first()
    if row is None:
        return []
    return [o.id for o in (row.outlets or [])]


def scope_filter(db: Session, model, user):
    """Return a SQLAlchemy filter expression restricting `model` to `user`'s outlets,
    or None when no restriction applies (admin, or model has no outlet_id)."""
    if is_admin(user):
        return None
    column = getattr(model, "outlet_id", None)
    if column is None:
        return None
    allowed = user_outlet_ids(db, user)
    if not allowed:
        # No assignment: shared rows only. Never "everything".
        return column.is_(None)
    return (column.is_(None)) | (column.in_(allowed))


def scoped_query(db: Session, model, user):
    """`db.query(model)` with outlet scoping already applied."""
    q = db.query(model)
    f = scope_filter(db, model, user)
    return q if f is None else q.filter(f)


def assert_can_write_outlet(db: Session, outlet_id, user) -> None:
    """Guard the write path: a non-admin may only create/modify records in an
    outlet they are assigned to.

    Without this, scoping is only half enforced — a manager could create records
    in another branch's outlet and then not even see them, silently polluting
    that branch's data.

    403 (not 404) on purpose: outlets are public master data, so their existence
    is not a secret, and the caller is being told they lack permission for an
    action they explicitly asked for.
    """
    if is_admin(user) or outlet_id is None:
        return                       # admin, or a shared/global record
    if outlet_id not in user_outlet_ids(db, user):
        raise HTTPException(status_code=403, detail="You are not assigned to this outlet")


def assert_can_access(db: Session, obj, user) -> None:
    """Guard a single fetched row.

    Raises 404 (not 403) for rows outside the caller's outlets — a 403 would
    confirm that the record exists, leaking information across outlets.
    """
    if obj is None or is_admin(user):
        return
    outlet_id = getattr(obj, "outlet_id", None)
    if outlet_id is None:
        return                       # shared row
    if outlet_id not in user_outlet_ids(db, user):
        raise HTTPException(status_code=404, detail="Not found")
