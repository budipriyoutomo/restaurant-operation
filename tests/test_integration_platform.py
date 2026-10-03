"""Integration tests — platform admin: managing companies (Todo-Pilot §11).

Written before the implementation (TDD). The SaaS operator creates and
deactivates customer companies; they never see a company's business data.

  GET   /api/platform/companies          list with user/outlet counts
  POST  /api/platform/companies          company + default roles + first outlet + first admin
  PATCH /api/platform/companies/{id}     rename, activate / deactivate
"""

import pytest

from app.core.tenancy import bypass_tenant
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import seed_user_headers


@pytest.fixture()
def platform(client, db):
    with bypass_tenant(db):
        op = User(email="ops@platform.test", name="Ops", password_hash=hash_password("Pass1234!"),
                  role="staff", company_id=None, is_platform_admin=True)
        db.add(op)
        db.flush()
    return {"Authorization": f"Bearer {create_access_token(str(op.id), op.email, op.role)}"}


@pytest.fixture()
def company_admin(client, db):
    return seed_user_headers(db, "admin@alpha.test", "admin")


NEW = {"name": "PT Sate Senayan", "admin_name": "Rina", "admin_email": "rina@satesenayan.test",
       "outlet_name": "Senayan City", "outlet_code": "SNC"}


def _create(client, headers, **over):
    return client.post("/api/platform/companies", json={**NEW, **over}, headers=headers)


class TestCreate:
    def test_creates_a_ready_to_use_company(self, client, platform):
        res = _create(client, platform)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["company"]["name"] == "PT Sate Senayan"
        assert body["company"]["slug"] == "pt-sate-senayan"
        assert body["company"]["is_active"] is True
        assert len(body["admin_password"]) >= 12              # generated, shown once

        login = client.post("/api/auth/login", json={"email": NEW["admin_email"], "password": body["admin_password"]})
        assert login.status_code == 200
        token = {"Authorization": f"Bearer {login.json()['access_token']}"}
        me = client.get("/api/auth/me", headers=token).json()
        assert me["company_name"] == "PT Sate Senayan" and me["role"] == "admin"
        assert [o["name"] for o in client.get("/api/outlets", headers=token).json()] == ["Senayan City"]
        assert sorted(r["key"] for r in client.get("/api/roles", headers=token).json()) == ["admin", "manager", "staff"]
        assert client.get("/api/issues", headers=token).json() == []      # empty, not someone else's

    def test_a_chosen_password_is_used_and_not_echoed(self, client, platform):
        body = _create(client, platform, admin_password="Rahasia-Sekali-1").json()
        assert body["admin_password"] is None
        assert client.post("/api/auth/login", json={"email": NEW["admin_email"],
                                                    "password": "Rahasia-Sekali-1"}).status_code == 200

    def test_duplicate_slug_or_admin_email_is_409(self, client, platform, company_admin):
        assert _create(client, platform).status_code == 201
        assert _create(client, platform, admin_email="other@x.test").status_code == 409          # same name → slug
        assert _create(client, platform, name="PT Lain", admin_email="admin@alpha.test").status_code == 409

    def test_invalid_input_is_422(self, client, platform):
        assert _create(client, platform, name="").status_code == 422
        assert _create(client, platform, admin_email="not-an-email").status_code == 422
        assert _create(client, platform, admin_password="short").status_code == 422

    def test_nothing_is_left_behind_when_creation_fails(self, client, db, platform, company_admin):
        assert _create(client, platform, admin_email="admin@alpha.test").status_code == 409
        names = [c["name"] for c in client.get("/api/platform/companies", headers=platform).json()]
        assert "PT Sate Senayan" not in names


class TestListAndUpdate:
    def test_list_shows_every_company_with_counts(self, client, platform, company_admin):
        _create(client, platform)
        rows = {c["name"]: c for c in client.get("/api/platform/companies", headers=platform).json()}
        assert rows["PT Sate Senayan"]["user_count"] == 1
        assert rows["PT Sate Senayan"]["outlet_count"] == 1
        assert rows["Test Company"]["user_count"] >= 1

    def test_rename_and_deactivate(self, client, platform):
        company = _create(client, platform).json()["company"]
        res = client.patch(f"/api/platform/companies/{company['id']}", json={"name": "PT Sate Senayan Group"},
                           headers=platform)
        assert res.status_code == 200 and res.json()["name"] == "PT Sate Senayan Group"
        assert res.json()["slug"] == "pt-sate-senayan"                     # slug is stable

        res = client.patch(f"/api/platform/companies/{company['id']}", json={"is_active": False}, headers=platform)
        assert res.json()["is_active"] is False
        assert client.post("/api/auth/login", json={"email": NEW["admin_email"], "password": "x"}).status_code in (401, 403)

    def test_unknown_company_is_404(self, client, platform):
        res = client.patch("/api/platform/companies/00000000-0000-0000-0000-000000000000", json={"name": "x"},
                           headers=platform)
        assert res.status_code == 404


class TestAccess:
    def test_company_admins_cannot_use_platform_endpoints(self, client, company_admin):
        assert client.get("/api/platform/companies", headers=company_admin).status_code == 403
        assert _create(client, company_admin).status_code == 403

    def test_anonymous_is_401(self, client):
        assert client.get("/api/platform/companies").status_code == 401

    def test_platform_admin_still_cannot_read_company_data(self, client, platform):
        _create(client, platform)
        assert client.get("/api/issues", headers=platform).status_code == 403
        assert client.get("/api/auth/users", headers=platform).status_code == 403


class TestBootstrap:
    def test_create_platform_admin_makes_a_companyless_operator(self, client, db):
        from app.services.platform_service import create_platform_admin
        user, password = create_platform_admin(db, "founder@platform.test", "Founder")
        assert user.company_id is None and user.is_platform_admin is True and len(password) >= 12
        token = client.post("/api/auth/login", json={"email": "founder@platform.test", "password": password})
        assert token.status_code == 200
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
        assert client.get("/api/platform/companies", headers=headers).status_code == 200

    def test_existing_email_is_refused(self, client, db):
        from app.services.platform_service import create_platform_admin
        seed_user_headers(db, "taken@alpha.test", "admin")
        with pytest.raises(ValueError):
            create_platform_admin(db, "taken@alpha.test", "X")
