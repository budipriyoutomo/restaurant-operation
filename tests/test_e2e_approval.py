"""e2e tests — Multi-step Approval flow.

Scenarios covered:
  1. Create Maintenance issue with cost > threshold → WO on-hold + 2-step approval
  2. Manager approve step 1 → pending, currentStepOrder=2
  3. Admin approve step 2  → approved, WO advanced to in-progress
  4. Reject path: manager reject → approval rejected, WO cancelled, Issue → waiting
  5. Role enforcement: wrong role at active step → 403
  6. Below-threshold issue → WO created without approval (scheduled, requiresApproval=False)
"""

import pytest
from tests.conftest import register_and_login

ABOVE_THRESHOLD = 1_500_000   # > APPROVAL_THRESHOLD (1_000_000)
BELOW_THRESHOLD =   500_000   # < APPROVAL_THRESHOLD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mgr(client):
    return register_and_login(client, "mgr_apr@test.test", "Pass1234!", "manager")


@pytest.fixture()
def adm(client):
    return register_and_login(client, "adm_apr@test.test", "Pass1234!", "admin")


@pytest.fixture()
def asset_id(client, mgr):
    res = client.post("/api/assets", json={
        "name": "AC Ruang Manager",
        "category": "HVAC",
        "outlet": "Bandung",
        "status": "operational",
    }, headers=mgr)
    assert res.status_code == 201
    return res.json()["id"]


@pytest.fixture()
def issue_above_threshold(client, mgr, asset_id):
    """Create a Maintenance issue that triggers approval (cost > 1 juta)."""
    res = client.post("/api/issues", json={
        "title": "Kompresor AC perlu penggantian",
        "category": "Maintenance",
        "priority": "critical",
        "reportedBy": "Staff A",
        "outlet": "Bandung",
        "generateWorkOrder": True,
        "assetId": asset_id,
        "estimatedCost": ABOVE_THRESHOLD,
    }, headers=mgr)
    assert res.status_code == 201, res.json()
    return res.json()


@pytest.fixture()
def wo_id_above(issue_above_threshold):
    return issue_above_threshold["workOrderId"]


def _get_approval_id(client, headers, wo_id: str) -> str:
    wo = client.get(f"/api/work-orders/{wo_id}", headers=headers).json()
    return wo["approvalId"]


# ---------------------------------------------------------------------------
# 1. Initial state after creation
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_issue_has_work_order_id(self, issue_above_threshold):
        assert issue_above_threshold["workOrderId"] is not None

    def test_wo_status_on_hold(self, client, mgr, wo_id_above):
        wo = client.get(f"/api/work-orders/{wo_id_above}", headers=mgr).json()
        assert wo["status"] == "on-hold"

    def test_wo_requires_approval(self, client, mgr, wo_id_above):
        wo = client.get(f"/api/work-orders/{wo_id_above}", headers=mgr).json()
        assert wo["requiresApproval"] is True
        assert wo["approvalId"] is not None

    def test_approval_created_with_two_steps(self, client, mgr, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        assert len(approval["steps"]) == 2

    def test_approval_step_roles(self, client, mgr, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        roles = [s["approverRole"] for s in approval["steps"]]
        assert roles == ["manager", "admin"]

    def test_approval_starts_at_step_1(self, client, mgr, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        assert approval["currentStepOrder"] == 1
        assert approval["status"] == "pending"


# ---------------------------------------------------------------------------
# 2. Happy path — 2-step approval
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_manager_approve_step1_still_pending(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "approved", "comment": "OK"},
                           headers=mgr)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "pending"
        assert data["currentStepOrder"] == 2

    def test_manager_approve_step1_marks_step_approved(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=mgr)
        approval = client.get(f"/api/approvals/{approval_id}", headers=mgr).json()
        assert approval["steps"][0]["status"] == "approved"
        assert approval["steps"][1]["status"] == "pending"

    def test_admin_approve_step2_final_approved(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=mgr)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "approved", "comment": "Disetujui"},
                           headers=adm)
        assert res.status_code == 200
        assert res.json()["status"] == "approved"

    def test_final_approval_advances_wo_to_in_progress(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=mgr)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=adm)
        wo = client.get(f"/api/work-orders/{wo_id_above}", headers=mgr).json()
        assert wo["status"] == "in-progress"


# ---------------------------------------------------------------------------
# 3. Reject path
# ---------------------------------------------------------------------------

class TestRejectPath:
    def test_manager_rejects_step1_approval_becomes_rejected(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "rejected", "comment": "Budget tidak cukup"},
                           headers=mgr)
        assert res.status_code == 200
        assert res.json()["status"] == "rejected"

    def test_reject_cancels_linked_work_order(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "rejected"}, headers=mgr)
        wo = client.get(f"/api/work-orders/{wo_id_above}", headers=mgr).json()
        assert wo["status"] == "cancelled"

    def test_reject_sets_issue_to_waiting(self, client, mgr, adm, issue_above_threshold):
        wo_id = issue_above_threshold["workOrderId"]
        issue_id = issue_above_threshold["id"]
        approval_id = _get_approval_id(client, mgr, wo_id)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "rejected"}, headers=mgr)
        issue = client.get(f"/api/issues/{issue_id}", headers=mgr).json()
        assert issue["status"] == "waiting"

    def test_admin_rejects_step2_after_manager_approved(self, client, mgr, adm, wo_id_above):
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=mgr)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "rejected"}, headers=adm)
        assert res.json()["status"] == "rejected"
        wo = client.get(f"/api/work-orders/{wo_id_above}", headers=mgr).json()
        assert wo["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 4. Role enforcement
# ---------------------------------------------------------------------------

class TestRoleEnforcement:
    def test_admin_cannot_act_on_step1_manager_step(self, client, mgr, adm, wo_id_above):
        """Step 1 requires manager; admin acting on it should get 403."""
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "approved"}, headers=adm)
        assert res.status_code == 403

    def test_staff_cannot_decide_approval(self, client, mgr, wo_id_above):
        staff = register_and_login(client, "staff_apr@test.test", "Pass1234!", "staff")
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "approved"}, headers=staff)
        # staff is not in require_roles("manager", "admin") → 403
        assert res.status_code == 403

    def test_manager_cannot_act_on_step2_admin_step(self, client, mgr, adm, wo_id_above):
        """After step 1 approved, step 2 is admin-only; manager acting on it should get 403."""
        approval_id = _get_approval_id(client, mgr, wo_id_above)
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=mgr)
        res = client.patch(f"/api/approvals/{approval_id}/decide",
                           json={"decision": "approved"}, headers=mgr)
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# 5. Below-threshold — no approval required
# ---------------------------------------------------------------------------

class TestBelowThreshold:
    def test_wo_created_scheduled_no_approval(self, client, mgr, asset_id):
        res = client.post("/api/issues", json={
            "title": "Lampu mati",
            "category": "Maintenance",
            "priority": "low",
            "reportedBy": "Staff B",
            "outlet": "Bandung",
            "generateWorkOrder": True,
            "assetId": asset_id,
            "estimatedCost": BELOW_THRESHOLD,
        }, headers=mgr)
        assert res.status_code == 201
        wo_id = res.json()["workOrderId"]
        assert wo_id is not None

        wo = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert wo["status"] == "scheduled"
        assert wo["requiresApproval"] is False
        assert wo["approvalId"] is None
