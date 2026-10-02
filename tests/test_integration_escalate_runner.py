"""Integration tests — the scheduled approval escalator (scripts/run_escalate_stale).

The scheduler invokes this module every hour, so the contract that matters is:
running it repeatedly must never escalate (or notify about) the same request twice.
"""

import pytest
from scripts.run_escalate_stale import run
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_esc_runner@test.test", "admin")


@pytest.fixture()
def approval_id(client, admin):
    asset = client.post("/api/assets", json={
        "name": "Chiller", "category": "Refrigeration", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    issue = client.post("/api/issues", json={
        "title": "Chiller mati", "category": "Maintenance", "priority": "critical",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": 3_000_000,
    }, headers=admin).json()
    return issue["approvalId"]


def _escalation_notes(client, headers):
    items = client.get("/api/notifications?limit=100", headers=headers).json()
    return [n for n in items if "macet" in n["title"].lower()]


def test_runner_escalates_stale_approval(client, db, admin, approval_id):
    escalated = run(db, threshold_days=0)
    assert approval_id in [str(a.id) for a in escalated]
    assert client.get(f"/api/approvals/{approval_id}", headers=admin).json()["escalated"] is True


def test_runner_twice_does_not_duplicate_notifications(client, db, admin, approval_id):
    run(db, threshold_days=0)
    first = len(_escalation_notes(client, admin))
    second_run = run(db, threshold_days=0)

    assert approval_id not in [str(a.id) for a in second_run]
    assert len(_escalation_notes(client, admin)) == first


def test_runner_skips_fresh_approval_at_default_threshold(client, db, admin, approval_id):
    escalated = run(db)
    assert approval_id not in [str(a.id) for a in escalated]
