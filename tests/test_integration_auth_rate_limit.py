"""Integration tests — per-IP rate limit on the public auth endpoints.

Written before the implementation (TDD). The suite runs with the limiter off
(conftest); these tests switch it on. Limits come from settings:

  POST /api/auth/login             RATE_LIMIT_LOGIN
  POST /api/auth/forgot-password   RATE_LIMIT_FORGOT_PASSWORD
  POST /api/auth/reset-password    RATE_LIMIT_RESET_PASSWORD
"""

import pytest
from starlette.testclient import TestClient

from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture()
def limited():
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


def _post(client, path, body, ip="203.0.113.7"):
    # `client` installs the test-DB override; this one only changes the peer
    # address (in production uvicorn sets it from the proxy's X-Forwarded-For).
    return TestClient(app, client=(ip, 50000)).post(path, json=body)


LOGIN = ("/api/auth/login", {"email": "nobody@test.test", "password": "wrong"})


def test_login_is_limited_with_a_clear_message(client, limited):
    codes = [_post(client, *LOGIN).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10
    res = _post(client, *LOGIN)
    assert res.status_code == 429
    assert "Too many attempts" in res.json()["detail"]
    assert res.headers.get("Retry-After")


def test_forgot_password_is_limited(client, limited):
    body = {"email": "nobody@test.test"}
    codes = [_post(client, "/api/auth/forgot-password", body).status_code for _ in range(6)]
    assert codes[:5] == [202] * 5 and codes[5] == 429


def test_reset_password_is_limited(client, limited):
    body = {"token": "nope", "new_password": "BrandNewPass99"}
    codes = [_post(client, "/api/auth/reset-password", body).status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429


def test_limits_are_per_client_ip(client, limited):
    for _ in range(10):
        _post(client, *LOGIN, ip="203.0.113.7")
    assert _post(client, *LOGIN, ip="203.0.113.7").status_code == 429
    assert _post(client, *LOGIN, ip="198.51.100.9").status_code == 401


def test_signed_in_endpoints_are_not_affected(client, db, limited):
    from tests.conftest import seed_user_headers
    headers = seed_user_headers(db, "busy@test.test", "staff")
    assert all(client.get("/api/auth/me", headers=headers).status_code == 200 for _ in range(30))
