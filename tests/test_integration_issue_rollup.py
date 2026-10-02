"""Integration tests — Task → Issue status roll-up (Todo-Next §2.1).

When the last open Task of an Issue is resolved, the Issue becomes `resolved`,
unless a pending Approval or an active Work Order still blocks it. When that
blocker clears (final approval / WO completed), the roll-up runs again.
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_rollup@test.test", "admin")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_rollup@test.test", "manager")


def _issue(client, headers, **overrides):
    payload = {
        "title": "Lampu dapur mati", "category": "Other", "priority": "medium",
        "outlet": "Jakarta", "assignee": "Budi", "generateTask": True,
    }
    payload.update(overrides)
    res = client.post("/api/issues", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _set_task(client, headers, task_id, status):
    res = client.patch(f"/api/tasks/{task_id}", json={"status": status}, headers=headers)
    assert res.status_code == 200, res.text


def _issue_status(client, headers, issue_id):
    return client.get(f"/api/issues/{issue_id}", headers=headers).json()["status"]


def test_resolving_last_task_resolves_issue(client, admin):
    issue = _issue(client, admin)
    _set_task(client, admin, issue["taskIds"][0], "resolved")
    assert _issue_status(client, admin, issue["id"]) == "resolved"


def test_rollup_is_audited_as_system(client, admin):
    issue = _issue(client, admin)
    _set_task(client, admin, issue["taskIds"][0], "resolved")

    logs = client.get(f"/api/audit-logs?table_name=issues&record_id={issue['id']}", headers=admin).json()
    rollup = [l for l in logs if (l.get("newValue") or l.get("new_value") or {}).get("reason") == "all_tasks_done"]
    assert len(rollup) == 1
    assert (rollup[0].get("performedBy") or rollup[0].get("performed_by")) == "system"


def test_task_in_progress_does_not_resolve(client, admin):
    issue = _issue(client, admin)
    _set_task(client, admin, issue["taskIds"][0], "in-progress")
    assert _issue_status(client, admin, issue["id"]) == "assigned"


def test_pending_approval_blocks_until_final_approval(client, admin, manager):
    issue = _issue(client, admin, category="Procurement", generateApproval=True, approvalAmount=500_000)
    _set_task(client, admin, issue["taskIds"][0], "resolved")
    assert _issue_status(client, admin, issue["id"]) != "resolved"

    approval_id = issue["approvalId"]
    client.patch(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"}, headers=manager)
    assert _issue_status(client, admin, issue["id"]) != "resolved"      # step 2 still pending
    res = client.patch(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"}, headers=admin)
    assert res.json()["status"] == "approved"
    assert _issue_status(client, admin, issue["id"]) == "resolved"


def test_active_work_order_blocks_until_completed(client, admin):
    asset = client.post("/api/assets", json={
        "name": "Exhaust Hood", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    issue = _issue(client, admin, category="Maintenance", generateWorkOrder=True,
                   assetId=asset["id"], estimatedCost=200_000)
    wo_id = issue["workOrderId"]

    _set_task(client, admin, issue["taskIds"][0], "resolved")
    assert _issue_status(client, admin, issue["id"]) != "resolved"

    client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": "in-progress"}, headers=admin)
    res = client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": "completed"}, headers=admin)
    assert res.status_code == 200, res.text
    assert _issue_status(client, admin, issue["id"]) == "resolved"


def test_reopening_task_keeps_issue_resolved(client, admin):
    issue = _issue(client, admin)
    _set_task(client, admin, issue["taskIds"][0], "resolved")
    _set_task(client, admin, issue["taskIds"][0], "in-progress")
    assert _issue_status(client, admin, issue["id"]) == "resolved"
