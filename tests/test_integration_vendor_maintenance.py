"""Integration tests — vendor assignment + SLA + performance (Tier 3)."""

from datetime import date, timedelta

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_vendor@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Lift Barang", "category": "Elevator", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


@pytest.fixture()
def vendor_id(client, admin):
    return client.post("/api/vendors", json={"name": "PT Servis Lift", "category": "Elevator"},
                       headers=admin).json()["id"]


def _make_wo(client, admin, asset_id):
    return client.post("/api/work-orders", json={
        "assetId": asset_id, "type": "corrective", "title": "Lift macet", "priority": "high",
    }, headers=admin).json()


def _complete(client, admin, wo_id):
    client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": "in-progress"}, headers=admin)
    client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": "completed"}, headers=admin)


class TestAssignment:
    def test_assign_vendor_sets_fields(self, client, admin, asset_id, vendor_id):
        wo = _make_wo(client, admin, asset_id)
        sla = (date.today() + timedelta(days=3)).isoformat()
        r = client.patch(f"/api/work-orders/{wo['id']}/assign-vendor",
                         json={"vendorId": vendor_id, "slaDue": sla}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["vendorName"] == "PT Servis Lift"
        assert r.json()["slaDue"] == sla

    def test_assign_unknown_vendor_404(self, client, admin, asset_id):
        wo = _make_wo(client, admin, asset_id)
        r = client.patch(f"/api/work-orders/{wo['id']}/assign-vendor",
                         json={"vendorId": "00000000-0000-0000-0000-000000000000"}, headers=admin)
        assert r.status_code == 404


class TestSLA:
    def test_completed_before_due_is_on_time(self, client, admin, asset_id, vendor_id):
        wo = _make_wo(client, admin, asset_id)
        client.patch(f"/api/work-orders/{wo['id']}/assign-vendor",
                     json={"vendorId": vendor_id, "slaDue": (date.today() + timedelta(days=5)).isoformat()},
                     headers=admin)
        _complete(client, admin, wo["id"])
        got = client.get(f"/api/work-orders/{wo['id']}", headers=admin).json()
        assert got["slaMet"] is True

    def test_completed_after_due_is_late(self, client, admin, asset_id, vendor_id):
        wo = _make_wo(client, admin, asset_id)
        client.patch(f"/api/work-orders/{wo['id']}/assign-vendor",
                     json={"vendorId": vendor_id, "slaDue": (date.today() - timedelta(days=1)).isoformat()},
                     headers=admin)
        _complete(client, admin, wo["id"])
        got = client.get(f"/api/work-orders/{wo['id']}", headers=admin).json()
        assert got["slaMet"] is False


class TestPerformance:
    def test_performance_aggregates(self, client, admin, asset_id, vendor_id):
        # one on-time, one late
        for delta in (5, -1):
            wo = _make_wo(client, admin, asset_id)
            client.patch(f"/api/work-orders/{wo['id']}/assign-vendor",
                         json={"vendorId": vendor_id, "slaDue": (date.today() + timedelta(days=delta)).isoformat()},
                         headers=admin)
            _complete(client, admin, wo["id"])

        perf = client.get(f"/api/vendors/{vendor_id}/performance", headers=admin).json()
        assert perf["totalAssigned"] == 2
        assert perf["completed"] == 2
        assert perf["onTime"] == 1
        assert perf["onTimePct"] == 50.0
