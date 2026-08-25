"""Regression — Maintenance issue exposes its auto-generated workOrderId.

Confirms the fix for the previously-null IssueResponse.workOrderId (the Issue
model was missing a work_orders relationship).
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_link@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Kompresor", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def test_issue_response_populates_work_order_id(client, admin, asset_id):
    res = client.post("/api/issues", json={
        "title": "Kompresor mati", "category": "Maintenance", "priority": "high",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset_id, "estimatedCost": 200_000,
    }, headers=admin)
    assert res.status_code == 201, res.text
    wo_id = res.json()["workOrderId"]
    assert wo_id is not None

    # And it points at a real corrective WO for this asset
    wo = client.get(f"/api/work-orders/{wo_id}", headers=admin).json()
    assert wo["type"] == "corrective"
    assert wo["assetId"] == asset_id


def test_issue_get_also_populates_work_order_id(client, admin, asset_id):
    created = client.post("/api/issues", json={
        "title": "Kompresor bising", "category": "Maintenance", "priority": "medium",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset_id, "estimatedCost": 100_000,
    }, headers=admin).json()
    fetched = client.get(f"/api/issues/{created['id']}", headers=admin).json()
    assert fetched["workOrderId"] is not None
