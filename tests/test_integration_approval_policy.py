"""Integration tests — approval policy engine drives step generation (§2.2).

Verifies:
  - with no policy, a maintenance approval falls back to the default 2 steps
  - a matching policy overrides the default step chain (count + roles)
  - amount tiers select different policies (1 step vs 3 steps)
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_pol@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    res = client.post("/api/assets", json={
        "name": "Chiller Utama", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin)
    assert res.status_code == 201
    return res.json()["id"]


def _create_maintenance_issue(client, admin, asset_id, cost):
    res = client.post("/api/issues", json={
        "title": "Perbaikan besar", "category": "Maintenance", "priority": "high",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset_id, "estimatedCost": cost,
    }, headers=admin)
    assert res.status_code == 201, res.text
    approval_id = res.json()["approvalId"]
    assert approval_id is not None, "expected an approval to be generated above threshold"
    return client.get(f"/api/approvals/{approval_id}", headers=admin).json()


class TestDefaultFallback:
    def test_no_policy_uses_default_two_steps(self, client, admin, asset_id):
        approval = _create_maintenance_issue(client, admin, asset_id, 2_000_000)
        roles = [s["approverRole"] for s in approval["steps"]]
        assert roles == ["manager", "admin"]


class TestPolicyOverride:
    def test_policy_one_step_overrides_default(self, client, admin, asset_id):
        # Policy: maintenance 1jt–5jt → manager only
        r = client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "minAmount": 1_000_001, "maxAmount": 5_000_000,
            "steps": [{"order": 1, "role": "manager"}], "isActive": True,
        }, headers=admin)
        assert r.status_code == 201, r.text

        approval = _create_maintenance_issue(client, admin, asset_id, 2_000_000)
        roles = [s["approverRole"] for s in approval["steps"]]
        assert roles == ["manager"]

    def test_higher_tier_adds_finance_step(self, client, admin, asset_id):
        client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "minAmount": 1_000_001, "maxAmount": 5_000_000,
            "steps": [{"order": 1, "role": "manager"}], "isActive": True,
        }, headers=admin)
        client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "minAmount": 5_000_001,
            "steps": [
                {"order": 1, "role": "manager"},
                {"order": 2, "role": "admin"},
                {"order": 3, "role": "admin"},
            ], "isActive": True,
        }, headers=admin)

        approval = _create_maintenance_issue(client, admin, asset_id, 9_000_000)
        roles = [s["approverRole"] for s in approval["steps"]]
        orders = [s["stepOrder"] for s in approval["steps"]]
        assert roles == ["manager", "admin", "admin"]
        assert orders == [1, 2, 3]


class TestPolicyCrudGuards:
    def test_empty_steps_rejected(self, client, admin):
        r = client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "steps": [], "isActive": True,
        }, headers=admin)
        assert r.status_code == 422

    def test_min_greater_than_max_rejected(self, client, admin):
        r = client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "minAmount": 5_000_000, "maxAmount": 1_000_000,
            "steps": [{"order": 1, "role": "manager"}], "isActive": True,
        }, headers=admin)
        assert r.status_code == 422
