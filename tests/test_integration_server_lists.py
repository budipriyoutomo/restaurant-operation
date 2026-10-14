"""Integration — lists and counts computed by the server (Todo-Pilot §22 / UX-9).

The browser no longer downloads every issue, task and approval at login. The
Issues list pages with every filter on the server, cards carry their approval
and work order summary, the dashboard and the menu badges get their numbers
from aggregate endpoints.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from app.models.outlet import Outlet
from tests.conftest import seed_user_headers


@pytest.fixture()
def co(client, db, test_company):
    from app.services.role_service import ensure_default_roles
    with bypass_tenant(db):
        company = Company(name="PT Daftar", slug=f"daftar-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        dago = Outlet(name="Dago", code="DGO", status="operational")
        riau = Outlet(name="Riau", code="RIA", status="operational")
        db.add_all([dago, riau])
        db.flush()
        admin = seed_user_headers(db, f"adm-{company.slug}@x.test", "admin")
        staff = seed_user_headers(db, f"stf-{company.slug}@x.test", "staff", outlets=[riau])
    return {"company": company, "admin": admin, "staff": staff}


def _issue(client, h, **over):
    body = {"title": "x", "description": "x", "outlet": "Dago", "category": "Other", "priority": "medium",
            "assignee": "Unassigned", "generateTask": False, **over}
    res = client.post("/api/issues", json=body, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def data(client, co):
    """open · late · ready (WO done, no task) · resolved · closed · waiting-for-approval"""
    h = co["admin"]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    d = {
        "open": _issue(client, h, title="Open one"),
        "late": _issue(client, h, title="Late one", dueDate=yesterday, priority="critical"),
        "ready": _issue(client, h, title="Ready one", category="Maintenance", generateWorkOrder=True, estimatedCost=100_000),
        "resolved": _issue(client, h, title="Resolved one", generateTask=True),
        "closed": _issue(client, h, title="Closed one", generateTask=True, outlet="Riau"),
        "approval": _issue(client, h, title="Needs money", category="Procurement", generateApproval=True, approvalAmount=5_000_000),
    }
    for t in ("in-progress", "completed"):
        assert client.patch(f"/api/work-orders/{d['ready']['workOrderId']}/transition", json={"targetStatus": t}, headers=h).status_code == 200
    assert client.patch(f"/api/tasks/{d['resolved']['taskIds'][0]}", json={"status": "resolved"}, headers=h).status_code == 200
    assert client.patch(f"/api/tasks/{d['closed']['taskIds'][0]}", json={"status": "resolved"}, headers=h).status_code == 200
    assert client.patch(f"/api/issues/{d['closed']['id']}", json={"status": "closed"}, headers=h).status_code == 200
    return d


def _titles(client, h, query):
    res = client.get(f"/api/issues?{query}", headers=h)
    assert res.status_code == 200, res.text
    return sorted(i["title"] for i in res.json()), int(res.headers["X-Total-Count"])


class TestIssueFilters:
    def test_active_hides_resolved_closed_cancelled(self, client, co, data):
        titles, total = _titles(client, co["admin"], "active=true")
        assert titles == ["Late one", "Needs money", "Open one", "Ready one"] and total == 4

    def test_ready_matches_the_card_flag(self, client, co, data):
        titles, _ = _titles(client, co["admin"], "ready=true")
        assert titles == ["Ready one", "Resolved one"]
        flagged = sorted(i["title"] for i in client.get("/api/issues", headers=co["admin"]).json() if i["readyToClose"])
        assert titles == flagged          # same rule in SQL and in Python

    def test_late_is_open_and_past_due(self, client, co, data):
        assert _titles(client, co["admin"], "late=true") == (["Late one"], 1)

    def test_outlet_and_paging_combine(self, client, co, data):
        titles, total = _titles(client, co["admin"], "outlet=Dago&active=true&limit=2")
        assert len(titles) == 2 and total == 4


class TestCardSummaries:
    def test_issue_carries_its_approval_and_work_order(self, client, co, data):
        rows = {i["title"]: i for i in client.get("/api/issues", headers=co["admin"]).json()}
        appr = rows["Needs money"]["approval"]
        assert appr["status"] == "pending" and appr["currentStepOrder"] == 1 and appr["stepCount"] >= 1
        assert appr["number"].startswith("APR-")
        wo = rows["Ready one"]["workOrder"]
        assert wo["status"] == "completed" and wo["number"].startswith("WO-") and wo["id"] == data["ready"]["workOrderId"]
        assert rows["Open one"]["approval"] is None and rows["Open one"]["workOrder"] is None


class TestChildLists:
    def test_tasks_and_approvals_of_one_issue(self, client, co, data):
        h = co["admin"]
        tasks = client.get(f"/api/tasks?issue_id={data['resolved']['id']}", headers=h).json()
        assert [t["id"] for t in tasks] == data["resolved"]["taskIds"]
        appr = client.get(f"/api/approvals?issue_id={data['approval']['id']}", headers=h).json()
        assert [a["id"] for a in appr] == [data["approval"]["approvalId"]]
        assert client.get("/api/tasks?issue_id=not-a-uuid", headers=h).status_code == 422

    def test_search(self, client, co, data):
        h = co["admin"]
        found = client.get("/api/approvals?q=needs%20MONEY", headers=h).json()
        assert found and all("Needs money" in a["title"] for a in found)
        found = client.get("/api/tasks?q=resolved%20one", headers=h).json()
        assert found and all("Resolved one" in t["title"] for t in found)


class TestOverview:
    def test_dashboard_numbers(self, client, co, data):
        res = client.get("/api/analytics/overview", headers=co["admin"])
        assert res.status_code == 200, res.text
        o = res.json()
        assert o["issues"]["total"] == 6
        assert o["issues"]["inProgress"] == 3          # open, late, needs money
        assert o["issues"]["awaitingClose"] == 2       # ready (WO done) + resolved
        assert o["issues"]["slaBreached"] == 1
        assert o["issues"]["byCategory"]["Maintenance"] == 1
        assert o["issues"]["byOutlet"]["Riau"] == 1
        assert [i["title"] for i in o["issues"]["urgent"]] == ["Late one"]      # critical/high, still in progress
        assert o["tasks"]["active"] == 0
        assert o["approvals"]["pending"] == 1 and o["approvals"]["pendingByType"]["procurement"] == 1

    def test_counts_for_the_menu_badges(self, client, co, data):
        c = client.get("/api/counts", headers=co["admin"]).json()
        assert c == {"issuesInProgress": 3, "tasksActive": 0, "approvalsPending": 1}

    def test_counts_follow_scope_and_permissions(self, client, db, co, data):
        from app.models.role import Role
        with tenant(db, co["company"].id):
            db.query(Role).filter(Role.key == "staff").one().permissions = {"issues": "view"}
            db.flush()
        c = client.get("/api/counts", headers=co["staff"]).json()
        assert c == {"issuesInProgress": 0, "tasksActive": None, "approvalsPending": None}   # Riau has only the closed one


class TestTaskBoard:
    def test_filters_for_the_kanban_columns(self, client, co, data):
        h = co["admin"]
        overdue = _issue(client, h, title="Late task", generateTask=True, priority="high",
                         dueDate=(date.today() - timedelta(days=2)).isoformat())
        tid = overdue["taskIds"][0]
        assert [t["id"] for t in client.get("/api/tasks?priority=high", headers=h).json()] == [tid]
        res = client.get("/api/tasks?late=true", headers=h)
        assert [t["id"] for t in res.json()] == [tid] and res.headers["X-Total-Count"] == "1"

    def test_a_task_knows_whether_its_issue_is_finished(self, client, co, data):
        rows = {t["issueId"]: t for t in client.get("/api/tasks", headers=co["admin"]).json()}
        assert rows[data["closed"]["id"]]["issueStatus"] == "closed"
        assert rows[data["resolved"]["id"]]["issueStatus"] == "resolved"
