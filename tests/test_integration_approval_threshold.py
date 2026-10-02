"""Integration tests — per-outlet work-order approval threshold (Todo-Next §2.2).

outlets.approval_threshold overrides the global default for corrective work
orders auto-generated from a Maintenance issue. NULL = global default.
"""

import pytest
from app.services.work_order_service import APPROVAL_THRESHOLD
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_threshold@test.test", "admin")


def _outlet(client, admin, name):
    return next(o for o in client.get("/api/outlets", headers=admin).json() if o["name"] == name)


def _set_threshold(client, admin, outlet_id, value):
    res = client.patch(f"/api/outlets/{outlet_id}", json={"approvalThreshold": value}, headers=admin)
    assert res.status_code == 200, res.text
    return res.json()


def _maintenance_issue(client, admin, outlet, cost):
    asset = client.post("/api/assets", json={
        "name": "Freezer", "category": "Refrigeration", "outlet": outlet, "status": "operational",
    }, headers=admin).json()
    issue = client.post("/api/issues", json={
        "title": "Freezer bocor", "category": "Maintenance", "priority": "high",
        "outlet": outlet, "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": cost,
    }, headers=admin)
    assert issue.status_code == 201, issue.text
    return client.get(f"/api/work-orders/{issue.json()['workOrderId']}", headers=admin).json()


def test_outlet_response_exposes_threshold_and_default(client, admin):
    outlet = _outlet(client, admin, "Jakarta")
    assert outlet["approvalThreshold"] is None
    assert outlet["approvalThresholdDefault"] == APPROVAL_THRESHOLD


def test_default_threshold_applies_without_override(client, admin):
    wo = _maintenance_issue(client, admin, "Jakarta", APPROVAL_THRESHOLD + 1)
    assert wo["requiresApproval"] is True
    assert wo["status"] == "on-hold"


def test_higher_outlet_threshold_skips_approval(client, admin):
    outlet = _outlet(client, admin, "Jakarta")
    _set_threshold(client, admin, outlet["id"], 5_000_000)

    wo = _maintenance_issue(client, admin, "Jakarta", 3_000_000)
    assert wo["requiresApproval"] is False
    assert wo["status"] == "scheduled"


def test_lower_outlet_threshold_requires_approval(client, admin):
    outlet = _outlet(client, admin, "Jakarta")
    _set_threshold(client, admin, outlet["id"], 100_000)

    wo = _maintenance_issue(client, admin, "Jakarta", 200_000)
    assert wo["requiresApproval"] is True


def test_override_is_per_outlet(client, admin):
    _set_threshold(client, admin, _outlet(client, admin, "Jakarta")["id"], 100_000)

    wo = _maintenance_issue(client, admin, "Bandung", 200_000)
    assert wo["requiresApproval"] is False


def test_null_clears_override_and_omitted_keeps_it(client, admin):
    outlet_id = _outlet(client, admin, "Jakarta")["id"]
    _set_threshold(client, admin, outlet_id, 100_000)

    kept = client.patch(f"/api/outlets/{outlet_id}", json={"status": "operational"}, headers=admin).json()
    assert kept["approvalThreshold"] == 100_000

    cleared = _set_threshold(client, admin, outlet_id, None)
    assert cleared["approvalThreshold"] is None


def test_negative_threshold_rejected(client, admin):
    outlet_id = _outlet(client, admin, "Jakarta")["id"]
    res = client.patch(f"/api/outlets/{outlet_id}", json={"approvalThreshold": -1}, headers=admin)
    assert res.status_code == 422
