"""Integration tests — campaigns: integer budget, spend, results (Todo-Pilot §10).

Written before the implementation (TDD). The campaigns API is snake_case.

  POST/PATCH/DELETE /api/campaigns[/{id}]     budget = integer + currency
  PATCH /api/campaigns/{id}/results           actual_cost, transactions, revenue (+ baseline)
  GET   /api/campaigns/summary                totals per currency, over-budget count
"""

import pytest

from tests.conftest import TEST_OUTLETS, seed_user_headers

OTHER = next(n for n, _ in TEST_OUTLETS if n != "Jakarta")


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_mkt@test.test", "admin", name="Admin Mkt")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_mkt@test.test", "staff")


def _campaign(client, headers, **over):
    body = {"title": "Promo Ramadan", "type": "promotion", "outlet": "Jakarta", "budget": 10_000_000,
            "start_date": "2026-09-01", "end_date": "2026-09-30"}
    body.update(over)
    res = client.post("/api/campaigns", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _results(client, headers, cid, **body):
    return client.patch(f"/api/campaigns/{cid}/results", json=body, headers=headers)


class TestBudget:
    def test_budget_is_an_integer_with_currency(self, client, admin):
        c = _campaign(client, admin)
        assert (c["budget"], c["currency"], c["budget_legacy"]) == (10_000_000, "IDR", None)
        assert c["actual_cost"] is None and c["metrics"]["over_budget"] is False

    def test_text_budget_is_rejected(self, client, admin):
        res = client.post("/api/campaigns", json={"title": "x", "budget": "Rp 5.000.000"}, headers=admin)
        assert res.status_code == 422

    def test_negative_budget_and_bad_currency(self, client, admin):
        assert client.post("/api/campaigns", json={"title": "x", "budget": -1}, headers=admin).status_code == 422
        assert client.post("/api/campaigns", json={"title": "x", "currency": "RUPIAH"}, headers=admin).status_code == 422

    def test_end_before_start_is_422(self, client, admin):
        res = client.post("/api/campaigns", json={"title": "x", "start_date": "2026-09-10", "end_date": "2026-09-01"},
                          headers=admin)
        assert res.status_code == 422
        c = _campaign(client, admin)
        assert client.patch(f"/api/campaigns/{c['id']}", json={"end_date": "2026-08-01"}, headers=admin).status_code == 422

    def test_patch_budget(self, client, admin):
        c = _campaign(client, admin)
        body = client.patch(f"/api/campaigns/{c['id']}", json={"budget": 7_500_000}, headers=admin).json()
        assert body["budget"] == 7_500_000


class TestResults:
    def test_spend_and_results_give_metrics(self, client, admin):
        c = _campaign(client, admin)
        client.patch(f"/api/campaigns/{c['id']}", json={"status": "active"}, headers=admin)
        res = _results(client, admin, c["id"], actual_cost=12_000_000, result_transactions=400,
                       result_revenue=96_000_000, baseline_transactions=320, baseline_revenue=80_000_000)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["actual_cost"] == 12_000_000
        m = body["metrics"]
        assert m["budget_used_pct"] == 120.0 and m["over_budget"] is True
        assert m["cost_per_transaction"] == 30_000
        assert m["revenue_per_rupiah"] == 8.0
        assert m["revenue_uplift_pct"] == 20.0
        assert m["transaction_uplift_pct"] == 25.0

    def test_results_need_an_active_campaign(self, client, admin):
        c = _campaign(client, admin)                                   # draft
        assert _results(client, admin, c["id"], result_revenue=1_000_000).status_code == 409
        assert _results(client, admin, c["id"], actual_cost=500_000).status_code == 200

    def test_partial_update_keeps_other_fields(self, client, admin):
        c = _campaign(client, admin, status=None)
        client.patch(f"/api/campaigns/{c['id']}", json={"status": "completed"}, headers=admin)
        _results(client, admin, c["id"], actual_cost=1_000, result_transactions=10)
        body = _results(client, admin, c["id"], result_revenue=50_000).json()
        assert (body["actual_cost"], body["result_transactions"], body["result_revenue"]) == (1_000, 10, 50_000)

    def test_negative_values_rejected(self, client, admin):
        c = _campaign(client, admin)
        assert _results(client, admin, c["id"], actual_cost=-5).status_code == 422

    def test_staff_cannot_record(self, client, admin, staff):
        c = _campaign(client, admin)
        assert _results(client, staff, c["id"], actual_cost=1).status_code == 403

    def test_results_are_audited(self, client, admin):
        c = _campaign(client, admin)
        _results(client, admin, c["id"], actual_cost=2_000_000)
        logs = client.get(f"/api/audit-logs?table_name=campaigns&record_id={c['id']}", headers=admin).json()
        assert any(l["action"] == "results" and (l["new_value"] or {}).get("actual_cost") == 2_000_000 for l in logs)


class TestScopingAndSummary:
    def _jkt_manager(self, db):
        from app.models.outlet import Outlet
        o = db.query(Outlet).filter(Outlet.name == "Jakarta").one()
        return seed_user_headers(db, "mgr_jkt_mkt@test.test", "manager", outlets=[o])

    def test_outlet_scoping(self, client, db, admin):
        theirs = _campaign(client, admin, outlet=OTHER)
        mgr = self._jkt_manager(db)
        assert all(c["id"] != theirs["id"] for c in client.get("/api/campaigns", headers=mgr).json())
        assert client.patch(f"/api/campaigns/{theirs['id']}", json={"title": "x"}, headers=mgr).status_code == 404
        assert client.delete(f"/api/campaigns/{theirs['id']}", headers=mgr).status_code == 404
        assert client.post("/api/campaigns", json={"title": "x", "outlet": OTHER}, headers=mgr).status_code == 403

    def test_moving_outlet_updates_scope(self, client, db, admin):
        c = _campaign(client, admin)
        client.patch(f"/api/campaigns/{c['id']}", json={"outlet": OTHER}, headers=admin)
        mgr = self._jkt_manager(db)
        assert all(x["id"] != c["id"] for x in client.get("/api/campaigns", headers=mgr).json())

    def test_summary(self, client, db, admin):
        a = _campaign(client, admin, budget=10_000_000)
        b = _campaign(client, admin, budget=5_000_000)
        _campaign(client, admin, budget=1_000, currency="MYR")
        for cid in (a["id"], b["id"]):
            client.patch(f"/api/campaigns/{cid}", json={"status": "active"}, headers=admin)
        _results(client, admin, a["id"], actual_cost=12_000_000, result_revenue=60_000_000)
        _results(client, admin, b["id"], actual_cost=3_000_000)
        res = client.get("/api/campaigns/summary", headers=admin)
        assert res.status_code == 200, res.text
        s = res.json()
        assert s["campaigns"] == 3
        assert s["over_budget"] == 1
        assert s["by_currency"]["IDR"] == {"budget": 15_000_000, "actual_cost": 15_000_000, "revenue": 60_000_000}
        assert s["by_currency"]["MYR"]["budget"] == 1_000

    def test_summary_respects_scope(self, client, db, admin):
        _campaign(client, admin, outlet=OTHER)
        assert client.get("/api/campaigns/summary", headers=self._jkt_manager(db)).json()["campaigns"] == 0
