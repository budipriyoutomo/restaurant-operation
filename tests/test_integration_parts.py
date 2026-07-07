"""Integration tests — spare parts inventory + WO consumption (Tier 3)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_parts@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Pcompressor", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _make_part(client, admin, sku="FLT-01", stock=10, unit_cost=50_000, reorder=2):
    return client.post("/api/parts", json={
        "sku": sku, "name": "Filter Udara", "category": "HVAC",
        "unitCost": unit_cost, "stockQty": stock, "reorderLevel": reorder,
    }, headers=admin).json()


def _make_wo(client, admin, asset_id):
    return client.post("/api/work-orders", json={
        "assetId": asset_id, "type": "corrective", "title": "Ganti filter", "priority": "medium",
    }, headers=admin).json()


class TestConsumption:
    def test_consume_decrements_stock_and_sets_parts_cost(self, client, admin, asset_id):
        part = _make_part(client, admin, stock=10, unit_cost=50_000)
        wo = _make_wo(client, admin, asset_id)

        r = client.post(f"/api/work-orders/{wo['id']}/parts",
                        json={"partId": part["id"], "quantity": 3}, headers=admin)
        assert r.status_code == 201, r.text
        assert r.json()["lineCost"] == 150_000

        # stock decremented
        p = client.get(f"/api/parts/{part['id']}", headers=admin).json()
        assert p["stockQty"] == 7

        # WO parts_cost reflects consumption
        wo_after = client.get(f"/api/work-orders/{wo['id']}", headers=admin).json()
        assert wo_after["partsCost"] == 150_000
        assert wo_after["totalCost"] == 150_000

    def test_multiple_consumptions_accumulate(self, client, admin, asset_id):
        part = _make_part(client, admin, stock=10, unit_cost=10_000)
        wo = _make_wo(client, admin, asset_id)
        client.post(f"/api/work-orders/{wo['id']}/parts", json={"partId": part["id"], "quantity": 2}, headers=admin)
        client.post(f"/api/work-orders/{wo['id']}/parts", json={"partId": part["id"], "quantity": 3}, headers=admin)

        wo_after = client.get(f"/api/work-orders/{wo['id']}", headers=admin).json()
        assert wo_after["partsCost"] == 50_000  # (2+3) * 10_000
        p = client.get(f"/api/parts/{part['id']}", headers=admin).json()
        assert p["stockQty"] == 5

    def test_insufficient_stock_409(self, client, admin, asset_id):
        part = _make_part(client, admin, stock=2)
        wo = _make_wo(client, admin, asset_id)
        r = client.post(f"/api/work-orders/{wo['id']}/parts",
                        json={"partId": part["id"], "quantity": 5}, headers=admin)
        assert r.status_code == 409
        # stock unchanged
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 2

    def test_wo_parts_list(self, client, admin, asset_id):
        part = _make_part(client, admin, stock=10)
        wo = _make_wo(client, admin, asset_id)
        client.post(f"/api/work-orders/{wo['id']}/parts", json={"partId": part["id"], "quantity": 1}, headers=admin)
        parts = client.get(f"/api/work-orders/{wo['id']}/parts", headers=admin).json()
        assert len(parts) == 1
        assert parts[0]["partName"] == "Filter Udara"


class TestInventory:
    def test_low_stock_flag_and_filter(self, client, admin):
        _make_part(client, admin, sku="LOW-1", stock=1, reorder=5)
        _make_part(client, admin, sku="OK-1", stock=50, reorder=5)
        low = client.get("/api/parts?low_stock=true", headers=admin).json()
        skus = {p["sku"] for p in low}
        assert "LOW-1" in skus and "OK-1" not in skus

    def test_duplicate_sku_409(self, client, admin):
        _make_part(client, admin, sku="DUP-1")
        r = client.post("/api/parts", json={"sku": "DUP-1", "name": "x"}, headers=admin)
        assert r.status_code == 409
