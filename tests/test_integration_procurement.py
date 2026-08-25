"""Integration tests — procurement loop PR → approval → PO → receipt (Tier 6.1)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_proc@test.test", "admin")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_proc@test.test", "manager")


@pytest.fixture()
def vendor_id(client, admin):
    return client.post("/api/vendors", json={"name": "PT Suku Cadang", "category": "Parts"},
                       headers=admin).json()["id"]


@pytest.fixture()
def wo_id(client, admin):
    asset = client.post("/api/assets", json={
        "name": "AC Proc", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    return client.post("/api/work-orders", json={
        "assetId": asset["id"], "type": "corrective", "title": "Servis", "priority": "high",
    }, headers=admin).json()["id"]


def _make_part(client, admin, sku="PROC-1", stock=10, reorder=5):
    return client.post("/api/parts", json={
        "sku": sku, "name": "Filter Proc", "stockQty": stock, "reorderLevel": reorder, "unitCost": 50_000,
    }, headers=admin).json()


def _consume(client, admin, wo_id, part_id, qty):
    return client.post(f"/api/work-orders/{wo_id}/parts",
                       json={"partId": part_id, "quantity": qty}, headers=admin)


def _open_prs_for(client, admin, part_id):
    prs = client.get("/api/purchase-requests", headers=admin).json()
    return [pr for pr in prs if any(i["partId"] == part_id for i in pr["items"])]


class TestAutoReorder:
    def test_consuming_below_reorder_raises_one_pr(self, client, admin, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)          # stock 4 ≤ 5

        prs = _open_prs_for(client, admin, part["id"])
        assert len(prs) == 1
        pr = prs[0]
        assert pr["source"] == "auto_reorder"
        assert pr["status"] == "pending_approval"
        assert pr["approvalId"] is not None                    # went through the approval engine
        # top back up to 2× reorder: order 10 - 4 = 6
        assert pr["items"][0]["quantity"] == 6

    def test_second_consumption_does_not_duplicate_pr(self, client, admin, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)          # → auto PR
        _consume(client, admin, wo_id, part["id"], 1)          # still low, but PR already open
        assert len(_open_prs_for(client, admin, part["id"])) == 1

    def test_healthy_stock_raises_nothing(self, client, admin, wo_id):
        part = _make_part(client, admin, stock=10, reorder=2)
        _consume(client, admin, wo_id, part["id"], 3)          # stock 7 > 2
        assert _open_prs_for(client, admin, part["id"]) == []

    def test_scan_low_stock_endpoint(self, client, admin):
        _make_part(client, admin, sku="LOW-A", stock=1, reorder=5)
        _make_part(client, admin, sku="OK-A", stock=50, reorder=5)
        res = client.post("/api/purchase-requests/scan-low-stock", headers=admin).json()
        assert res["created"] == 1


class TestApprovalReuse:
    def _approve_pr(self, client, manager, admin, approval_id):
        # Default chain: manager (step 1) → admin (step 2)
        client.patch(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"}, headers=manager)
        return client.patch(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"}, headers=admin)

    def test_pr_approval_uses_existing_engine(self, client, admin, manager, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)
        pr = _open_prs_for(client, admin, part["id"])[0]

        # The PR's approval shows up in the normal approval center, tagged to the PR.
        appr = client.get(f"/api/approvals/{pr['approvalId']}", headers=admin).json()
        assert appr["type"] == "procurement"
        assert appr["purchaseRequestId"] == pr["id"]
        assert appr["issueId"] is None

        r = self._approve_pr(client, manager, admin, pr["approvalId"])
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        # PR advanced to approved by the decide hook.
        assert client.get(f"/api/purchase-requests/{pr['id']}", headers=admin).json()["status"] == "approved"

    def test_pr_rejection_marks_pr_rejected(self, client, admin, manager, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)
        pr = _open_prs_for(client, admin, part["id"])[0]
        client.patch(f"/api/approvals/{pr['approvalId']}/decide", json={"decision": "rejected"}, headers=manager)
        assert client.get(f"/api/purchase-requests/{pr['id']}", headers=admin).json()["status"] == "rejected"


class TestOrderAndReceive:
    def _approved_pr(self, client, admin, manager, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)          # stock 4, PR orders 6
        pr = _open_prs_for(client, admin, part["id"])[0]
        client.patch(f"/api/approvals/{pr['approvalId']}/decide", json={"decision": "approved"}, headers=manager)
        client.patch(f"/api/approvals/{pr['approvalId']}/decide", json={"decision": "approved"}, headers=admin)
        return part, pr

    def test_order_requires_approval(self, client, admin, manager, vendor_id, wo_id):
        part = _make_part(client, admin, stock=10, reorder=5)
        _consume(client, admin, wo_id, part["id"], 6)
        pr = _open_prs_for(client, admin, part["id"])[0]
        # still pending_approval
        res = client.post(f"/api/purchase-requests/{pr['id']}/order", json={"vendorId": vendor_id}, headers=admin)
        assert res.status_code == 409

    def test_full_loop_receipt_restocks(self, client, admin, manager, vendor_id, wo_id):
        part, pr = self._approved_pr(client, admin, manager, wo_id)

        po = client.post(f"/api/purchase-requests/{pr['id']}/order", json={"vendorId": vendor_id}, headers=admin).json()
        assert po["status"] == "sent"
        assert po["items"][0]["quantityOrdered"] == 6
        # PR moved to ordered
        assert client.get(f"/api/purchase-requests/{pr['id']}", headers=admin).json()["status"] == "ordered"

        item_id = po["items"][0]["id"]
        # partial receive: 2 of 6 → stock 4 + 2 = 6
        client.post(f"/api/purchase-orders/{po['id']}/receive",
                    json={"lines": [{"purchaseOrderItemId": item_id, "quantityReceived": 2}]}, headers=admin)
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 6
        assert client.get(f"/api/purchase-orders/{po['id']}", headers=admin).json()["status"] == "partially_received"

        # receive the rest: 4 → stock 10, PO received, PR received
        client.post(f"/api/purchase-orders/{po['id']}/receive",
                    json={"lines": [{"purchaseOrderItemId": item_id, "quantityReceived": 4}]}, headers=admin)
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 10
        assert client.get(f"/api/purchase-orders/{po['id']}", headers=admin).json()["status"] == "received"
        assert client.get(f"/api/purchase-requests/{pr['id']}", headers=admin).json()["status"] == "received"

    def test_cannot_over_receive(self, client, admin, manager, vendor_id, wo_id):
        part, pr = self._approved_pr(client, admin, manager, wo_id)
        po = client.post(f"/api/purchase-requests/{pr['id']}/order", json={"vendorId": vendor_id}, headers=admin).json()
        item_id = po["items"][0]["id"]
        res = client.post(f"/api/purchase-orders/{po['id']}/receive",
                          json={"lines": [{"purchaseOrderItemId": item_id, "quantityReceived": 99}]}, headers=admin)
        assert res.status_code == 422


class TestManualPr:
    def test_manual_pr_create(self, client, admin):
        res = client.post("/api/purchase-requests", json={
            "outlet": "Jakarta", "notes": "Belanja rutin",
            "items": [{"partName": "Oli mesin", "quantity": 4, "estUnitCost": 75_000}],
        }, headers=admin)
        assert res.status_code == 201, res.text
        pr = res.json()
        assert pr["source"] == "manual"
        assert pr["totalEst"] == 300_000
        assert pr["approvalId"] is not None
