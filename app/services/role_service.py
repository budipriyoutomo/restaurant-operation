"""Role management (migration 031): CRUD plus the privilege-escalation guard.

Anyone holding users:manage can edit roles, so without a guard a delegated
"HR" role could hand itself admin rights. The rule: a caller can never grant
(via a role definition or a role assignment) more than they hold themselves.
The superuser role is exempt — it already holds everything.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import permissions as perm
from app.models.outlet import Outlet
from app.models.role import Role
from app.models.user import User
from app.services.auth_service import UserResponse, role_permissions


class RoleResponse(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    permissions: Dict[str, str]
    all_outlets: bool
    outlet_ids: List[str]
    approval_tier: str
    is_system: bool
    user_count: int = 0


class CreateRoleRequest(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    permissions: Dict[str, str] = {}
    all_outlets: bool = False
    outlet_ids: List[str] = []
    approval_tier: str = "staff"


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[Dict[str, str]] = None
    all_outlets: Optional[bool] = None
    outlet_ids: Optional[List[str]] = None
    approval_tier: Optional[str] = None


class ModuleResponse(BaseModel):
    key: str
    label: str
    group: str
    manageable: bool


_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,49}$")


def _to_response(role: Role, user_count: int = 0) -> RoleResponse:
    return RoleResponse(
        key=role.key,
        name=role.name,
        description=role.description,
        permissions=role_permissions(role),
        all_outlets=role.all_outlets,
        outlet_ids=[str(o.id) for o in (role.outlets or [])],
        approval_tier=role.approval_tier,
        is_system=role.is_system,
        user_count=user_count,
    )


def _user_counts(db: Session) -> Dict[str, int]:
    rows = (
        db.query(User.role, func.count(User.id))
        .filter(User.is_active == True)  # noqa: E712
        .group_by(User.role)
        .all()
    )
    return {k: n for k, n in rows}


def _validate_permissions(raw: Dict[str, str]) -> Dict[str, str]:
    for key, level in raw.items():
        if key not in perm.MODULE_KEYS:
            raise HTTPException(status_code=422, detail=f"Unknown module '{key}'")
        if level not in perm.LEVELS:
            raise HTTPException(status_code=422, detail=f"Invalid level '{level}' for module '{key}'")
        if perm.rank(level) > perm.rank(perm.max_level(key)):
            raise HTTPException(status_code=422, detail=f"Module '{key}' supports at most '{perm.max_level(key)}'")
    return perm.normalize(raw)


def _validate_tier(tier: str) -> None:
    if tier not in perm.APPROVAL_TIERS:
        raise HTTPException(status_code=422, detail=f"approval_tier must be one of {list(perm.APPROVAL_TIERS)}")


def _resolve_outlets(db: Session, outlet_ids: List[str]) -> list:
    if not outlet_ids:
        return []
    outlets = db.query(Outlet).filter(Outlet.id.in_(outlet_ids), Outlet.deleted_at.is_(None)).all()
    missing = set(outlet_ids) - {str(o.id) for o in outlets}
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown outlet id(s): {', '.join(sorted(missing))}")
    return outlets


# ── Privilege-escalation guard ───────────────────────────────────────────────

def _is_superuser(caller: UserResponse) -> bool:
    return caller.role == perm.SUPERUSER_ROLE


def assert_can_grant(caller: UserResponse, permissions: Dict[str, str], all_outlets: bool,
                     outlet_ids: List[str], approval_tier: str) -> None:
    """403 unless every right in the definition is already held by the caller."""
    if _is_superuser(caller):
        return
    over = [
        m for m, level in permissions.items()
        if perm.rank(level) > perm.rank(caller.permissions.get(m, perm.NONE))
    ]
    if over:
        raise HTTPException(status_code=403, detail=f"Cannot grant more than you hold: {', '.join(over)}")
    if not caller.all_outlets:
        if all_outlets:
            raise HTTPException(status_code=403, detail="Cannot grant access to all outlets")
        outside = set(outlet_ids) - set(caller.effective_outlet_ids)
        if outside:
            raise HTTPException(status_code=403, detail="Cannot grant outlets you cannot access")
    if perm.APPROVAL_TIERS.index(approval_tier) > perm.APPROVAL_TIERS.index(caller.approval_tier):
        raise HTTPException(status_code=403, detail="Cannot grant a higher approval tier than your own")


def assert_can_assign_role(db: Session, caller: UserResponse, role_key: str) -> None:
    """Guard user↔role assignment with the same rule as role editing."""
    role = db.query(Role).filter(Role.key == role_key).first()
    if role is None:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role_key}'")
    assert_can_grant(
        caller, role_permissions(role), role.all_outlets,
        [str(o.id) for o in role.outlets], role.approval_tier,
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_roles(db: Session) -> List[RoleResponse]:
    counts = _user_counts(db)
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.name).all()
    return [_to_response(r, counts.get(r.key, 0)) for r in roles]


def get_role(db: Session, key: str) -> Role:
    role = db.query(Role).filter(Role.key == key).first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def create_role(db: Session, req: CreateRoleRequest, caller: UserResponse) -> RoleResponse:
    key = req.key.strip().lower()
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=422,
            detail="key must be 2–50 chars: lowercase letters, digits, '-' or '_', starting with a letter",
        )
    if db.query(Role).filter(Role.key == key).first():
        raise HTTPException(status_code=409, detail=f"Role '{key}' already exists")
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    permissions = _validate_permissions(req.permissions)
    _validate_tier(req.approval_tier)
    outlet_ids = [] if req.all_outlets else req.outlet_ids
    assert_can_grant(caller, permissions, req.all_outlets, outlet_ids, req.approval_tier)

    role = Role(
        key=key,
        name=req.name.strip(),
        description=req.description,
        permissions=permissions,
        all_outlets=req.all_outlets,
        approval_tier=req.approval_tier,
        is_system=False,
        outlets=_resolve_outlets(db, outlet_ids),
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return _to_response(role)


def update_role(db: Session, key: str, req: UpdateRoleRequest, caller: UserResponse) -> RoleResponse:
    role = get_role(db, key)

    access_change = any(
        v is not None for v in (req.permissions, req.all_outlets, req.outlet_ids, req.approval_tier)
    )
    if access_change and role.key == perm.SUPERUSER_ROLE:
        raise HTTPException(status_code=400, detail="The admin role always has full access and cannot be restricted")
    if access_change and caller.role == role.key and not _is_superuser(caller):
        raise HTTPException(status_code=403, detail="Cannot change the access of your own role")

    permissions = _validate_permissions(req.permissions) if req.permissions is not None else role_permissions(role)
    all_outlets = req.all_outlets if req.all_outlets is not None else role.all_outlets
    tier = req.approval_tier if req.approval_tier is not None else role.approval_tier
    _validate_tier(tier)
    if all_outlets:
        outlets = []
    elif req.outlet_ids is not None:
        outlets = _resolve_outlets(db, req.outlet_ids)
    else:
        outlets = list(role.outlets)
    if access_change:
        assert_can_grant(caller, permissions, all_outlets, [str(o.id) for o in outlets], tier)

    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(status_code=422, detail="name is required")
        role.name = req.name.strip()
    if req.description is not None:
        role.description = req.description
    role.permissions = permissions
    role.all_outlets = all_outlets
    role.approval_tier = tier
    role.outlets = outlets
    db.commit()
    db.refresh(role)
    return _to_response(role, _user_counts(db).get(role.key, 0))


def delete_role(db: Session, key: str) -> None:
    role = get_role(db, key)
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    in_use = db.query(User).filter(User.role == key).count()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Role is assigned to {in_use} user(s) (incl. inactive). Reassign them first.",
        )
    db.delete(role)
    db.commit()


def list_modules() -> List[ModuleResponse]:
    return [ModuleResponse(**m) for m in perm.MODULES]


def ensure_default_roles(db: Session) -> None:
    """Insert staff/manager/admin if missing. Migration 031 does this in real
    databases; tests build the schema with create_all() and call this instead."""
    defaults = [
        ("staff",   "Staff",   "Day-to-day operations",             False, "staff"),
        ("manager", "Manager", "Runs an outlet, approves requests", False, "manager"),
        ("admin",   "Admin",   "Full system access",                True,  "admin"),
    ]
    for key, name, desc, all_outlets, tier in defaults:
        if db.query(Role).filter(Role.key == key).first() is None:
            db.add(Role(
                key=key, name=name, description=desc,
                permissions=perm.DEFAULT_PERMISSIONS[key],
                all_outlets=all_outlets, approval_tier=tier, is_system=True,
            ))
    db.flush()
