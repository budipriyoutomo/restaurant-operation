"""JWT authentication service.

Existing endpoints remain public (no auth enforced yet).
Call get_current_user() on protected routes; call get_optional_user()
on routes that want user context but don't require it.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import permissions as perm
from app.config import settings
from app.database import get_db
from app.models.role import Role
from app.models.user import User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int   # seconds


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    # Personal outlet override (user_outlets). Empty = inherit the role's access.
    outlet_ids: List[str] = []
    # Resolved from the role (migration 031) — what the UI should honour.
    role_name: str = ""
    approval_tier: str = "staff"            # approver_role used in approval workflows
    permissions: Dict[str, str] = {}        # module key -> none | view | manage
    all_outlets: bool = False               # True = every outlet
    effective_outlet_ids: List[str] = []    # outlets visible when all_outlets is False
    whatsapp_number: Optional[str] = None   # normalised, e.g. 6281234567890 (Todo-Pilot §4)


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "staff"


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    # Replaces the user's outlet assignment wholesale (Tier 4.1).
    # None = leave unchanged; [] = explicitly clear all outlets.
    outlet_ids: Optional[List[str]] = None
    # None = leave unchanged; "" = remove the number (Todo-Pilot §4).
    whatsapp_number: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_access_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {"sub": user_id, "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return {}


def role_permissions(role: Optional[Role]) -> Dict[str, str]:
    """Module→level map for a role. The superuser role always gets everything,
    whatever is stored, so a bad edit can never lock admins out."""
    if role is None:
        return perm.normalize({})
    if role.key == perm.SUPERUSER_ROLE:
        return perm.full_access()
    return perm.normalize(role.permissions)


def _user_to_response(u: User) -> UserResponse:
    role = u.role_obj
    override = [str(o.id) for o in (u.outlets or [])]
    if override:
        all_outlets, effective = False, override
    elif role is not None and role.all_outlets:
        all_outlets, effective = True, []
    else:
        all_outlets = False
        effective = [str(o.id) for o in (role.outlets if role else []) if o.deleted_at is None]
    return UserResponse(
        id=str(u.id), email=u.email, name=u.name, role=u.role, is_active=u.is_active,
        outlet_ids=override,
        role_name=role.name if role else u.role,
        approval_tier=role.approval_tier if role else "staff",
        permissions=role_permissions(role),
        all_outlets=all_outlets,
        effective_outlet_ids=effective,
        whatsapp_number=u.whatsapp_number,
    )


def has_permission(user: UserResponse, module: str, level: str = perm.VIEW) -> bool:
    return perm.rank(user.permissions.get(module, perm.NONE)) >= perm.rank(level)


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def get_current_user(
    token: Optional[str] = Depends(_oauth2),
    db: Session = Depends(get_db),
) -> UserResponse:
    """Require a valid JWT. Raises 401 if missing or invalid."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = _decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return _user_to_response(user)


def require_permission(module: str, level: str = perm.VIEW, *alternatives: tuple):
    """Return a dependency requiring the caller's role to grant `level` on `module`.

    Extra (module, level) pairs are alternatives — any one suffices. Used for
    shared reads, e.g. the asset list is needed by both Assets and CMMS.

    Usage:
        _: UserResponse = Depends(require_permission("assets", "manage"))
        _: UserResponse = Depends(require_permission("assets", "view", ("cmms", "view")))
    """
    checks = [(module, level), *alternatives]
    for m, lv in checks:
        if m not in perm.MODULE_KEYS or lv not in perm.LEVELS:
            raise ValueError(f"Unknown permission {m}:{lv}")   # fail at import, not at request

    def _check(current: UserResponse = Depends(get_current_user)) -> UserResponse:
        if not any(has_permission(current, m, lv) for m, lv in checks):
            wanted = " or ".join(f"{m}:{lv}" for m, lv in checks)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {wanted}",
            )
        return current
    return _check


def _require_role_exists(db: Session, key: str) -> None:
    if db.query(Role).filter(Role.key == key).first() is None:
        raise HTTPException(status_code=422, detail=f"Unknown role '{key}'")


def get_optional_user(
    token: Optional[str] = Depends(_oauth2),
    db: Session = Depends(get_db),
) -> Optional[UserResponse]:
    """Return user if a valid token is provided, else None. Never raises."""
    if not token:
        return None
    payload = _decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    return _user_to_response(user) if user else None


# ── CRUD ─────────────────────────────────────────────────────────────────────

def register_user(db: Session, req: RegisterRequest) -> UserResponse:
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=409, detail=f"Email '{req.email}' already registered")
    _require_role_exists(db, req.role)
    user = User(
        email=req.email,
        name=req.name,
        password_hash=hash_password(req.password),
        role=req.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


def update_user(db: Session, user_id: str, req: UpdateUserRequest, caller_id: str) -> UserResponse:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if req.name is not None:
        user.name = req.name
    if req.role is not None:
        _require_role_exists(db, req.role)
        if str(user.id) == caller_id and req.role != user.role:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        user.role = req.role
    if req.is_active is not None:
        if not req.is_active and str(user.id) == caller_id:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
        user.is_active = req.is_active
    if req.outlet_ids is not None:
        user.outlets = _resolve_outlets(db, req.outlet_ids)
    if req.whatsapp_number is not None:
        user.whatsapp_number = parse_whatsapp_number(req.whatsapp_number)
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


def parse_whatsapp_number(raw: str) -> Optional[str]:
    """Normalise a number from the UI; 422 with the reason when it is not valid."""
    from app.services.whatsapp_service import normalize_phone
    try:
        return normalize_phone(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _resolve_outlets(db: Session, outlet_ids: List[str]) -> list:
    """Resolve outlet ids to Outlet rows, rejecting unknown/deleted ones.

    Assigning a user to an outlet that doesn't exist would silently grant them
    nothing, so this fails loudly instead.
    """
    from app.models.outlet import Outlet

    if not outlet_ids:
        return []
    outlets = (
        db.query(Outlet)
        .filter(Outlet.id.in_(outlet_ids), Outlet.deleted_at.is_(None))
        .all()
    )
    found = {str(o.id) for o in outlets}
    missing = [oid for oid in outlet_ids if oid not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown outlet id(s): {', '.join(missing)}")
    return outlets


def delete_user(db: Session, user_id: str, caller_id: str) -> None:
    if user_id == caller_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()


def login_user(db: Session, req: LoginRequest) -> TokenResponse:
    user = db.query(User).filter(User.email == req.email, User.is_active == True).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(str(user.id), user.email, user.role)
    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )
