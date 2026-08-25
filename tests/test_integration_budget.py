"""Integration tests — budget vs spend per outlet (Tier 6.3)."""

from datetime import date

import pytest
from tests.conftest import seed_user_headers

PERIOD = date.today().strftime("%Y-%m")   # WO completed_date uses today's month


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_budget@test.test", "admin")


@pytest.fixture()
def jakarta_id(db):
    from app.models.outlet import Outlet
    return str(db.query(Outlet).filter(Outlet.name == "Jakarta").first().id)


def _budget(client, admin, outlet_id, amount, period=PERIOD):
    return client.post("/api/budgets", json={"outletId": outlet_id, "period": period, "amount": amount}, headers=admin)


def _completed_wo_with_cost(client, admin, labor, parts):
    asset = client.post("/api/assets", json={
        "name": "Aset Budget", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    wo = client.post("/api/work-orders", json={
        "assetId": asset["id"], "type": "corrective", "title": "Servis", "priority": "high",
    }, headers=admin).json()
    client.patch(f"/api/work-orders/{wo['id']}/cost", json={"laborCost": labor, "partsCost": parts}, headers=admin)
    client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": "in-progress"}, headers=admin)
    client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": "completed"}, headers=admin)
    return wo


class TestBudgetCrud:
    def test_create_and_duplicate(self, client, admin, jakarta_id):
        assert _budget(client, admin, jakarta_id, 5_000_000).status_code == 201
        assert _budget(client, admin, jakarta_id, 6_000_000).status_code == 409   # same outlet+period

    def test_unknown_outlet_rejected(self, client, admin):
        res = _budget(client, admin, "00000000-0000-0000-0000-000000000000", 1_000_000)
        assert res.status_code == 422


class TestBudgetStatus:
    def test_spend_counts_completed_wo(self, client, admin, jakarta_id):
        _budget(client, admin, jakarta_id, 1_000_000)
        _completed_wo_with_cost(client, admin, labor=300_000, parts=100_000)

        status = client.get(f"/api/analytics/budget?period={PERIOD}", headers=admin).json()
        jkt = next(s for s in status if s["outletId"] == jakarta_id)
        assert jkt["spentWorkOrders"] == 400_000
        assert jkt["spent"] == 400_000
        assert jkt["remaining"] == 600_000
        assert jkt["pct"] == 40.0
        assert jkt["warning"] is False

    def test_warning_when_over_80_percent(self, client, admin, jakarta_id):
        _budget(client, admin, jakarta_id, 1_000_000)
        _completed_wo_with_cost(client, admin, labor=850_000, parts=0)
        status = client.get(f"/api/analytics/budget?period={PERIOD}", headers=admin).json()
        jkt = next(s for s in status if s["outletId"] == jakarta_id)
        assert jkt["warning"] is True

    def test_scoping_hides_other_outlet_budget(self, client, db, admin, jakarta_id):
        from app.models.outlet import Outlet
        bdg = db.query(Outlet).filter(Outlet.name == "Bandung").first()
        _budget(client, admin, jakarta_id, 1_000_000)
        _budget(client, admin, str(bdg.id), 2_000_000)

        mgr_bdg = seed_user_headers(db, "mgr_bud_bdg@test.test", "manager", outlets=[bdg])
        status = client.get(f"/api/analytics/budget?period={PERIOD}", headers=mgr_bdg).json()
        outlets = {s["outletId"] for s in status}
        assert str(bdg.id) in outlets
        assert jakarta_id not in outlets       # Jakarta budget invisible to Bandung manager
