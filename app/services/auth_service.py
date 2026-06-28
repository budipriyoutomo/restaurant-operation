"""JWT authentication service.

Existing endpoints remain public (no auth enforced yet).
Call get_current_user() on protected routes; call get_optional_user()
on routes that want user context but don't require it.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
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


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "staff"


class LoginRequest(BaseModel):
    email: str
    password: str


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


def _user_to_response(u: User) -> UserResponse:
    return UserResponse(id=str(u.id), email=u.email, name=u.name, role=u.role, is_active=u.is_active)


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


def login_user(db: Session, req: LoginRequest) -> TokenResponse:
    user = db.query(User).filter(User.email == req.email, User.is_active == True).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(str(user.id), user.email, user.role)
    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )
