from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth_service import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UpdateUserRequest,
    UserResponse,
    _user_to_response,
    delete_user,
    get_current_user,
    login_user,
    parse_whatsapp_number,
    register_user,
    require_permission,
    update_user,
)
from app.services.role_service import assert_can_assign_role, assert_can_grant

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    req: RegisterRequest,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_permission("users", "manage")),
):
    """Create a new user account. Requires users:manage, and the caller must
    already hold every right the assigned role grants."""
    assert_can_assign_role(db, caller, req.role)
    return register_user(db, req)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate and receive a JWT access token."""
    return login_user(db, req)


@router.get("/me", response_model=UserResponse)
def me(current_user: UserResponse = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


@router.get("/users", response_model=List[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_permission("users", "view", ("approvals", "manage"))),
):
    """Return all registered users (active and inactive). Requires users:view, or
    approvals:manage (the approval delegation picker lists users)."""
    users = db.query(User).order_by(User.name).all()
    return [_user_to_response(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user_endpoint(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_permission("users", "manage")),
):
    """Update user name, role, active status or outlet override. Requires users:manage."""
    _assert_can_manage_user(db, caller, user_id)
    if req.role is not None:
        assert_can_assign_role(db, caller, req.role)
    if req.outlet_ids:
        assert_can_grant(caller, {}, False, req.outlet_ids, "staff")
    return update_user(db, user_id, req, caller.id)


@router.delete("/users/{user_id}", status_code=204)
def delete_user_endpoint(
    user_id: str,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_permission("users", "manage")),
):
    """Soft-delete a user (sets is_active=false). Requires users:manage."""
    _assert_can_manage_user(db, caller, user_id)
    delete_user(db, user_id, caller.id)


def _assert_can_manage_user(db: Session, caller: UserResponse, user_id: str) -> None:
    """A delegated user manager must not be able to touch someone more
    privileged than themselves (e.g. deactivate an admin)."""
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    assert_can_assign_role(db, caller, target.role)


class PreferencesBody(BaseModel):
    preferences: Dict[str, Any]


@router.get("/me/preferences")
def get_preferences(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the authenticated user's preferences JSON."""
    user = db.query(User).filter(User.id == current_user.id).first()
    return user.preferences or {}


@router.patch("/me/preferences")
def update_preferences(
    body: PreferencesBody,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Dict[str, Any]:
    """Merge-update the authenticated user's preferences JSON."""
    user = db.query(User).filter(User.id == current_user.id).first()
    merged = {**(user.preferences or {}), **body.preferences}
    user.preferences = merged
    db.commit()
    db.refresh(user)
    return user.preferences


# ── WhatsApp (Todo-Pilot §4) ─────────────────────────────────────────────────
# The number is profile data (column), the opt-in switches are preferences
# (waEnabled + one wa* key per event, see whatsapp_service.WA_EVENT_PREFS).

class WhatsAppNumberBody(BaseModel):
    number: str = ""          # "" removes it


@router.patch("/me/whatsapp", response_model=UserResponse)
def set_my_whatsapp(
    body: WhatsAppNumberBody,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Save (normalise) or clear the caller's own WhatsApp number."""
    user = db.query(User).filter(User.id == current_user.id).first()
    user.whatsapp_number = parse_whatsapp_number(body.number)
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


@router.post("/me/whatsapp/test", status_code=202)
def send_my_whatsapp_test(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Dict[str, Any]:
    """Queue a test message to the caller's number. 409 when WhatsApp is not
    configured on the server or the caller has no number."""
    from app.config import settings
    from app.services.whatsapp_service import send_test

    if settings.whatsapp_backend == "disabled":
        raise HTTPException(status_code=409, detail="WhatsApp is not configured on the server (WUZAPI_URL).")
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user.whatsapp_number:
        raise HTTPException(status_code=409, detail="Save your WhatsApp number first.")
    row = send_test(db, user)
    db.commit()
    if row is None or row.status == "skipped":
        raise HTTPException(status_code=429, detail="Hourly WhatsApp limit reached — try again later.")
    return {"queued": True, "to": user.whatsapp_number}


@router.get("/whatsapp/status")
def whatsapp_status(_: UserResponse = Depends(get_current_user)) -> Dict[str, Any]:
    """Whether the server can send WhatsApp at all (Settings shows a hint)."""
    from app.config import settings
    return {"enabled": settings.whatsapp_backend != "disabled", "backend": settings.whatsapp_backend}
