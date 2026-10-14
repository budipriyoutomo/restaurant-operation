"""Integration — actions refused with a reason (Todo-Pilot §23 / UX-10).

The UI explains *before* a click why an action is not possible; the API must
refuse the same actions with the same reason, so a stale screen or a direct
API call cannot skip them.
"""

import pytest

from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_guard@test.test", "admin")


def _issue(client, headers, **over):
    body = {
        "title": "Kompor rusak", "description": "x", "category": "Maintenance", "priority": "high",
        "outlet": "Jakarta", "assignee": "Unassigned", "generateTask": False, **over,
    }
    res = client.post("/api/issues", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


class TestWorkOrderWaitingForApproval:
    def test_cannot_start_while_the_approval_is_pending(self, client, admin):
        issue = _issue(client, admin, generateWorkOrder=True, estimatedCost=50_000_000)
        wo = client.get(f"/api/work-orders/{issue['workOrderId']}", headers=admin).json()
        assert wo["status"] == "on-hold" and wo["requiresApproval"]
        approval = client.get(f"/api/approvals/{issue['approvalId']}", headers=admin).json()

        res = client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": "in-progress"}, headers=admin)

        assert res.status_code == 409
        assert approval["number"] in res.json()["detail"]
        assert client.get(f"/api/work-orders/{wo['id']}", headers=admin).json()["status"] == "on-hold"

    def test_a_manual_hold_can_be_resumed(self, client, admin):
        issue = _issue(client, admin, generateWorkOrder=True, estimatedCost=100_000)
        wo_id = issue["workOrderId"]
        for target in ("on-hold", "in-progress"):
            res = client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": target}, headers=admin)
            assert res.status_code == 200, res.text


class TestTaskOfAFinishedIssue:
    def test_tasks_of_a_cancelled_issue_cannot_move(self, client, admin):
        issue = _issue(client, admin, generateTask=True)
        assert client.post(f"/api/issues/{issue['id']}/cancel", json={"reason": "Dobel"}, headers=admin).status_code == 200

        res = client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "in-progress"}, headers=admin)

        assert res.status_code == 409
        assert issue["number"] in res.json()["detail"]

    def test_tasks_of_a_closed_issue_cannot_move(self, client, admin):
        issue = _issue(client, admin, generateTask=True)
        task_id = issue["taskIds"][0]
        assert client.patch(f"/api/tasks/{task_id}", json={"status": "resolved"}, headers=admin).status_code == 200
        assert client.patch(f"/api/issues/{issue['id']}", json={"status": "closed"}, headers=admin).status_code == 200

        res = client.patch(f"/api/tasks/{task_id}", json={"status": "in-progress"}, headers=admin)

        assert res.status_code == 409
        assert issue["number"] in res.json()["detail"]

    def test_other_fields_of_such_a_task_can_still_change(self, client, admin):
        issue = _issue(client, admin, generateTask=True)
        client.post(f"/api/issues/{issue['id']}/cancel", json={"reason": "Dobel"}, headers=admin)
        res = client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"priority": "low"}, headers=admin)
        assert res.status_code == 200
