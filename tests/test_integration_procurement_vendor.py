"""Integration tests — data-driven vendor selection (Tier 6.2)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_pv@test.test", "admin")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_pv@test.test", "manager")


@pytest.fixture()
def part_id(client, admin):
    return client.post("/api/parts", json={
        "sku": "PV-1", "name": "Bearing", "stockQty": 10, "reorderLevel": 2, "unitCost": 40_000,
    }, headers=admin).json()["id"]


def _vendor(client, admin, name):
    return client.post("/api/vendors", json={"name": name, "category": "Parts"}, headers=admin).json()["id"]


def _order_part(client, admin, manager, part_id, vendor_id, unit_cost, qty=1):
    """Manual PR for one part → approve → order to a vendor. Leaves a PO whose
    item captures unit_cost, feeding price history."""
    pr = client.post("/api/purchase-requests", json={
        "outlet": "Jakarta",
        "items": [{"partId": part_id, "partName": "Bearing", "quantity": qty, "estUnitCost": unit_cost}],
    }, headers=admin).json()
    aid = pr["approvalId"]
    client.patch(f"/api/approvals/{aid}/decide", json={"decision": "approved"}, headers=manager)
    client.patch(f"/api/approvals/{aid}/decide", json={"decision": "approved"}, headers=admin)
    return client.post(f"/api/purchase-requests/{pr['id']}/order", json={"vendorId": vendor_id}, headers=admin).json()


class TestPriceHistory:
    def test_empty_when_never_ordered(self, client, admin, part_id):
        assert client.get(f"/api/parts/{part_id}/price-history", headers=admin).json() == []

    def test_aggregates_per_vendor(self, client, admin, manager, part_id):
        v_a = _vendor(client, admin, "Vendor Murah")
        v_b = _vendor(client, admin, "Vendor Mahal")
        _order_part(client, admin, manager, part_id, v_a, 30_000, qty=2)
        _order_part(client, admin, manager, part_id, v_a, 20_000, qty=3)   # cheaper later
        _order_part(client, admin, manager, part_id, v_b, 50_000, qty=1)

        hist = client.get(f"/api/parts/{part_id}/price-history", headers=admin).json()
        by_name = {e["vendorName"]: e for e in hist}

        a = by_name["Vendor Murah"]
        assert a["timesOrdered"] == 2
        assert a["totalQuantity"] == 5
        assert a["lastUnitCost"] == 20_000          # most recent
        assert a["avgUnitCost"] == 25_000
        assert a["minUnitCost"] == 20_000

        b = by_name["Vendor Mahal"]
        assert b["timesOrdered"] == 1
        assert b["lastUnitCost"] == 50_000

        # cheapest average first
        assert hist[0]["vendorName"] == "Vendor Murah"


class TestPerformanceSummary:
    def test_lists_vendors_with_on_time(self, client, admin):
        _vendor(client, admin, "PT Alpha")
        _vendor(client, admin, "PT Beta")
        summary = client.get("/api/vendors/performance-summary", headers=admin).json()
        names = {v["name"] for v in summary}
        assert {"PT Alpha", "PT Beta"} <= names
        # shape carries the fields the buyer compares on
        assert all("onTimePct" in v and "avgResolutionDays" in v for v in summary)

    def test_summary_route_not_shadowed_by_id_route(self, client, admin):
        # /performance-summary must not be captured as /{vendor_id}
        res = client.get("/api/vendors/performance-summary", headers=admin)
        assert res.status_code == 200
