"""Integration — home page per role (Todo-Pilot §19 / UX-6).

A role may set the page its users land on (`home_page`); none = "Pekerjaan
Saya". The admin role lands on the executive dashboard.
"""

import pytest

from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_home@test.test", "admin")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_home@test.test", "staff")


def test_default_roles(client, admin, staff):
    roles = {r["key"]: r for r in client.get("/api/roles", headers=admin).json()}
    assert roles["admin"]["home_page"] == "dashboard"
    assert roles["staff"]["home_page"] is None
    assert client.get("/api/auth/me", headers=staff).json()["home_page"] is None
    assert client.get("/api/auth/me", headers=admin).json()["home_page"] == "dashboard"


def test_set_a_home_page_on_a_role(client, admin, staff):
    res = client.patch("/api/roles/staff", json={"home_page": "issues"}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["home_page"] == "issues"
    assert client.get("/api/auth/me", headers=staff).json()["home_page"] == "issues"
    res = client.patch("/api/roles/staff", json={"home_page": "my-work"}, headers=admin)
    assert res.json()["home_page"] is None          # "my-work" = back to the default


def test_unknown_page_is_422(client, admin):
    assert client.patch("/api/roles/staff", json={"home_page": "nowhere"}, headers=admin).status_code == 422


def test_home_must_be_a_page_the_role_can_open(client, admin):
    res = client.post("/api/roles", json={
        "key": "kitchen", "name": "Kitchen", "permissions": {"issues": "view"}, "home_page": "cmms",
    }, headers=admin)
    assert res.status_code == 422
    res = client.post("/api/roles", json={
        "key": "kitchen", "name": "Kitchen", "permissions": {"issues": "view"}, "home_page": "issues",
    }, headers=admin)
    assert res.status_code == 201, res.text
    assert res.json()["home_page"] == "issues"


def test_home_page_is_not_an_access_change(client, db, admin):
    """Setting a home page is cosmetic: a manager with users:manage may set it on
    their own role (unlike permissions)."""
    mgr = seed_user_headers(db, "mgr_home@test.test", "manager")
    client.patch("/api/roles/manager", json={"permissions": {"users": "manage", "issues": "manage"}}, headers=admin)
    res = client.patch("/api/roles/manager", json={"home_page": "issues"}, headers=mgr)
    assert res.status_code == 200, res.text
