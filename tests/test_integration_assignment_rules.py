"""Integration tests — assignee routing (Todo-Ops-Checklist P2). Written before
the implementation (TDD). Contract:

  PICs     PATCH/POST /api/pics accept `userId` (a user account; one PIC per user)
  Rules    GET/POST/PATCH/DELETE /api/issue-assignment-rules
           {categoryId, outletId|null, assigneeUserId}  — master-data:manage
  Routing  an Issue created without an assignee goes to the (category, outlet)
           rule → the (category, all outlets) rule → a PIC of the category with
           a user → Unassigned. Inactive users and users without access to the
           outlet are skipped. Task / WO / notification follow the assignee.
"""

import uuid

import pytest

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from app.models.notification import Notification
from app.models.outlet import Outlet
from app.models.user import User
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_route@test.test", "admin", name="Admin Route")


@pytest.fixture()
def people(client, db):
    """Budi works at Dago only; Andi and Siti everywhere (default: every test outlet)."""
    dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
    seed_user_headers(db, "budi@route.test", "staff", name="Budi", outlets=[dago])
    seed_user_headers(db, "andi@route.test", "staff", name="Andi")
    seed_user_headers(db, "siti@route.test", "staff", name="Siti")
    users = {u.name: u for u in db.query(User).filter(User.email.like("%@route.test"))}
    return {name: str(u.id) for name, u in users.items()} | {"_rows": users}


def _cat(client, h, key="maintenance"):
    return next(c for c in client.get("/api/categories", headers=h).json() if c["systemKey"] == key)


def _outlet_id(db, name):
    return str(db.query(Outlet).filter(Outlet.name == name).one().id)


def _rule(client, h, **body):
    return client.post("/api/issue-assignment-rules", json=body, headers=h)


def _pic(client, h, user_id, categories, email="pic@route.test", name="PIC Andi"):
    res = client.post("/api/pics", headers=h, json={
        "name": name, "email": email, "phone": "0812", "department": "Engineering",
        "categories": categories, "userId": user_id})
    assert res.status_code == 201, res.text
    return res.json()


