"""Integration tests — assignees are users, not free text (Todo-Pilot §17 / UX-4).

The work-order notification used to be matched by display name: a typo or two
people with the same name meant the technician silently got nothing.
"""

import uuid

import pytest

from app.models.outlet import Outlet
from app.models.user import User
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_asg@test.test", "admin")


@pytest.fixture()
def outlets(db):
    return {o.name: o for o in db.query(Outlet).filter(Outlet.name.in_(["Jakarta", "Bandung"])).all()}


@pytest.fixture()
def techs(client, db, outlets):
    """Two technicians called "Budi" (Jakarta, Bandung), one "Joko" (Jakarta), one inactive."""
    h = {
        "budi_jkt": seed_user_headers(db, "budi.jkt@test.test", "staff", name="Budi", outlets=[outlets["Jakarta"]]),
        "budi_bdg": seed_user_headers(db, "budi.bdg@test.test", "staff", name="Budi", outlets=[outlets["Bandung"]]),
        "joko":     seed_user_headers(db, "joko@test.test", "staff", name="Joko", outlets=[outlets["Jakarta"]]),
        "gone":     seed_user_headers(db, "gone@test.test", "staff", name="Gone", outlets=[outlets["Jakarta"]]),
    }
    db.query(User).filter(User.email == "gone@test.test").update({"is_active": False})
    db.flush()
    ids = {k: str(db.query(User).filter(User.email == e).one().id) for k, e in [
        ("budi_jkt", "budi.jkt@test.test"), ("budi_bdg", "budi.bdg@test.test"),
        ("joko", "joko@test.test"), ("gone", "gone@test.test")]}
    return {"headers": h, "ids": ids}


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Freezer Asg", "category": "Refrigeration", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _notes(client, headers):
    return client.get("/api/notifications", headers=headers).json()


def _wo(client, headers, asset_id, **over):
    body = {"assetId": asset_id, "title": "Ganti kompresor", "priority": "high"}
    body.update(over)
    return client.post("/api/work-orders", json=body, headers=headers)


class TestAssignableUsers:
    def test_lists_active_users_who_can_see_the_outlet(self, client, admin, techs):
        res = client.get("/api/auth/users/assignable", params={"outlet": "Jakarta"}, headers=admin)
        assert res.status_code == 200, res.text
        ids = {u["id"] for u in res.json()}
        assert techs["ids"]["budi_jkt"] in ids and techs["ids"]["joko"] in ids
        assert techs["ids"]["budi_bdg"] not in ids          # Bandung only
        assert techs["ids"]["gone"] not in ids              # inactive
        assert set(res.json()[0]) == {"id", "name", "role_name", "approval_tier"}

    def test_open_to_anyone_who_reports_issues(self, client, techs):
        res = client.get("/api/auth/users/assignable", params={"outlet": "Jakarta"}, headers=techs["headers"]["joko"])
        assert res.status_code == 200

    def test_unknown_outlet_is_422(self, client, admin):
        assert client.get("/api/auth/users/assignable", params={"outlet": "Nowhere"}, headers=admin).status_code == 422


