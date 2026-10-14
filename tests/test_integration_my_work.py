"""Integration — "Pekerjaan Saya" (Todo-Pilot §19 / UX-6).

GET /api/my-work: what is waiting for *me* across modules, computed on the
server (the browser will stop loading everything at sign-in — item 22):
approvals whose active step I may decide, work orders and tasks assigned to me,
issues of my outlets ready to close, guest complaints still waiting for a
first response. Each list is outlet-scoped and gated by module permission.
"""

import pytest

from app.models.outlet import Outlet
from app.models.user import User
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_mw@test.test", "admin")


@pytest.fixture()
def outlets(db):
    return {o.name: o for o in db.query(Outlet).filter(Outlet.name.in_(["Jakarta", "Bandung"])).all()}


@pytest.fixture()
def manager_jkt(client, db, outlets):
    return seed_user_headers(db, "mgr_mw@test.test", "manager", name="Mgr JKT", outlets=[outlets["Jakarta"]])


@pytest.fixture()
def manager_bdg(client, db, outlets):
    return seed_user_headers(db, "mgr_bdg_mw@test.test", "manager", name="Mgr BDG", outlets=[outlets["Bandung"]])


@pytest.fixture()
def tech(client, db, outlets):
    return seed_user_headers(db, "tech_mw@test.test", "staff", name="Tek Jakarta", outlets=[outlets["Jakarta"]])


def _uid(db, email):
    return str(db.query(User).filter(User.email == email).one().id)


def _asset(client, admin, outlet="Jakarta"):
    return client.post("/api/assets", json={
        "name": f"Freezer {outlet}", "category": "Refrigeration", "outlet": outlet, "status": "operational",
    }, headers=admin).json()["id"]


def _issue(client, headers, **over):
    body = {"title": "Kompor mati", "description": "x", "category": "Other", "priority": "high",
            "outlet": "Jakarta", "generateTask": False}
    body.update(over)
    res = client.post("/api/issues", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _work(client, headers):
    res = client.get("/api/my-work", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _ids(section):
    return {x["id"] for x in section["items"]}


class TestApprovals:
    def test_step_for_my_tier_in_my_outlet_is_waiting_for_me(self, client, admin, manager_jkt, manager_bdg):
        issue = _issue(client, admin, category="Maintenance", generateWorkOrder=True,
                       assetId=_asset(client, admin), estimatedCost=3_000_000)
        w = _work(client, manager_jkt)
        assert issue["approvalId"] in _ids(w["approvals"])
        item = next(x for x in w["approvals"]["items"] if x["id"] == issue["approvalId"])
        assert item["stepOrder"] == 1 and item["stepCount"] == 2 and item["amount"] == 3_000_000
        # another outlet's manager does not see it; the admin's step is not active yet
        assert issue["approvalId"] not in _ids(_work(client, manager_bdg)["approvals"])
        assert issue["approvalId"] not in _ids(_work(client, admin)["approvals"])

    def test_after_step_one_it_moves_to_the_admin(self, client, admin, manager_jkt):
        issue = _issue(client, admin, category="Maintenance", generateWorkOrder=True,
                       assetId=_asset(client, admin), estimatedCost=3_000_000)
        client.patch(f"/api/approvals/{issue['approvalId']}/decide", json={"decision": "approved"}, headers=manager_jkt)
        assert issue["approvalId"] not in _ids(_work(client, manager_jkt)["approvals"])
        assert issue["approvalId"] in _ids(_work(client, admin)["approvals"])


class TestAssignedToMe:
    def test_work_orders_and_tasks_assigned_to_me(self, client, db, admin, tech):
        tech_id = _uid(db, "tech_mw@test.test")
        wo = client.post("/api/work-orders", json={
            "assetId": _asset(client, admin), "title": "Ganti seal", "assigneeUserId": tech_id,
        }, headers=admin).json()
        issue = _issue(client, admin, generateTask=True, assigneeUserId=tech_id)
        w = _work(client, tech)
        assert wo["id"] in _ids(w["workOrders"])
        assert issue["taskIds"][0] in _ids(w["tasks"])
        task = next(x for x in w["tasks"]["items"] if x["id"] == issue["taskIds"][0])
        assert task["issueId"] == issue["id"]

    def test_finished_work_drops_off(self, client, db, admin, tech):
        tech_id = _uid(db, "tech_mw@test.test")
        wo = client.post("/api/work-orders", json={
            "assetId": _asset(client, admin), "title": "Ganti seal", "assigneeUserId": tech_id,
        }, headers=admin).json()
        for target in ("in-progress", "completed"):
            client.patch(f"/api/work-orders/{wo['id']}/transition", json={"targetStatus": target}, headers=admin)
        assert wo["id"] not in _ids(_work(client, tech)["workOrders"])

    def test_someone_elses_work_is_not_mine(self, client, db, admin, tech):
        client.post("/api/work-orders", json={"assetId": _asset(client, admin), "title": "Tanpa teknisi"}, headers=admin)
        assert _work(client, tech)["workOrders"]["count"] == 0


class TestReadyAndGuests:
    def test_ready_to_close_for_managers_of_that_outlet(self, client, admin, manager_jkt, manager_bdg, tech):
        issue = _issue(client, admin, category="Maintenance", generateWorkOrder=True,
                       assetId=_asset(client, admin), estimatedCost=100_000)
        for target in ("in-progress", "completed"):
            client.patch(f"/api/work-orders/{issue['workOrderId']}/transition", json={"targetStatus": target}, headers=admin)
        assert issue["id"] in _ids(_work(client, manager_jkt)["readyToClose"])
        assert issue["id"] not in _ids(_work(client, manager_bdg)["readyToClose"])
        assert _work(client, tech)["readyToClose"] is None       # staff cannot close issues

    def test_guest_complaints_waiting_for_a_first_response(self, client, admin, manager_jkt):
        res = client.post("/api/guest-cases", json={
            "title": "Steak dingin", "outlet": "Jakarta", "priority": "medium", "channel": "walk-in",
        }, headers=admin)
        assert res.status_code == 201, res.text
        case = res.json()
        assert case["id"] in _ids(_work(client, manager_jkt)["guestWaiting"])
        client.post(f"/api/guest-cases/{case['id']}/respond", json={"note": "Ditelepon"}, headers=admin)
        assert case["id"] not in _ids(_work(client, manager_jkt)["guestWaiting"])


def test_counts_and_limit(client, db, admin, tech):
    tech_id = _uid(db, "tech_mw@test.test")
    asset = _asset(client, admin)
    for i in range(3):
        client.post("/api/work-orders", json={"assetId": asset, "title": f"WO {i}", "assigneeUserId": tech_id}, headers=admin)
    res = client.get("/api/my-work", params={"limit": 2}, headers=tech)
    assert res.json()["workOrders"]["count"] == 3
    assert len(res.json()["workOrders"]["items"]) == 2


def test_requires_sign_in(client):
    assert client.get("/api/my-work").status_code == 401
