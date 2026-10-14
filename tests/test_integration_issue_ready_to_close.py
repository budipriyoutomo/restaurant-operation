"""Integration tests — "Siap ditutup" + bulk close (Todo-Pilot §18 / UX-5, Option A).

An Issue whose derived records are all finished but which no roll-up resolves
(e.g. a Maintenance Issue with only a Work Order) is flagged `readyToClose`,
and a manager can close many of them at once.
"""

import uuid

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_ready@test.test", "admin")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_ready@test.test", "staff")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Freezer Ready", "category": "Refrigeration", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _issue(client, headers, **overrides):
    payload = {
        "title": "Freezer bocor", "description": "Air menetes", "category": "Other", "priority": "high",
        "outlet": "Jakarta", "assignee": "Budi", "generateTask": False,
    }
    payload.update(overrides)
    res = client.post("/api/issues", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _wo_only_issue(client, headers, asset_id, complete=True):
    issue = _issue(client, headers, category="Maintenance", generateWorkOrder=True,
                   assetId=asset_id, estimatedCost=100_000)
    if complete:
        for target in ("in-progress", "completed"):
            res = client.patch(f"/api/work-orders/{issue['workOrderId']}/transition",
                               json={"targetStatus": target}, headers=headers)
            assert res.status_code == 200, res.text
    return client.get(f"/api/issues/{issue['id']}", headers=headers).json()


class TestReadyToCloseFlag:
    def test_wo_only_issue_stays_open_but_is_flagged_ready(self, client, admin, asset_id):
        issue = _wo_only_issue(client, admin, asset_id)
        assert issue["status"] != "resolved"
        assert issue["readyToClose"] is True

    def test_active_wo_is_not_ready(self, client, admin, asset_id):
        issue = _wo_only_issue(client, admin, asset_id, complete=False)
        assert issue["readyToClose"] is False

    def test_issue_without_children_is_not_ready(self, client, admin):
        assert _issue(client, admin)["readyToClose"] is False

    def test_flag_is_in_the_list_too(self, client, admin, asset_id):
        issue = _wo_only_issue(client, admin, asset_id)
        listed = {i["id"]: i for i in client.get("/api/issues", headers=admin).json()}
        assert listed[issue["id"]]["readyToClose"] is True


class TestBulkClose:
    def test_closes_ready_issues_and_reports_the_rest(self, client, admin, asset_id):
        ready_a = _wo_only_issue(client, admin, asset_id)
        ready_b = _wo_only_issue(client, admin, asset_id)
        busy = _wo_only_issue(client, admin, asset_id, complete=False)
        missing = str(uuid.uuid4())

        res = client.post("/api/issues/bulk-close",
                          json={"ids": [ready_a["id"], ready_b["id"], busy["id"], missing]}, headers=admin)
        assert res.status_code == 200, res.text
        body = res.json()
        assert sorted(i["id"] for i in body["closed"]) == sorted([ready_a["id"], ready_b["id"]])
        assert all(i["status"] == "closed" and i["readyToClose"] is False for i in body["closed"])
        failed = {f["id"]: f for f in body["failed"]}
        assert set(failed) == {busy["id"], missing}
        assert failed[busy["id"]]["number"] == busy["number"]
        wo_number = client.get(f"/api/work-orders/{busy['workOrderId']}", headers=admin).json()["number"]
        assert wo_number in failed[busy["id"]]["reason"]           # names what still blocks it
        assert failed[missing]["reason"] == "Issue not found"

        assert client.get(f"/api/issues/{busy['id']}", headers=admin).json()["status"] != "closed"

    def test_bulk_close_is_audited_per_issue(self, client, admin, asset_id):
        issue = _wo_only_issue(client, admin, asset_id)
        client.post("/api/issues/bulk-close", json={"ids": [issue["id"]]}, headers=admin)
        logs = client.get("/api/audit-logs", params={"table_name": "issues", "record_id": issue["id"]}, headers=admin)
        assert logs.status_code == 200, logs.text
        closes = [l for l in logs.json()
                  if l["action"] == "status_change" and (l["new_value"] or {}).get("status") == "closed"]
        assert len(closes) == 1
        assert closes[0]["new_value"].get("reason") == "bulk_close"
        assert closes[0]["performed_by"] == "admin_ready@test.test"

    def test_requires_issue_manage(self, client, admin, staff, asset_id):
        issue = _wo_only_issue(client, admin, asset_id)
        res = client.post("/api/issues/bulk-close", json={"ids": [issue["id"]]}, headers=staff)
        assert res.status_code == 403

    @pytest.mark.parametrize("ids", [[], [str(uuid.uuid4())] * 101])
    def test_rejects_empty_or_huge_batches(self, client, admin, ids):
        res = client.post("/api/issues/bulk-close", json={"ids": ids}, headers=admin)
        assert res.status_code == 422

    def test_invalid_id_is_reported_not_500(self, client, admin):
        res = client.post("/api/issues/bulk-close", json={"ids": ["not-a-uuid"]}, headers=admin)
        assert res.status_code == 200
        assert res.json()["failed"] == [{"id": "not-a-uuid", "number": None, "reason": "Issue not found"}]
