"""Integration tests — a password change signs out every other session.

Written before the implementation (TDD). users.token_version is in the JWT
("ver"); changing or resetting a password bumps it, so older tokens stop
working. Tokens issued before this feature (no "ver") count as version 0.
"""

import pytest

from app.config import settings
from app.core.tenancy import bypass_tenant
from app.models.user import User
from app.services import email_service
from app.services.auth_service import create_access_token
from tests.conftest import seed_user_headers

EMAIL = "sessions@test.test"
OLD = "Pass1234!"
NEW = "BrandNewPass99"


def _login(client, password=OLD):
    res = client.post("/api/auth/login", json={"email": EMAIL, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).status_code


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_sessions@test.test", "admin")


@pytest.fixture()
def user(client, db):
    seed_user_headers(db, EMAIL, "staff")
    with bypass_tenant(db):
        return db.query(User).filter(User.email == EMAIL).one()


def test_change_own_password_keeps_this_device_and_signs_out_the_others(client, user):
    phone, laptop = _login(client), _login(client)
    res = client.post("/api/auth/me/password", headers=laptop,
                      json={"current_password": OLD, "new_password": NEW})
    assert res.status_code == 200
    fresh = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert _me(client, fresh) == 200
    assert _me(client, phone) == 401
    assert _me(client, laptop) == 401


def test_admin_reset_signs_the_user_out(client, admin, user):
    session = _login(client)
    res = client.post(f"/api/auth/users/{user.id}/reset-password", headers=admin, json={"password": NEW})
    assert res.status_code == 200
    assert _me(client, session) == 401
    assert _me(client, _login(client, NEW)) == 200


def test_forgot_password_reset_signs_the_user_out(client, user, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "memory")
    monkeypatch.setattr(settings, "APP_URL", "https://ops.example.com")
    email_service.OUTBOX.clear()
    session = _login(client)
    client.post("/api/auth/forgot-password", json={"email": EMAIL})
    token = email_service.OUTBOX[-1].body.split("token=")[1].split()[0]
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": NEW}).status_code == 204
    assert _me(client, session) == 401
    email_service.OUTBOX.clear()


def test_a_token_without_a_version_still_works_until_the_first_change(client, user):
    from jose import jwt
    from datetime import datetime, timedelta, timezone
    legacy = jwt.encode({"sub": str(user.id), "email": EMAIL, "role": "staff",
                         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    headers = {"Authorization": f"Bearer {legacy}"}
    assert _me(client, headers) == 200
    client.post("/api/auth/me/password", headers=_login(client),
                json={"current_password": OLD, "new_password": NEW})
    assert _me(client, headers) == 401


def test_set_password_script_signs_the_user_out(client, db, user):
    from app.services.auth_service import set_password
    session = _login(client)
    set_password(db, user, NEW)
    assert _me(client, session) == 401
