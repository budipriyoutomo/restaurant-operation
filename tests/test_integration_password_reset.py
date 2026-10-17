"""Integration tests — forgot password by email.

Written before the implementation (TDD).

  POST /api/auth/forgot-password   {email}                  always 202 (no account enumeration);
                                                            emails a one-time link for an active user
  POST /api/auth/reset-password    {token, new_password}    204; the link works once, for
                                                            PASSWORD_RESET_EXPIRE_MINUTES
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.tenancy import bypass_tenant
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services import email_service
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import seed_user_headers

EMAIL = "forgot@test.test"
OLD = "Pass1234!"
NEW = "BrandNewPass99"


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "memory")
    monkeypatch.setattr(settings, "APP_URL", "https://ops.example.com")
    email_service.OUTBOX.clear()
    yield email_service.OUTBOX
    email_service.OUTBOX.clear()


@pytest.fixture()
def user(client, db):
    seed_user_headers(db, EMAIL, "staff", name="Rina")
    with bypass_tenant(db):
        return db.query(User).filter(User.email == EMAIL).one()


def _forgot(client, email=EMAIL):
    return client.post("/api/auth/forgot-password", json={"email": email})


def _token_from(mail) -> str:
    m = re.search(r"https://ops\.example\.com/reset-password\?token=([A-Za-z0-9_\-]+)", mail.body)
    assert m, mail.body
    return m.group(1)


def _reset(client, token, password=NEW):
    return client.post("/api/auth/reset-password", json={"token": token, "new_password": password})


def _login(client, password, email=EMAIL):
    return client.post("/api/auth/login", json={"email": email, "password": password})


class TestForgotPassword:
    def test_sends_a_link_to_an_active_user(self, client, user, outbox):
        res = _forgot(client)
        assert res.status_code == 202
        assert len(outbox) == 1
        assert outbox[0].to == EMAIL
        assert _token_from(outbox[0])

    def test_unknown_email_looks_the_same_and_sends_nothing(self, client, user, outbox):
        known = _forgot(client)
        outbox.clear()
        unknown = _forgot(client, "nobody@nowhere.test")
        assert unknown.status_code == known.status_code == 202
        assert unknown.json() == known.json()
        assert outbox == []

    def test_email_is_matched_case_insensitively(self, client, user, outbox):
        _forgot(client, "  FORGOT@Test.test ")
        assert len(outbox) == 1

    def test_inactive_user_gets_nothing(self, client, db, user, outbox):
        with bypass_tenant(db):
            user.is_active = False
            db.flush()
        assert _forgot(client).status_code == 202
        assert outbox == []

    def test_at_most_three_emails_per_hour(self, client, user, outbox):
        for _ in range(5):
            assert _forgot(client).status_code == 202
        assert len(outbox) == 3

    def test_only_the_hash_is_stored(self, client, db, user, outbox):
        _forgot(client)
        token = _token_from(outbox[0])
        with bypass_tenant(db):
            row = db.query(PasswordResetToken).one()
        assert token not in row.token_hash and len(row.token_hash) == 64

    def test_email_is_in_the_users_language(self, client, db, user, outbox):
        with bypass_tenant(db):
            user.preferences = {"language": "en"}
            db.flush()
        _forgot(client)
        assert "Reset your password" in outbox[0].subject


class TestResetPassword:
    def test_link_sets_the_new_password(self, client, user, outbox):
        _forgot(client)
        res = _reset(client, _token_from(outbox[0]))
        assert res.status_code == 204
        assert _login(client, NEW).status_code == 200
        assert _login(client, OLD).status_code == 401

    def test_link_works_only_once(self, client, user, outbox):
        _forgot(client)
        token = _token_from(outbox[0])
        assert _reset(client, token).status_code == 204
        assert _reset(client, token, "AnotherPass123").status_code == 400
        assert _login(client, NEW).status_code == 200

    def test_using_one_link_voids_the_other_links(self, client, user, outbox):
        _forgot(client)
        _forgot(client)
        first, second = _token_from(outbox[0]), _token_from(outbox[1])
        assert _reset(client, second).status_code == 204
        assert _reset(client, first, "AnotherPass123").status_code == 400

    def test_expired_link_is_400(self, client, db, user, outbox):
        _forgot(client)
        token = _token_from(outbox[0])
        with bypass_tenant(db):
            row = db.query(PasswordResetToken).one()
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.flush()
        assert _reset(client, token).status_code == 400
        assert _login(client, OLD).status_code == 200

    def test_garbage_token_is_400(self, client, user):
        assert _reset(client, "not-a-real-token").status_code == 400

    def test_short_password_is_422_and_keeps_the_link(self, client, user, outbox):
        _forgot(client)
        token = _token_from(outbox[0])
        assert _reset(client, token, "short").status_code == 422
        assert _reset(client, token).status_code == 204

    def test_deactivated_after_request_cannot_reset(self, client, db, user, outbox):
        _forgot(client)
        token = _token_from(outbox[0])
        with bypass_tenant(db):
            user.is_active = False
            db.flush()
        assert _reset(client, token).status_code == 400

    def test_platform_admin_can_reset(self, client, db, outbox):
        with bypass_tenant(db):
            db.add(User(email="ops_forgot@platform.test", name="Ops", password_hash=hash_password(OLD),
                        role="staff", company_id=None, is_platform_admin=True))
            db.flush()
        _forgot(client, "ops_forgot@platform.test")
        assert _reset(client, _token_from(outbox[0])).status_code == 204
        assert _login(client, NEW, "ops_forgot@platform.test").status_code == 200
