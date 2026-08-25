"""Integration tests — asset QR sticker resolution (Tier 5.2)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_qr@test.test", "admin")


@pytest.fixture()
def asset(client, admin):
    return client.post("/api/assets", json={
        "name": "AC QR", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()


class TestQrToken:
    def test_new_asset_has_a_token(self, asset):
        assert asset["qrToken"]
        assert len(asset["qrToken"]) >= 16

    def test_tokens_are_unique(self, client, admin):
        a = client.post("/api/assets", json={"name": "A", "category": "x", "outlet": "Jakarta"}, headers=admin).json()
        b = client.post("/api/assets", json={"name": "B", "category": "x", "outlet": "Jakarta"}, headers=admin).json()
        assert a["qrToken"] != b["qrToken"]

    def test_token_is_not_the_asset_id(self, asset):
        # Opaque on purpose — a sticker must not leak the internal UUID.
        assert asset["qrToken"] != asset["id"]


class TestResolve:
    def test_resolve_returns_asset(self, client, admin, asset):
        res = client.get(f"/api/assets/by-qr/{asset['qrToken']}", headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["asset"]["id"] == asset["id"]
        assert res.json()["activeWorkOrderId"] is None
        assert res.json()["openWorkOrderCount"] == 0

    def test_resolve_points_at_active_work_order(self, client, admin, asset):
        wo = client.post("/api/work-orders", json={
            "assetId": asset["id"], "type": "corrective", "title": "Servis", "priority": "high",
        }, headers=admin).json()
        res = client.get(f"/api/assets/by-qr/{asset['qrToken']}", headers=admin).json()
        assert res["activeWorkOrderId"] == wo["id"]
        assert res["openWorkOrderCount"] == 1

    def test_completed_work_order_not_counted_active(self, client, admin, asset):
        wo = client.post("/api/work-orders", json={
            "assetId": asset["id"], "type": "corrective", "title": "Selesai", "priority": "low",
        }, headers=admin).json()
        client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": "in-progress"}, headers=admin)
        client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": "completed"}, headers=admin)
        res = client.get(f"/api/assets/by-qr/{asset['qrToken']}", headers=admin).json()
        assert res["activeWorkOrderId"] is None

    def test_unknown_token_404(self, client, admin):
        assert client.get("/api/assets/by-qr/deadbeefdeadbeef", headers=admin).status_code == 404


class TestScoping:
    def test_other_outlet_token_resolves_404(self, client, db, admin):
        from app.models.outlet import Outlet
        jkt = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
        mgr_jkt = seed_user_headers(db, "mgr_qr_jkt@test.test", "manager", outlets=[jkt])

        bandung_asset = client.post("/api/assets", json={
            "name": "AC Bandung", "category": "HVAC", "outlet": "Bandung", "status": "operational",
        }, headers=admin).json()

        # A scanned sticker from another outlet must not leak the asset.
        res = client.get(f"/api/assets/by-qr/{bandung_asset['qrToken']}", headers=mgr_jkt)
        assert res.status_code == 404
