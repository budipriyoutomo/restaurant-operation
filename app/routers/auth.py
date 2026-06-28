from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth_service import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
    _user_to_response,
    get_current_user,
    login_user,
    register_user,
    require_roles,
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
    """Return all registered users. Requires manager or admin role."""
    users = db.query(User).order_by(User.name).all()
    return [_user_to_response(u) for u in users]
