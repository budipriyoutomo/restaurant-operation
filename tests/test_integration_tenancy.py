"""Integration tests — tenant isolation through the API (Todo-Pilot §11).

Written before the implementation (TDD). "Alpha" is the suite's default test
company (conftest.test_company); "Beta" is a second customer. Every request
must run as the caller's own company, whatever the session held before.
"""

import uuid

import pytest

from app.core.tenancy import bypass_tenant, set_tenant, tenant
from app.models.company import Company
from tests.conftest import seed_user_headers

PASSWORD = "Pass1234!"


@pytest.fixture()
def alpha(client, db, test_company):
    return {
        "company": test_company,
        "admin": seed_user_headers(db, "admin@alpha.test", "admin", name="Alpha Admin"),
        "manager": seed_user_headers(db, "manager@alpha.test", "manager", name="Alpha Manager"),
    }


@pytest.fixture()
def beta(client, db, test_company, alpha):
    from app.models.outlet import Outlet
    from app.services.role_service import ensure_default_roles
    with bypass_tenant(db):
        company = Company(name="PT Beta", slug=f"beta-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        outlet = Outlet(name="Beta Dago", code="BDG", status="operational")
        db.add(outlet)
        db.flush()
        headers = {
            "admin": seed_user_headers(db, "admin@beta.test", "admin", name="Beta Admin"),
            "manager": seed_user_headers(db, "manager@beta.test", "manager", name="Beta Manager",
                                         outlets=[outlet]),
        }
    return {"company": company, **headers}


def _issue(client, headers, outlet, **over):
    body = {"title": "Kompor rusak", "description": "x", "category": "Other", "priority": "high",
            "outlet": outlet, "assignee": "Unassigned", "generateTask": True}
    body.update(over)
    res = client.post("/api/issues", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _ids(res):
    assert res.status_code == 200, res.text
    return {row["id"] for row in res.json()}


class TestRequestRunsAsCallersCompany:
    def test_each_company_sees_only_its_own_issues(self, client, db, alpha, beta):
        a = _issue(client, alpha["admin"], "Jakarta")
        b = _issue(client, beta["admin"], "Beta Dago")
        set_tenant(db, alpha["company"].id)                      # stale context must not matter
        assert _ids(client.get("/api/issues", headers=beta["admin"])) == {b["id"]}
        set_tenant(db, beta["company"].id)
        assert _ids(client.get("/api/issues", headers=alpha["admin"])) == {a["id"]}

    def test_another_companys_record_is_404(self, client, alpha, beta):
        a = _issue(client, alpha["admin"], "Jakarta")
        assert client.get(f"/api/issues/{a['id']}", headers=beta["admin"]).status_code == 404
        assert client.patch(f"/api/issues/{a['id']}", json={"title": "x"}, headers=beta["admin"]).status_code == 404
        assert client.post(f"/api/issues/{a['id']}/cancel", json={"reason": "x"}, headers=beta["admin"]).status_code == 404
        assert client.patch(f"/api/tasks/{a['taskIds'][0]}", json={"status": "resolved"}, headers=beta["admin"]).status_code == 404

    def test_document_numbers_start_at_one_per_company(self, client, alpha, beta):
        a = _issue(client, alpha["admin"], "Jakarta")
        _issue(client, alpha["admin"], "Jakarta")
        b = _issue(client, beta["admin"], "Beta Dago")
        assert b["number"] == a["number"]                       # ISS-<year>-00001 in both

    def test_an_outlet_of_another_company_does_not_exist(self, client, alpha, beta):
        res = client.post("/api/issues", json={"title": "x", "description": "x", "category": "Other",
                                               "priority": "low", "outlet": "Jakarta"}, headers=beta["admin"])
        assert res.status_code == 422

    def test_master_data_is_per_company(self, client, alpha, beta):
        outlets = client.get("/api/outlets", headers=beta["admin"]).json()
        assert [o["name"] for o in outlets] == ["Beta Dago"]
        users = {u["email"] for u in client.get("/api/auth/users", headers=beta["admin"]).json()}
        assert users == {"admin@beta.test", "manager@beta.test"}
        roles = client.get("/api/roles", headers=beta["admin"]).json()
        assert sorted(r["key"] for r in roles) == ["admin", "manager", "staff"]

    def test_audit_log_and_notifications_stay_inside(self, client, alpha, beta):
        a = _issue(client, alpha["admin"], "Jakarta", category="Procurement", generateApproval=True,
                   approvalAmount=5_000_000)
        logs = client.get("/api/audit-logs", headers=beta["admin"]).json()
        assert all(l["record_id"] != a["id"] for l in logs)
        assert client.get("/api/notifications", headers=beta["manager"]).json() == []
        assert client.get("/api/notifications", headers=alpha["manager"]).json() != []

    def test_me_names_the_company(self, client, alpha, beta):
        me = client.get("/api/auth/me", headers=beta["admin"]).json()
        assert me["company_id"] == str(beta["company"].id)
        assert me["company_name"] == "PT Beta"
        assert me["is_platform_admin"] is False


class TestAccounts:
    def test_users_join_the_creators_company(self, client, alpha, beta):
        res = client.post("/api/auth/register", json={"email": "kasir@beta.test", "name": "Kasir",
                                                      "password": PASSWORD, "role": "staff"}, headers=beta["admin"])
        assert res.status_code == 201, res.text
        assert "kasir@beta.test" in {u["email"] for u in client.get("/api/auth/users", headers=beta["admin"]).json()}
        assert "kasir@beta.test" not in {u["email"] for u in client.get("/api/auth/users", headers=alpha["admin"]).json()}

    def test_email_is_unique_across_companies(self, client, alpha, beta):
        res = client.post("/api/auth/register", json={"email": "manager@alpha.test", "name": "Dup",
                                                      "password": PASSWORD, "role": "staff"}, headers=beta["admin"])
        assert res.status_code == 409

    def test_users_of_another_company_cannot_be_edited(self, client, db, alpha, beta):
        alpha_users = client.get("/api/auth/users", headers=alpha["admin"]).json()
        target = next(u for u in alpha_users if u["email"] == "manager@alpha.test")
        res = client.patch(f"/api/auth/users/{target['id']}", json={"name": "hacked"}, headers=beta["admin"])
        assert res.status_code == 404

    def test_login_works_and_inactive_company_is_locked_out(self, client, db, alpha, beta):
        ok = client.post("/api/auth/login", json={"email": "admin@beta.test", "password": PASSWORD})
        assert ok.status_code == 200
        token = {"Authorization": f"Bearer {ok.json()['access_token']}"}
        assert client.get("/api/auth/me", headers=token).status_code == 200

        with bypass_tenant(db):
            beta["company"].is_active = False
            db.flush()
        res = client.post("/api/auth/login", json={"email": "admin@beta.test", "password": PASSWORD})
        assert res.status_code == 403
        assert client.get("/api/auth/me", headers=token).status_code == 401
        assert client.get("/api/issues", headers=alpha["admin"]).status_code == 200   # others unaffected


class TestPlatformAdmin:
    def test_platform_admin_cannot_read_company_data(self, client, db, alpha):
        from app.models.user import User
        from app.services.auth_service import create_access_token, hash_password
        with bypass_tenant(db):
            op = User(email="ops@platform.test", name="Ops", password_hash=hash_password(PASSWORD),
                      role="staff", company_id=None, is_platform_admin=True)
            db.add(op)
            db.flush()
        headers = {"Authorization": f"Bearer {create_access_token(str(op.id), op.email, op.role)}"}
        assert client.get("/api/auth/me", headers=headers).json()["is_platform_admin"] is True
        assert client.get("/api/issues", headers=headers).status_code == 403
        assert client.get("/api/outlets", headers=headers).status_code == 403


class TestBackgroundJobs:
    """Cron jobs open a session with no user: they must work through every active
    company, each in its own context, and never mix companies' data."""

    def _stale_approval(self, client, headers, outlet, title):
        issue = _issue(client, headers, outlet, title=title, category="Maintenance", generateTask=False,
                       generateWorkOrder=True, estimatedCost=3_000_000)
        return issue["approvalId"]

    def _escalations(self, client, headers):
        return [n for n in client.get("/api/notifications?limit=100", headers=headers).json()
                if "macet" in n["title"].lower()]

    def test_escalator_covers_every_company_and_notifies_inside_each(self, client, db, alpha, beta):
        from scripts.run_escalate_stale import run
        a = self._stale_approval(client, alpha["admin"], "Jakarta", "Chiller Alpha")
        b = self._stale_approval(client, beta["admin"], "Beta Dago", "Chiller Beta")
        set_tenant(db, None)                                   # a cron session has no company
        escalated = {str(x.id) for x in run(db, threshold_days=0)}
        assert {a, b} <= escalated
        assert len(self._escalations(client, beta["admin"])) == 1
        assert len(self._escalations(client, alpha["admin"])) == 1

    def test_inactive_companies_are_skipped(self, client, db, alpha, beta):
        from scripts.run_escalate_stale import run
        b = self._stale_approval(client, beta["admin"], "Beta Dago", "Chiller Beta")
        with bypass_tenant(db):
            beta["company"].is_active = False
            db.flush()
        set_tenant(db, None)
        assert b not in {str(x.id) for x in run(db, threshold_days=0)}

    def test_pm_generator_and_whatsapp_delivery_run_without_a_company(self, client, db, alpha, beta):
        from app.services.whatsapp_service import process_due
        from scripts.run_pm_generator import run as run_pm
        set_tenant(db, None)
        assert run_pm(db) == []
        assert process_due(db)["sent"] == 0
