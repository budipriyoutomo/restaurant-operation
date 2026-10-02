"""Integration tests — Issue closure guard, cancel/reopen/revise, idempotent create
(Todo-Pilot §1–3)."""

import uuid

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_life@test.test", "admin")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_life@test.test", "manager")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_life@test.test", "staff")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Freezer Life", "category": "Refrigeration", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _issue(client, headers, **overrides):
    payload = {
        "title": "Freezer bocor", "description": "Air menetes", "category": "Other", "priority": "high",
        "outlet": "Jakarta", "assignee": "Budi", "generateTask": True,
    }
    payload.update(overrides)
    res = client.post("/api/issues", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _maint(client, headers, asset_id, cost, **kw):
    return _issue(client, headers, category="Maintenance", generateWorkOrder=True,
                  assetId=asset_id, estimatedCost=cost, **kw)


def _get(client, headers, issue_id):
    return client.get(f"/api/issues/{issue_id}", headers=headers).json()


def _wo(client, headers, wo_id):
    return client.get(f"/api/work-orders/{wo_id}", headers=headers).json()


def _transition(client, headers, wo_id, target):
    res = client.patch(f"/api/work-orders/{wo_id}/transition", json={"targetStatus": target}, headers=headers)
    assert res.status_code == 200, res.text


def _notifications(client, headers):
    return client.get("/api/notifications", headers=headers).json()


# ---------------------------------------------------------------------------
# §1 Closure guard
# ---------------------------------------------------------------------------

class TestClosureGuard:
    def test_in_progress_wo_blocks_close_with_409_naming_wo(self, client, admin, asset_id):
        issue = _maint(client, admin, asset_id, 200_000, generateTask=False)
        wo = _wo(client, admin, issue["workOrderId"])
        _transition(client, admin, wo["id"], "in-progress")

        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "closed"}, headers=admin)
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert wo["number"] in detail["message"]
        assert detail["blockers"] == [
            {"type": "work_order", "id": wo["id"], "number": wo["number"], "status": "in-progress"},
        ]

    def test_open_task_blocks_resolve(self, client, admin):
        issue = _issue(client, admin)
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "resolved"}, headers=admin)
        assert res.status_code == 409
        assert res.json()["detail"]["blockers"][0]["type"] == "task"

    def test_issue_response_lists_blockers(self, client, admin):
        issue = _issue(client, admin)
        assert [b["type"] for b in issue["closureBlockers"]] == ["task"]

    def test_all_tasks_resolved_and_wo_completed_can_close(self, client, admin, asset_id):
        issue = _maint(client, admin, asset_id, 200_000)
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        _transition(client, admin, issue["workOrderId"], "in-progress")
        _transition(client, admin, issue["workOrderId"], "completed")

        current = _get(client, admin, issue["id"])
        assert current["closureBlockers"] == []
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "closed"}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "closed"

    def test_manager_notified_when_last_child_finishes(self, client, admin, manager, asset_id):
        issue = _maint(client, admin, asset_id, 200_000)
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        ready = [n for n in _notifications(client, manager) if n["title"].startswith("Issue siap ditutup")]
        assert ready == []                                      # WO still open

        _transition(client, admin, issue["workOrderId"], "in-progress")
        _transition(client, admin, issue["workOrderId"], "completed")
        ready = [n for n in _notifications(client, manager)
                 if n["title"] == f"Issue siap ditutup: {issue['number']}"]
        assert len(ready) == 1

    def test_issue_is_not_auto_closed(self, client, admin):
        issue = _issue(client, admin)
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        assert _get(client, admin, issue["id"])["status"] == "resolved"   # roll-up, not closed

    def test_resolved_cannot_go_back_by_patch(self, client, admin):
        issue = _issue(client, admin)
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "in-progress"}, headers=admin)
        assert res.status_code == 409

    def test_patch_cannot_cancel(self, client, admin):
        issue = _issue(client, admin)
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "cancelled"}, headers=admin)
        assert res.status_code == 409

    def test_unknown_status_is_422(self, client, admin):
        issue = _issue(client, admin)
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "done"}, headers=admin)
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# §2 Cancel / reopen / revise
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_cascades_in_one_go(self, client, admin, asset_id):
        issue = _maint(client, admin, asset_id, 5_000_000)      # above threshold → approval
        assert issue["approvalId"]

        res = client.post(f"/api/issues/{issue['id']}/cancel", json={"reason": "Duplikat"}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "cancelled"
        assert res.json()["closureBlockers"] == []

        task = next(t for t in client.get("/api/tasks", headers=admin).json() if t["id"] == issue["taskIds"][0])
        assert task["status"] == "cancelled"
        assert _wo(client, admin, issue["workOrderId"])["status"] == "cancelled"
        approval = client.get(f"/api/approvals/{issue['approvalId']}", headers=admin).json()
        assert approval["status"] == "rejected"
        assert any("Duplikat" in (s.get("comment") or "") for s in approval["steps"])

    def test_cancel_is_audited(self, client, admin):
        issue = _issue(client, admin)
        client.post(f"/api/issues/{issue['id']}/cancel", json={}, headers=admin)
        logs = client.get(f"/api/audit-logs?table_name=issues&record_id={issue['id']}", headers=admin).json()
        assert any((l.get("new_value") or {}).get("status") == "cancelled" for l in logs)

    def test_cannot_cancel_twice_or_resolved(self, client, admin):
        issue = _issue(client, admin)
        client.post(f"/api/issues/{issue['id']}/cancel", json={}, headers=admin)
        assert client.post(f"/api/issues/{issue['id']}/cancel", json={}, headers=admin).status_code == 409

    def test_staff_cannot_cancel(self, client, admin, staff):
        issue = _issue(client, admin)
        assert client.post(f"/api/issues/{issue['id']}/cancel", json={}, headers=staff).status_code == 403

    def test_cancelled_issue_is_frozen(self, client, admin):
        issue = _issue(client, admin)
        client.post(f"/api/issues/{issue['id']}/cancel", json={}, headers=admin)
        res = client.patch(f"/api/issues/{issue['id']}", json={"status": "open"}, headers=admin)
        assert res.status_code == 409


class TestReopen:
    def _resolved(self, client, admin):
        issue = _issue(client, admin)
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        return issue

    def test_reopen_requires_reason(self, client, admin):
        issue = self._resolved(client, admin)
        assert client.post(f"/api/issues/{issue['id']}/reopen", json={}, headers=admin).status_code == 422
        assert client.post(f"/api/issues/{issue['id']}/reopen", json={"reason": ""}, headers=admin).status_code == 422

    def test_reopen_resolved_to_in_progress_audited(self, client, admin):
        issue = self._resolved(client, admin)
        res = client.post(f"/api/issues/{issue['id']}/reopen", json={"reason": "Bocor lagi"}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "in-progress"
        logs = client.get(f"/api/audit-logs?table_name=issues&record_id={issue['id']}", headers=admin).json()
        assert any((l.get("new_value") or {}).get("reason") == "Bocor lagi" for l in logs)

    def test_only_resolved_can_reopen(self, client, admin):
        issue = _issue(client, admin)
        assert client.post(f"/api/issues/{issue['id']}/reopen", json={"reason": "x"}, headers=admin).status_code == 409


class TestReviseAfterReject:
    def _rejected(self, client, admin, manager, asset_id):
        issue = _maint(client, admin, asset_id, 5_000_000)
        res = client.patch(f"/api/approvals/{issue['approvalId']}/decide",
                           json={"decision": "rejected", "comment": "Terlalu mahal"}, headers=manager)
        assert res.status_code == 200, res.text
        assert _get(client, admin, issue["id"])["status"] == "waiting"
        assert _wo(client, admin, issue["workOrderId"])["status"] == "cancelled"
        return issue

    def test_revise_restarts_same_approval_and_revives_wo(self, client, admin, manager, asset_id):
        issue = self._rejected(client, admin, manager, asset_id)

        res = client.post(f"/api/issues/{issue['id']}/revise-approval",
                          json={"amount": 3_000_000, "reason": "Pakai vendor lain"}, headers=manager)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "in-progress"
        assert res.json()["approvalId"] == issue["approvalId"]          # same request (UNIQUE issue_id)

        approval = client.get(f"/api/approvals/{issue['approvalId']}", headers=admin).json()
        assert approval["status"] == "pending"
        assert approval["amount"] == 3_000_000
        assert approval["currentStepOrder"] == 1
        assert all(s["status"] == "pending" for s in approval["steps"])

        wo = _wo(client, admin, issue["workOrderId"])
        assert wo["status"] == "on-hold"
        assert wo["estimatedCost"] == 3_000_000

        # The revived chain works end to end: approve twice → WO in-progress.
        client.patch(f"/api/approvals/{issue['approvalId']}/decide", json={"decision": "approved"}, headers=manager)
        client.patch(f"/api/approvals/{issue['approvalId']}/decide", json={"decision": "approved"}, headers=admin)
        assert _wo(client, admin, issue["workOrderId"])["status"] == "in-progress"

    def test_or_cancel_from_waiting(self, client, admin, manager, asset_id):
        issue = self._rejected(client, admin, manager, asset_id)
        res = client.post(f"/api/issues/{issue['id']}/cancel", json={"reason": "Tidak jadi"}, headers=manager)
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

    def test_revise_requires_rejected_approval(self, client, admin, asset_id):
        issue = _maint(client, admin, asset_id, 5_000_000)
        res = client.post(f"/api/issues/{issue['id']}/revise-approval", json={"amount": 1}, headers=admin)
        assert res.status_code == 409

    def test_revise_is_audited(self, client, admin, manager, asset_id):
        issue = self._rejected(client, admin, manager, asset_id)
        client.post(f"/api/issues/{issue['id']}/revise-approval", json={"amount": 2_000_000}, headers=manager)
        logs = client.get(
            f"/api/audit-logs?table_name=approval_requests&record_id={issue['approvalId']}", headers=admin,
        ).json()
        assert any(l["action"] == "revise" for l in logs)


# ---------------------------------------------------------------------------
# §3 Idempotent create
# ---------------------------------------------------------------------------

class TestIdempotentCreate:
    def test_same_key_creates_one_set_of_records(self, client, admin, asset_id):
        headers = {**admin, "Idempotency-Key": str(uuid.uuid4())}
        payload = {
            "title": "Kompor mati", "description": "x", "category": "Maintenance", "priority": "high",
            "outlet": "Jakarta", "assignee": "Budi", "generateTask": True,
            "generateWorkOrder": True, "assetId": asset_id, "estimatedCost": 5_000_000,
        }
        before = len(client.get("/api/issues", headers=admin).json())
        tasks_before = len(client.get("/api/tasks", headers=admin).json())
        wos_before = len(client.get("/api/work-orders", headers=admin).json())
        apr_before = len(client.get("/api/approvals", headers=admin).json())

        r1 = client.post("/api/issues", json=payload, headers=headers)
        r2 = client.post("/api/issues", json=payload, headers=headers)
        assert r1.status_code == r2.status_code == 201
        assert r1.json() == r2.json()

        assert len(client.get("/api/issues", headers=admin).json()) == before + 1
        assert len(client.get("/api/tasks", headers=admin).json()) == tasks_before + 1
        assert len(client.get("/api/work-orders", headers=admin).json()) == wos_before + 1
        assert len(client.get("/api/approvals", headers=admin).json()) == apr_before + 1

    def test_different_keys_create_two(self, client, admin):
        before = len(client.get("/api/issues", headers=admin).json())
        for _ in range(2):
            _issue(client, {**admin, "Idempotency-Key": str(uuid.uuid4())})
        assert len(client.get("/api/issues", headers=admin).json()) == before + 2
