"""Integration tests — approval step notifications (Todo-CMMS.md §2.3).

Verifies:
  - approving an intermediate step notifies the next approver (by role)
  - the intermediate approve does NOT yet notify the requester as "decided"
  - the final decision notifies managers/admins (requester side)
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_ntf@test.test", "admin")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_ntf@test.test", "manager")


@pytest.fixture()
def approval_id(client, admin):
    """A maintenance approval above threshold → default steps [manager, admin]."""
    asset = client.post("/api/assets", json={
        "name": "Genset", "category": "Electrical", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    issue = client.post("/api/issues", json={
        "title": "Genset rusak berat", "category": "Maintenance", "priority": "critical",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": 3_000_000,
    }, headers=admin).json()
    aid = issue["approvalId"]
    assert aid is not None
    return aid


def _notes(client, headers, contains=None, entity_type=None):
    items = client.get("/api/notifications?limit=100", headers=headers).json()
    out = items
    if entity_type:
        out = [n for n in out if n["entity_type"] == entity_type]
    if contains:
        out = [n for n in out if contains.lower() in (n["title"] + n["message"]).lower()]
    return out


class TestNextApproverNotified:
    def test_intermediate_approve_notifies_next_approver(self, client, admin, manager, approval_id):
        # Manager approves step 1 → step 2 (admin) becomes active
        r = client.patch(f"/api/approvals/{approval_id}/decide",
                         json={"decision": "approved"}, headers=manager)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending"
        assert r.json()["currentStepOrder"] == 2

        # Admin (next approver) should have a "menunggu" notification
        waiting = _notes(client, admin, contains="menunggu", entity_type="approvals")
        assert len(waiting) == 1

    def test_intermediate_approve_does_not_notify_requester_decided(self, client, admin, manager, approval_id):
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=manager)
        # No "Approved/Rejected" decided notification yet (still mid-chain)
        decided = _notes(client, admin, contains="Approved", entity_type="approvals")
        assert decided == []


class TestFinalDecisionNotifies:
    def test_final_approve_notifies_requester_side(self, client, admin, manager, approval_id):
        client.patch(f"/api/approvals/{approval_id}/decide",
                     json={"decision": "approved"}, headers=manager)
        r2 = client.patch(f"/api/approvals/{approval_id}/decide",
                          json={"decision": "approved"}, headers=admin)
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "approved"

        decided = _notes(client, admin, contains="Approved", entity_type="approvals")
        assert len(decided) >= 1
