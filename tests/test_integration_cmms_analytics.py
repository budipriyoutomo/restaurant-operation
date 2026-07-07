"""Integration tests — GET /api/analytics/cmms (Tier 3)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_an@test.test", "admin")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_an@test.test", "staff")


def _make_asset(client, admin, name, purchase_cost=None):
    body = {"name": name, "category": "HVAC", "outlet": "Jakarta", "status": "operational"}
    if purchase_cost is not None:
        body["purchaseCost"] = purchase_cost
    return client.post("/api/assets", json=body, headers=admin).json()


def _make_corrective_wo(client, admin, asset_id, labor=0, parts=0):
    wo = client.post("/api/work-orders", json={
        "assetId": asset_id, "type": "corrective", "title": "Perbaikan", "priority": "high",
    }, headers=admin).json()
    if labor or parts:
        client.patch(f"/api/work-orders/{wo['id']}/cost",
                     json={"laborCost": labor, "partsCost": parts}, headers=admin)
    return wo


def _find(resp, asset_id):
    return next(a for a in resp["perAsset"] if a["assetId"] == asset_id)


class TestCMMSAnalytics:
    def test_costs_and_failures_counted(self, client, admin):
        asset = _make_asset(client, admin, "Chiller", purchase_cost=1_000_000)
        _make_corrective_wo(client, admin, asset["id"], labor=600_000, parts=0)

        res = client.get("/api/analytics/cmms", headers=admin)
        assert res.status_code == 200, res.text
        a = _find(res.json(), asset["id"])
        assert a["workOrders"] == 1
        assert a["failures"] == 1
        assert a["totalCost"] == 600_000
        assert a["repairCost"] == 600_000
        # No downtime recorded → asset counts as fully up
        assert a["uptimePct"] == 100.0

    def test_repair_vs_replace_flag(self, client, admin):
        # repair 600k >= 0.5 * 1jt → replace candidate
        replace_it = _make_asset(client, admin, "Old Genset", purchase_cost=1_000_000)
        _make_corrective_wo(client, admin, replace_it["id"], labor=600_000)
        # repair 100k < 0.5 * 1jt → keep repairing
        keep_it = _make_asset(client, admin, "New Genset", purchase_cost=1_000_000)
        _make_corrective_wo(client, admin, keep_it["id"], labor=100_000)
        # unknown purchase cost → None
        unknown = _make_asset(client, admin, "Mystery", purchase_cost=None)
        _make_corrective_wo(client, admin, unknown["id"], labor=900_000)

        res = client.get("/api/analytics/cmms", headers=admin).json()
        assert _find(res, replace_it["id"])["repairVsReplace"] is True
        assert _find(res, keep_it["id"])["repairVsReplace"] is False
        assert _find(res, unknown["id"])["repairVsReplace"] is None
        assert res["fleet"]["replaceCandidates"] >= 1

    def test_outlet_filter(self, client, admin):
        a_jkt = _make_asset(client, admin, "JKT Unit")
        client.post("/api/assets", json={
            "name": "BDG Unit", "category": "HVAC", "outlet": "Bandung", "status": "operational",
        }, headers=admin)

        res = client.get("/api/analytics/cmms?outlet=Jakarta", headers=admin).json()
        outlets = {a["outlet"] for a in res["perAsset"]}
        assert outlets == {"Jakarta"}
        assert any(a["assetId"] == a_jkt["id"] for a in res["perAsset"])

    def test_requires_manager_or_admin(self, client, staff):
        res = client.get("/api/analytics/cmms", headers=staff)
        assert res.status_code == 403
