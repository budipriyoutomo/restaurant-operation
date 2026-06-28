"""Integration tests: Issue creation flow and status transitions."""

import pytest


ISSUE_PAYLOAD = {
    "title": "AC unit broken at main hall",
    "description": "Temperature 32°C, guests complaining",
    "outlet": "Outlet Kuala Lumpur",
    "category": "Maintenance",
    "priority": "high",
    "assignee": "Ahmad Razif",
    "generateTask": True,
    "generateApproval": False,
}


class TestCreateIssue:
    def test_create_returns_201(self, client):
        res = client.post("/api/issues", json=ISSUE_PAYLOAD)
        assert res.status_code == 201

    def test_number_format(self, client):
        res = client.post("/api/issues", json=ISSUE_PAYLOAD)
        data = res.json()
        assert data["number"].startswith("ISS-")

    def test_auto_assigned_status_when_assignee_set(self, client):
        res = client.post("/api/issues", json=ISSUE_PAYLOAD)
        assert res.json()["status"] == "assigned"

    def test_open_status_when_unassigned(self, client):
        payload = {**ISSUE_PAYLOAD, "assignee": "Unassigned", "generateTask": False}
        res = client.post("/api/issues", json=payload)
        assert res.json()["status"] == "open"

    def test_task_auto_generated(self, client):
        res = client.post("/api/issues", json=ISSUE_PAYLOAD)
        assert len(res.json()["taskIds"]) == 1

    def test_no_task_when_flag_false(self, client):
        payload = {**ISSUE_PAYLOAD, "generateTask": False}
        res = client.post("/api/issues", json=payload)
        assert res.json()["taskIds"] == []

    def test_approval_auto_generated(self, client):
        payload = {**ISSUE_PAYLOAD, "generateApproval": True, "category": "Procurement"}
        res = client.post("/api/issues", json=payload)
        assert res.json()["approvalId"] is not None

    def test_no_approval_when_flag_false(self, client):
        res = client.post("/api/issues", json=ISSUE_PAYLOAD)
        assert res.json()["approvalId"] is None


class TestListIssues:
    def test_returns_list(self, client):
        client.post("/api/issues", json=ISSUE_PAYLOAD)
        res = client.get("/api/issues")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
        assert len(res.json()) >= 1

    def test_filter_by_status(self, client):
        client.post("/api/issues", json=ISSUE_PAYLOAD)
        res = client.get("/api/issues?status=assigned")
        assert res.status_code == 200
        for issue in res.json():
            assert issue["status"] == "assigned"


class TestUpdateIssue:
    def test_status_change(self, client):
        issue_id = client.post("/api/issues", json=ISSUE_PAYLOAD).json()["id"]
        res = client.patch(f"/api/issues/{issue_id}", json={"status": "in-progress"})
        assert res.status_code == 200
        assert res.json()["status"] == "in-progress"

    def test_404_on_unknown_id(self, client):
        res = client.patch("/api/issues/00000000-0000-0000-0000-000000000000", json={"status": "closed"})
        assert res.status_code == 404

    def test_status_change_creates_audit_log(self, client):
        issue_id = client.post("/api/issues", json=ISSUE_PAYLOAD).json()["id"]
        client.patch(f"/api/issues/{issue_id}", json={"status": "resolved"})
        logs = client.get(f"/api/audit-logs?table_name=issues&record_id={issue_id}").json()
        assert any(log["action"] == "status_change" for log in logs)