class TestWorkOrderAssignee:
    def test_assign_by_user_notifies_that_user_even_with_a_shared_name(self, client, admin, techs, asset_id):
        res = _wo(client, admin, asset_id, assigneeUserId=techs["ids"]["budi_jkt"])
        assert res.status_code == 201, res.text
        wo = res.json()
        assert wo["assignee"] == "Budi" and wo["assigneeUserId"] == techs["ids"]["budi_jkt"]
        assert any(n["entity_id"] == wo["id"] for n in _notes(client, techs["headers"]["budi_jkt"]))
        assert not any(n["entity_id"] == wo["id"] for n in _notes(client, techs["headers"]["budi_bdg"]))

    @pytest.mark.parametrize("who", ["budi_bdg", "gone"])
    def test_user_who_cannot_work_there_is_422(self, client, admin, techs, asset_id, who):
        res = _wo(client, admin, asset_id, assigneeUserId=techs["ids"][who])
        assert res.status_code == 422

    def test_unknown_user_is_422(self, client, admin, asset_id):
        assert _wo(client, admin, asset_id, assigneeUserId=str(uuid.uuid4())).status_code == 422
        assert _wo(client, admin, asset_id, assigneeUserId="nope").status_code == 422

    def test_legacy_name_links_only_an_unambiguous_user(self, client, admin, techs, asset_id):
        joko = _wo(client, admin, asset_id, assignee="joko").json()
        assert joko["assigneeUserId"] == techs["ids"]["joko"] and joko["assignee"] == "Joko"
        budi = _wo(client, admin, asset_id, assignee="Budi").json()
        assert budi["assigneeUserId"] is None and budi["assignee"] == "Budi"
        assert not any(n["entity_id"] == budi["id"] for n in _notes(client, techs["headers"]["budi_jkt"]))

    def test_reassign_notifies_the_new_user_and_unassign_clears(self, client, admin, techs, asset_id):
        wo = _wo(client, admin, asset_id).json()
        assert wo["assigneeUserId"] is None and wo["assignee"] == "Unassigned"
        res = client.patch(f"/api/work-orders/{wo['id']}", json={"assigneeUserId": techs["ids"]["joko"]}, headers=admin)
        assert res.status_code == 200 and res.json()["assignee"] == "Joko"
        assert any(n["entity_id"] == wo["id"] for n in _notes(client, techs["headers"]["joko"]))
        res = client.patch(f"/api/work-orders/{wo['id']}", json={"assignee": "Unassigned"}, headers=admin)
        assert res.json()["assigneeUserId"] is None and res.json()["assignee"] == "Unassigned"


class TestIssueAndTaskAssignee:
    def _issue(self, client, headers, **over):
        body = {"title": "AC bocor", "description": "x", "category": "Other", "priority": "high",
                "outlet": "Jakarta", "generateTask": True}
        body.update(over)
        res = client.post("/api/issues", json=body, headers=headers)
        return res

    def test_issue_assignee_flows_to_its_task_and_the_user_is_told(self, client, admin, techs):
        res = self._issue(client, admin, assigneeUserId=techs["ids"]["joko"])
        assert res.status_code == 201, res.text
        issue = res.json()
        assert issue["assignee"] == "Joko" and issue["assigneeUserId"] == techs["ids"]["joko"]
        task = next(t for t in client.get("/api/tasks", headers=admin).json() if t["issueId"] == issue["id"])
        assert task["assigneeUserId"] == techs["ids"]["joko"]
        assert any(n["entity_id"] == issue["id"] for n in _notes(client, techs["headers"]["joko"]))

    def test_issue_assignee_must_see_the_outlet(self, client, admin, techs):
        assert self._issue(client, admin, assigneeUserId=techs["ids"]["budi_bdg"]).status_code == 422

    def test_patch_issue_and_task_assignee(self, client, admin, techs):
        issue = self._issue(client, admin).json()
        res = client.patch(f"/api/issues/{issue['id']}", json={"assigneeUserId": techs["ids"]["budi_jkt"]}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["assigneeUserId"] == techs["ids"]["budi_jkt"] and res.json()["assignee"] == "Budi"

        task_id = issue["taskIds"][0]
        res = client.patch(f"/api/tasks/{task_id}", json={"assigneeUserId": techs["ids"]["joko"]}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["assignee"] == "Joko" and res.json()["assigneeUserId"] == techs["ids"]["joko"]
        assert any(n["entity_type"] == "issues" and n["entity_id"] == issue["id"]
                   for n in _notes(client, techs["headers"]["joko"]))
        res = client.patch(f"/api/tasks/{task_id}", json={"assigneeUserId": techs["ids"]["budi_bdg"]}, headers=admin)
        assert res.status_code == 422
