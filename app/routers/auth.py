from typing import Any, Dict, List

from fastapi import APIRouter, Depends
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
    register_user,
    require_roles,
    update_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    req: RegisterRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    """Create a new user account. Requires admin role."""
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
    _: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Return all registered users (active and inactive). Requires manager or admin role."""
    users = db.query(User).order_by(User.name).all()
    return [_user_to_response(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user_endpoint(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_roles("admin")),
):
    """Update user name, role, or active status. Requires admin role."""
    return update_user(db, user_id, req, caller.id)


@router.delete("/users/{user_id}", status_code=204)
def delete_user_endpoint(
    user_id: str,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_roles("admin")),
):
    """Soft-delete a user (sets is_active=false). Requires admin role."""
    delete_user(db, user_id, caller.id)


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
