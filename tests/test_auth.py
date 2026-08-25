"""Integration tests: user registration, login, and /me endpoint.

Note: POST /api/auth/register is admin-guarded (RBAC). The initial admin is
created out-of-band by scripts/seed.py, so these tests seed an admin into the
test session and register through it.
"""

import pytest
from tests.conftest import seed_user_headers

_USER = {
    "email": "test_auth@restaurantops.test",
    "name": "Test User",
    "password": "Secure!Pass1",
    "role": "staff",
}


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_auth@restaurantops.test", "admin")


class TestRegister:
    def test_register_returns_201(self, client, admin):
        res = client.post("/api/auth/register", json=_USER, headers=admin)
        assert res.status_code == 201

    def test_register_returns_user_fields(self, client, admin):
        res = client.post("/api/auth/register", json=_USER, headers=admin)
        data = res.json()
        assert data["email"] == _USER["email"]
        assert data["name"] == _USER["name"]
        assert data["role"] == "staff"
        assert data["is_active"] is True
        assert "id" in data

    def test_duplicate_email_returns_409(self, client, admin):
        client.post("/api/auth/register", json=_USER, headers=admin)
        res = client.post("/api/auth/register", json=_USER, headers=admin)
        assert res.status_code == 409

    def test_register_without_token_returns_401(self, client):
        """RBAC: registration is admin-only."""
        res = client.post("/api/auth/register", json=_USER)
        assert res.status_code == 401

    def test_register_as_staff_returns_403(self, client, db):
        staff = seed_user_headers(db, "staff_auth@restaurantops.test", "staff")
        res = client.post("/api/auth/register", json=_USER, headers=staff)
        assert res.status_code == 403


class TestLogin:
    def test_login_returns_token(self, client, admin):
        client.post("/api/auth/register", json=_USER, headers=admin)
        res = client.post("/api/auth/login", json={"email": _USER["email"], "password": _USER["password"]})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_wrong_password_returns_401(self, client, admin):
        client.post("/api/auth/register", json=_USER, headers=admin)
        res = client.post("/api/auth/login", json={"email": _USER["email"], "password": "wrong"})
        assert res.status_code == 401

    def test_unknown_email_returns_401(self, client):
        res = client.post("/api/auth/login", json={"email": "nobody@nowhere.test", "password": "x"})
        assert res.status_code == 401


class TestMe:
    def _get_token(self, client, admin) -> str:
        client.post("/api/auth/register", json=_USER, headers=admin)
        return client.post("/api/auth/login", json={
            "email": _USER["email"], "password": _USER["password"]
        }).json()["access_token"]

    def test_me_returns_user(self, client, admin):
        token = self._get_token(client, admin)
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["email"] == _USER["email"]

    def test_me_without_token_returns_401(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401

    def test_me_with_bad_token_returns_401(self, client):
        res = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
        assert res.status_code == 401
