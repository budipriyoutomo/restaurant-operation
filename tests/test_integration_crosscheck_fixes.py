"""Integration tests — frontend/backend cross-check fixes (2026-10-03):
analytics outlet scoping, the budgets permission on budget status, and enum
fields rejected as 422 instead of surfacing as a DB 500."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_ana@test.test", "admin")


@pytest.fixture()
def mgr_jakarta(client, db):
    from app.models.outlet import Outlet
    jkt = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
    return seed_user_headers(db, "mgr_ana_jkt@test.test", "manager", outlets=[jkt])


def _asset(client, admin, name, outlet):
    return client.post("/api/assets", json={
        "name": name, "category": "HVAC", "outlet": outlet, "status": "operational",
    }, headers=admin).json()


def _issue(client, admin, outlet):
    return client.post("/api/issues", json={
        "title": f"Issue {outlet}", "outlet": outlet, "category": "Maintenance", "priority": "low",
    }, headers=admin)


class TestCMMSAnalyticsScoping:
    def test_manager_sees_only_own_outlet_assets(self, client, admin, mgr_jakarta):
        _asset(client, admin, "Chiller JKT", "Jakarta")
        _asset(client, admin, "Chiller BDG", "Bandung")
        res = client.get("/api/analytics/cmms", headers=mgr_jakarta)
        assert res.status_code == 200
        names = [a["assetName"] for a in res.json()["perAsset"]]
        assert "Chiller JKT" in names
        assert "Chiller BDG" not in names
        assert res.json()["fleet"]["assetCount"] == len(names)

    def test_admin_sees_all_outlets(self, client, admin):
        _asset(client, admin, "Chiller JKT", "Jakarta")
        _asset(client, admin, "Chiller BDG", "Bandung")
        names = [a["assetName"] for a in client.get("/api/analytics/cmms", headers=admin).json()["perAsset"]]
        assert {"Chiller JKT", "Chiller BDG"} <= set(names)


class TestSummaryScoping:
    def test_manager_summary_excludes_other_outlet(self, client, admin, mgr_jakarta):
        assert _issue(client, admin, "Jakarta").status_code == 201
        assert _issue(client, admin, "Bandung").status_code == 201
        body = client.get("/api/analytics/summary", headers=mgr_jakarta).json()
        assert body["issues"]["by_outlet"] == {"Jakarta": 1}
        assert body["issues"]["total"] == 1
        assert body["tasks"]["total"] == 1      # generateTask defaults to True

    def test_admin_summary_counts_everything(self, client, admin):
        _issue(client, admin, "Jakarta")
        _issue(client, admin, "Bandung")
        assert client.get("/api/analytics/summary", headers=admin).json()["issues"]["total"] == 2


class TestBudgetStatusPermission:
    def test_budgets_view_without_analytics_can_read_status(self, client, db):
        # Default staff role: budgets=view, analytics=none. The Procurement →
        # Anggaran tab is gated by `budgets`, so the status endpoint must accept it.
        staff = seed_user_headers(db, "staff_ana@test.test", "staff")
        assert client.get("/api/analytics/budget?period=2026-10", headers=staff).status_code == 200


class TestIssueEnumValidation:
    @pytest.mark.parametrize("field,value", [("category", "maintenance"), ("priority", "urgent")])
    def test_invalid_enum_is_422_not_500(self, client, admin, field, value):
        body = {"title": "X", "outlet": "Jakarta", "category": "Maintenance", "priority": "low", field: value}
        res = client.post("/api/issues", json=body, headers=admin)
        assert res.status_code == 422


class TestStatusEnumValidation:
    @pytest.fixture()
    def issue(self, client, admin):
        return _issue(client, admin, "Jakarta").json()

    @pytest.mark.parametrize("body", [{"status": "done"}, {"priority": "urgent"}])
    def test_invalid_task_update_is_422(self, client, admin, issue, body):
        res = client.patch(f"/api/tasks/{issue['taskIds'][0]}", json=body, headers=admin)
        assert res.status_code == 422

    def test_invalid_issue_status_is_422(self, client, admin, issue):
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "done"}, headers=admin)
        assert res.status_code == 422

    def test_valid_task_update_still_works(self, client, admin, issue):
        res = client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "in-progress"}, headers=admin)
        assert res.status_code == 200
