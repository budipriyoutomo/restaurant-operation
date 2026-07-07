"""Integration tests — atomic creation of Issue → WO → ApprovalRequest.

Verifies:
  - All 3 entities are created in one transaction (positive path)
  - Invalid assetId prevents the entire operation — no orphan WO or approval
  - generateWorkOrder=False → no WO created
  - Non-Maintenance category with generateWorkOrder=True → no WO (Maintenance-only)
"""

import pytest
from tests.conftest import register_and_login

ABOVE_THRESHOLD = 2_000_000
BELOW_THRESHOLD =   200_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mgr(client):
    return register_and_login(client, "mgr_itg@test.test", "Pass1234!", "manager")


@pytest.fixture()
def asset_id(client, mgr):
    res = client.post("/api/assets", json={
        "name": "Genset Lantai 2",
        "category": "Electrical",
        "outlet": "Jakarta",
        "status": "operational",
    }, headers=mgr)
    assert res.status_code == 201
    return res.json()["id"]


# ---------------------------------------------------------------------------
# 1. All-or-nothing creation
# ---------------------------------------------------------------------------

class TestAtomicCreation:
    def test_issue_wo_approval_all_created(self, client, mgr, asset_id):
        """Single request creates Issue + WO + ApprovalRequest + 2 ApprovalSteps."""
        res = client.post("/api/issues", json={
            "title": "Genset gagal nyala saat PLN mati",
            "category": "Maintenance",
            "priority": "critical",
            "reportedBy": "Teknisi A",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        assert res.status_code == 201
        data = res.json()

        # Issue exists
        assert "id" in data

        # WO created and linked
        wo_id = data["workOrderId"]
        assert wo_id is not None

        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert wo["requiresApproval"] is True
        assert wo["status"] == "on-hold"

        # Approval created and linked
        approval_id = wo["approvalId"]
        assert approval_id is not None

        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        assert approval["status"] == "pending"
        assert len(approval["steps"]) == 2

    def test_invalid_asset_id_creates_nothing(self, client, mgr):
        """Non-existent assetId → 404, no orphan WO or approval created."""
        fake_asset_id = "00000000-0000-0000-0000-000000000000"
        res = client.post("/api/issues", json={
            "title": "Issue with bad asset",
            "category": "Maintenance",
            "priority": "low",
            "reportedBy": "X",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": fake_asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        # Asset not found → should fail cleanly
        assert res.status_code == 404

        # No orphan WOs linked to the fake asset
        wos = client.get(f"/api/work-orders?asset_id={fake_asset_id}", headers=mgr).json()
        assert wos == []

    def test_no_wo_if_generate_false(self, client, mgr, asset_id):
        """generateWorkOrder=False → issue created, workOrderId is null."""
        res = client.post("/api/issues", json={
            "title": "Issue tanpa WO",
            "category": "Maintenance",
            "priority": "low",
            "reportedBy": "Y",
            "outlet": "Jakarta",
            "generateWorkOrder": False,
            "assetId": asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        assert res.status_code == 201
        assert res.json()["workOrderId"] is None

    def test_non_maintenance_no_wo(self, client, mgr, asset_id):
        """Non-Maintenance category does not create a WO even with generateWorkOrder=True."""
        res = client.post("/api/issues", json={
            "title": "AC bising",
            "category": "Complaint",
            "priority": "low",
            "reportedBy": "Tamu",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        assert res.status_code == 201
        assert res.json()["workOrderId"] is None


# ---------------------------------------------------------------------------
# 2. WO–Issue linkage integrity
# ---------------------------------------------------------------------------

class TestLinkageIntegrity:
    def test_wo_links_back_to_issue(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Pompa air bocor",
            "category": "Maintenance",
            "priority": "high",
            "reportedBy": "Staf B",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": BELOW_THRESHOLD,
        }, headers=mgr)
        issue_id = res.json()["id"]
        wo_id = res.json()["workOrderId"]

        wos_for_issue = client.get(f"/api/work-orders?issue_id={issue_id}", headers=mgr).json()
        assert any(w["id"] == wo_id for w in wos_for_issue)

    def test_wo_type_is_corrective(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Kran bocor",
            "category": "Maintenance",
            "priority": "medium",
            "reportedBy": "Staf C",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": BELOW_THRESHOLD,
        }, headers=mgr)
        wo_id = res.json()["workOrderId"]
        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert wo["type"] == "corrective"

    def test_approval_linked_to_correct_issue(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Chiller gagal",
            "category": "Maintenance",
            "priority": "critical",
            "reportedBy": "Eng A",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        issue_id = res.json()["id"]
        wo_id = res.json()["workOrderId"]
        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        approval_id = wo["approvalId"]

        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        # The approval's issue_id should match the created issue
        assert approval["issueId"] == issue_id


# ---------------------------------------------------------------------------
# 3. Estimated cost stored on WO
# ---------------------------------------------------------------------------

class TestEstimatedCost:
    def test_estimated_cost_persisted_on_wo(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Boiler rusak",
            "category": "Maintenance",
            "priority": "high",
            "reportedBy": "Eng B",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": 750_000,
        }, headers=mgr)
        wo_id = res.json()["workOrderId"]
        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert wo["estimatedCost"] == 750_000

    def test_approval_amount_matches_estimated_cost(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Lift darurat rusak",
            "category": "Maintenance",
            "priority": "critical",
            "reportedBy": "Security",
            "outlet": "Jakarta",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": ABOVE_THRESHOLD,
        }, headers=mgr)
        wo_id = res.json()["workOrderId"]
        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        approval = client.get(f"/api/approvals/{wo['approvalId']}", headers=mgr).json()
        assert approval["amount"] == str(ABOVE_THRESHOLD)
