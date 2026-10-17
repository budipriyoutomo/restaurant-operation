"""Forgot password by email.

request_reset() emails a one-time link to an active user and is silent about
everything else (unknown email, inactive user, throttled, email off), so the
endpoint never reveals which addresses have an account. Only the SHA-256 of
the token is stored; using a link voids every other open link of that user.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenancy import bypass_tenant
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services.auth_service import set_password
from app.services.email_service import queue_email
from app.services.notification_texts import recipient_language

logger = logging.getLogger(__name__)

INVALID_LINK = "This reset link is invalid or has expired. Request a new one."


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _email(user: User, link: str) -> tuple:
    minutes = settings.PASSWORD_RESET_EXPIRE_MINUTES
    if recipient_language(user) == "en":
        return ("RestaurantOps — Reset your password",
                f"Hi {user.name},\n\n"
                f"Someone asked to reset the password of your RestaurantOps account.\n"
                f"Open this link within {minutes} minutes to choose a new password:\n\n{link}\n\n"
                f"Didn't ask for this? Ignore this email — your password stays the same.")
    return ("RestaurantOps — Reset password Anda",
            f"Halo {user.name},\n\n"
            f"Ada permintaan untuk me-reset password akun RestaurantOps Anda.\n"
            f"Buka link ini dalam {minutes} menit untuk membuat password baru:\n\n{link}\n\n"
            f"Tidak merasa meminta? Abaikan email ini — password Anda tidak berubah.")


def request_reset(db: Session, email: str) -> None:
    base = (settings.APP_URL or "").rstrip("/")
    if settings.email_backend == "disabled" or not base:
        logger.warning("forgot-password requested but email or APP_URL is not configured")
        return
    now = datetime.now(timezone.utc)
    with bypass_tenant(db):                     # before sign-in: the company is not known
        user = (db.query(User)
                .filter(func.lower(User.email) == email.strip().lower(), User.is_active == True)  # noqa: E712
                .first())
        if user is None:
            return
        recent = (db.query(PasswordResetToken)
                  .filter(PasswordResetToken.user_id == user.id,
                          PasswordResetToken.created_at > now - timedelta(hours=1))
                  .count())
        if recent >= settings.PASSWORD_RESET_MAX_PER_HOUR:
            return
        token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=user.id, token_hash=_hash(token), created_at=now,
            expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES),
        ))
        subject, body = _email(user, f"{base}/reset-password?token={token}")
        queue_email(db, user.email, subject, body)
        db.commit()


def reset_password(db: Session, token: str, new_password: str) -> None:
    now = datetime.now(timezone.utc)
    with bypass_tenant(db):
        row = (db.query(PasswordResetToken)
               .filter(PasswordResetToken.token_hash == _hash(token),
                       PasswordResetToken.used_at.is_(None),
                       PasswordResetToken.expires_at > now)
               .first())
        user = db.query(User).filter(User.id == row.user_id).first() if row else None
        if user is None or not user.is_active:
            raise HTTPException(status_code=400, detail=INVALID_LINK)
        set_password(db, user, new_password)
        (db.query(PasswordResetToken)
         .filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
         .update({PasswordResetToken.used_at: now}, synchronize_session=False))
        db.commit()
