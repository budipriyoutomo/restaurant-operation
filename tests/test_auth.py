"""Integration tests: user registration, login, and /me endpoint."""

import pytest

_USER = {
    "email": "test_auth@restaurantops.test",
    "name": "Test User",
    "password": "Secure!Pass1",
    "role": "staff",
}


class TestRegister:
    def test_register_returns_201(self, client):
        res = client.post("/api/auth/register", json=_USER)
        assert res.status_code == 201

    def test_register_returns_user_fields(self, client):
        res = client.post("/api/auth/register", json=_USER)
        data = res.json()
        assert data["email"] == _USER["email"]
        assert data["name"] == _USER["name"]
        assert data["role"] == "staff"
        assert data["is_active"] is True
        assert "id" in data

    def test_duplicate_email_returns_409(self, client):
        client.post("/api/auth/register", json=_USER)
        res = client.post("/api/auth/register", json=_USER)
        assert res.status_code == 409


class TestLogin:
    def test_login_returns_token(self, client):
        client.post("/api/auth/register", json=_USER)
        res = client.post("/api/auth/login", json={"email": _USER["email"], "password": _USER["password"]})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_wrong_password_returns_401(self, client):
        client.post("/api/auth/register", json=_USER)
        res = client.post("/api/auth/login", json={"email": _USER["email"], "password": "wrong"})
        assert res.status_code == 401

    def test_unknown_email_returns_401(self, client):
        res = client.post("/api/auth/login", json={"email": "nobody@nowhere.test", "password": "x"})
        assert res.status_code == 401


class TestMe:
    def _get_token(self, client) -> str:
        client.post("/api/auth/register", json=_USER)
        return client.post("/api/auth/login", json={
            "email": _USER["email"], "password": _USER["password"]
        }).json()["access_token"]

    def test_me_returns_user(self, client):
        token = self._get_token(client)
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["email"] == _USER["email"]

    def test_me_without_token_returns_401(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401

    def test_me_with_bad_token_returns_401(self, client):
        res = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
        assert res.status_code == 401