def _issue(client, h, outlet="Dago", category="Maintenance", **over):
    body = {"title": "Kompor mati", "description": "x", "outlet": outlet, "category": category,
            "priority": "high", "generateTask": True, **over}
    res = client.post("/api/issues", json=body, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


# ── PIC ↔ user ───────────────────────────────────────────────────────────────

class TestPicUser:
    def test_pic_links_to_a_user(self, client, admin, people):
        pic = _pic(client, admin, people["Andi"], [_cat(client, admin)["id"]])
        assert (pic["userId"], pic["userName"]) == (people["Andi"], "Andi")

    def test_link_can_be_changed_and_cleared(self, client, admin, people):
        pic = _pic(client, admin, people["Andi"], [_cat(client, admin)["id"]])
        res = client.patch(f"/api/pics/{pic['id']}", json={"userId": people["Siti"]}, headers=admin)
        assert res.json()["userId"] == people["Siti"]
        res = client.patch(f"/api/pics/{pic['id']}", json={"userId": None}, headers=admin)
        assert res.status_code == 200 and res.json()["userId"] is None

    def test_one_pic_per_user(self, client, admin, people):
        cat = _cat(client, admin)["id"]
        _pic(client, admin, people["Andi"], [cat])
        res = client.post("/api/pics", headers=admin, json={
            "name": "Lagi", "email": "lagi@route.test", "phone": "1", "department": "Engineering",
            "categories": [cat], "userId": people["Andi"]})
        assert res.status_code == 409

    def test_unknown_user_is_422(self, client, admin):
        res = client.post("/api/pics", headers=admin, json={
            "name": "X", "email": "x@route.test", "phone": "1", "department": "Engineering",
            "categories": [], "userId": str(uuid.uuid4())})
        assert res.status_code == 422

    def test_pic_without_user_still_works(self, client, admin):
        res = client.post("/api/pics", headers=admin, json={
            "name": "Vendor AC", "email": "ac@route.test", "phone": "1", "department": "Engineering",
            "categories": []})
        assert res.status_code == 201 and res.json()["userId"] is None


# ── Rules CRUD ───────────────────────────────────────────────────────────────

class TestRulesCrud:
    def test_create_list_update_delete(self, client, db, admin, people):
        cat = _cat(client, admin)
        res = _rule(client, admin, categoryId=cat["id"], outletId=_outlet_id(db, "Dago"), assigneeUserId=people["Budi"])
        assert res.status_code == 201, res.text
        rule = res.json()
        assert (rule["categoryName"], rule["outlet"], rule["assigneeName"], rule["assigneeActive"]) == \
            ("Maintenance", "Dago", "Budi", True)

        listed = client.get("/api/issue-assignment-rules", headers=admin).json()
        assert [r["id"] for r in listed] == [rule["id"]]

        res = client.patch(f"/api/issue-assignment-rules/{rule['id']}", json={"assigneeUserId": people["Andi"]}, headers=admin)
        assert res.status_code == 200 and res.json()["assigneeName"] == "Andi"

        assert client.delete(f"/api/issue-assignment-rules/{rule['id']}", headers=admin).status_code == 204
        assert client.get("/api/issue-assignment-rules", headers=admin).json() == []

    def test_global_rule_has_no_outlet(self, client, admin, people):
        res = _rule(client, admin, categoryId=_cat(client, admin)["id"], outletId=None, assigneeUserId=people["Andi"])
        assert res.status_code == 201 and res.json()["outlet"] is None

    def test_one_rule_per_category_and_outlet(self, client, db, admin, people):
        cat = _cat(client, admin)["id"]
        assert _rule(client, admin, categoryId=cat, outletId=None, assigneeUserId=people["Andi"]).status_code == 201
        assert _rule(client, admin, categoryId=cat, outletId=None, assigneeUserId=people["Siti"]).status_code == 409
        dago = _outlet_id(db, "Dago")
        assert _rule(client, admin, categoryId=cat, outletId=dago, assigneeUserId=people["Andi"]).status_code == 201
        assert _rule(client, admin, categoryId=cat, outletId=dago, assigneeUserId=people["Siti"]).status_code == 409

    def test_assignee_must_work_at_the_outlet(self, client, db, admin, people):
        res = _rule(client, admin, categoryId=_cat(client, admin)["id"], outletId=_outlet_id(db, "Bandung"),
                    assigneeUserId=people["Budi"])
        assert res.status_code == 422

    def test_unknown_category_or_user_is_422(self, client, admin, people):
        assert _rule(client, admin, categoryId=str(uuid.uuid4()), outletId=None,
                     assigneeUserId=people["Andi"]).status_code == 422
        assert _rule(client, admin, categoryId=_cat(client, admin)["id"], outletId=None,
                     assigneeUserId=str(uuid.uuid4())).status_code == 422

    def test_staff_cannot_manage_rules(self, client, db, people):
        staff = seed_user_headers(db, "staff_route@test.test", "staff")
        cat = next(c for c in client.get("/api/categories", headers=staff).json() if c["systemKey"] == "maintenance")
        assert _rule(client, staff, categoryId=cat["id"], outletId=None, assigneeUserId=people["Andi"]).status_code == 403


# ── Routing ──────────────────────────────────────────────────────────────────

class TestRouting:
    def test_definition_of_done(self, client, db, admin, people):
        """Andi is the Maintenance PIC; rule Maintenance @ Dago → Budi."""
        cat = _cat(client, admin)["id"]
        _pic(client, admin, people["Andi"], [cat])
        _rule(client, admin, categoryId=cat, outletId=_outlet_id(db, "Dago"), assigneeUserId=people["Budi"])

        assert _issue(client, admin, "Dago")["assigneeUserId"] == people["Budi"]
        assert _issue(client, admin, "Bandung")["assigneeUserId"] == people["Andi"]
        other = _issue(client, admin, "Dago", category="Marketing")
        assert (other["assignee"], other["assigneeUserId"]) == ("Unassigned", None)

        people["_rows"]["Budi"].is_active = False
        db.flush()
        assert _issue(client, admin, "Dago")["assigneeUserId"] == people["Andi"]

    def test_global_rule_beats_pic(self, client, admin, people):
        cat = _cat(client, admin)["id"]
        _pic(client, admin, people["Andi"], [cat])
        _rule(client, admin, categoryId=cat, outletId=None, assigneeUserId=people["Siti"])
        assert _issue(client, admin, "Bandung")["assigneeUserId"] == people["Siti"]

    def test_pic_without_access_to_the_outlet_is_skipped(self, client, admin, people):
        cat = _cat(client, admin)["id"]
        _pic(client, admin, people["Budi"], [cat], email="budi-pic@route.test", name="PIC Budi")
        issue = _issue(client, admin, "Bandung")
        assert issue["assigneeUserId"] is None

    def test_explicit_assignee_is_kept(self, client, db, admin, people):
        cat = _cat(client, admin)["id"]
        _rule(client, admin, categoryId=cat, outletId=None, assigneeUserId=people["Andi"])
        assert _issue(client, admin, assigneeUserId=people["Siti"])["assigneeUserId"] == people["Siti"]
        assert _issue(client, admin, assignee="Tukang Luar")["assignee"] == "Tukang Luar"

    def test_task_work_order_and_notification_follow(self, client, db, admin, people):
        from app.models.asset import WorkOrder
        from app.models.task import Task
        cat = _cat(client, admin)["id"]
        _rule(client, admin, categoryId=cat, outletId=None, assigneeUserId=people["Andi"])
        issue = _issue(client, admin, generateWorkOrder=True)
        iid = uuid.UUID(issue["id"])
        assert db.query(Task).filter(Task.issue_id == iid).one().assignee_user_id == uuid.UUID(people["Andi"])
        assert db.query(WorkOrder).filter(WorkOrder.issue_id == iid).one().assignee_user_id == uuid.UUID(people["Andi"])
        assert db.query(Notification).filter(Notification.user_id == uuid.UUID(people["Andi"]),
                                             Notification.entity_id == iid).count() >= 1

    def test_custom_category_routes_too(self, client, admin, people):
        res = client.post("/api/categories", json={"name": "HVAC", "module": "maintenance"}, headers=admin)
        _rule(client, admin, categoryId=res.json()["id"], outletId=None, assigneeUserId=people["Siti"])
        assert _issue(client, admin, category="HVAC")["assigneeUserId"] == people["Siti"]


# ── Tenancy ──────────────────────────────────────────────────────────────────

class TestTenancy:
    def test_rules_are_per_company(self, client, db, admin, people):
        _rule(client, admin, categoryId=_cat(client, admin)["id"], outletId=None, assigneeUserId=people["Andi"])
        from app.services.category_service import ensure_system_categories
        from app.services.role_service import ensure_default_roles
        with bypass_tenant(db):
            company = Company(name="PT Beta Route", slug=f"beta-route-{uuid.uuid4().hex[:6]}")
            db.add(company)
            db.flush()
        with tenant(db, company.id):
            ensure_default_roles(db)
            ensure_system_categories(db)
            db.add(Outlet(name="Beta Dago", code="BDG", status="operational"))
            db.flush()
            beta = seed_user_headers(db, "admin@betaroute.test", "admin", name="Beta Admin")
        assert client.get("/api/issue-assignment-rules", headers=beta).json() == []
        beta_cat = _cat(client, beta)["id"]
        # Alpha's user is invisible to Beta → 422, not a cross-company rule.
        assert _rule(client, beta, categoryId=beta_cat, outletId=None, assigneeUserId=people["Andi"]).status_code == 422
