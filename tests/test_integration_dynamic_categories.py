"""Integration tests — dynamic issue categories (Todo-Ops-Checklist P1).

Written before the implementation (TDD). Contract:

  - Issue categories are master data (`categories`), not a Postgres enum.
  - Every company has the system categories (system_key), Food Safety included.
  - Behaviour comes from `module` (maintenance → corrective WO, guest_service →
    guest case, …), never from the name: renaming "Maintenance" keeps WOs.
  - System categories can be renamed but not deleted, deactivated, or moved to
    another module.
  - POST /api/issues accepts `category` (name, as before) or `categoryId`.
"""

import uuid

import pytest

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_cat@test.test", "admin", name="Admin Cat")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_cat@test.test", "staff", name="Staff Cat")


def _categories(client, headers):
    res = client.get("/api/categories", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _by_key(client, headers, key):
    return next(c for c in _categories(client, headers) if c["systemKey"] == key)


def _issue(client, headers, **over):
    body = {"title": "Sesuatu rusak", "description": "x", "outlet": "Jakarta", "category": "Maintenance",
            "priority": "medium", "generateTask": True, **over}
    return client.post("/api/issues", json=body, headers=headers)


def _new_category(client, headers, **over):
    body = {"name": "Kebersihan", "description": "Area bersih", **over}
    res = client.post("/api/categories", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ── System categories ────────────────────────────────────────────────────────

class TestSystemCategories:
    def test_every_company_has_the_system_categories(self, client, admin):
        keys = {c["systemKey"] for c in _categories(client, admin) if c["systemKey"]}
        assert {"maintenance", "it_support", "compliance", "training", "procurement", "marketing",
                "asset_purchase", "guest_service", "other", "food_safety"} <= keys

    def test_response_carries_module_and_defaults(self, client, admin):
        m = _by_key(client, admin, "maintenance")
        assert m["name"] == "Maintenance"
        assert m["module"] == "maintenance"
        assert m["isSystem"] is True
        assert m["isActive"] is True
        assert m["defaultGenerateTask"] is True
        assert m["defaultGenerateApproval"] is False
        assert m["approvalType"] == "maintenance"
        assert m["type"] == "maintenance"          # legacy field, derived from module
        fs = _by_key(client, admin, "food_safety")
        assert (fs["module"], fs["type"]) == ("compliance", "operations")

    def test_system_category_cannot_be_deleted(self, client, admin):
        m = _by_key(client, admin, "maintenance")
        assert client.delete(f"/api/categories/{m['id']}", headers=admin).status_code == 409
        assert any(c["id"] == m["id"] for c in _categories(client, admin))

    def test_system_category_cannot_be_deactivated(self, client, admin):
        m = _by_key(client, admin, "guest_service")
        res = client.patch(f"/api/categories/{m['id']}", json={"isActive": False}, headers=admin)
        assert res.status_code == 409

    def test_system_category_module_is_locked(self, client, admin):
        m = _by_key(client, admin, "guest_service")
        res = client.patch(f"/api/categories/{m['id']}", json={"module": "general"}, headers=admin)
        assert res.status_code == 409

    def test_system_category_defaults_can_be_edited(self, client, admin):
        m = _by_key(client, admin, "it_support")
        res = client.patch(f"/api/categories/{m['id']}", json={"defaultGenerateApproval": True,
                                                                "approvalType": "procurement", "icon": "laptop"},
                           headers=admin)
        assert res.status_code == 200, res.text
        assert (res.json()["defaultGenerateApproval"], res.json()["approvalType"], res.json()["icon"]) == \
            (True, "procurement", "laptop")


# ── Rename keeps behaviour ───────────────────────────────────────────────────

class TestRename:
    def test_renamed_maintenance_still_creates_a_work_order(self, client, admin):
        m = _by_key(client, admin, "maintenance")
        res = client.patch(f"/api/categories/{m['id']}", json={"name": "Perbaikan"}, headers=admin)
        assert res.status_code == 200, res.text

        res = _issue(client, admin, category="Perbaikan", generateWorkOrder=True)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["category"] == "Perbaikan"
        assert body["categoryId"] == m["id"]
        assert body["categoryModule"] == "maintenance"
        assert body["workOrderId"] is not None

    def test_rename_updates_existing_issues(self, client, admin):
        issue = _issue(client, admin).json()
        m = _by_key(client, admin, "maintenance")
        client.patch(f"/api/categories/{m['id']}", json={"name": "Perbaikan"}, headers=admin)
        assert client.get(f"/api/issues/{issue['id']}", headers=admin).json()["category"] == "Perbaikan"

    def test_old_name_is_rejected_after_rename(self, client, admin):
        m = _by_key(client, admin, "maintenance")
        client.patch(f"/api/categories/{m['id']}", json={"name": "Perbaikan"}, headers=admin)
        assert _issue(client, admin, category="Maintenance").status_code == 422

    def test_duplicate_active_name_is_409(self, client, admin):
        assert client.post("/api/categories", json={"name": "maintenance"}, headers=admin).status_code == 409
        other = _by_key(client, admin, "other")
        res = client.patch(f"/api/categories/{other['id']}", json={"name": "Maintenance"}, headers=admin)
        assert res.status_code == 409


# ── Custom categories ────────────────────────────────────────────────────────

class TestCustomCategories:
    def test_custom_general_category_creates_a_task(self, client, admin):
        cat = _new_category(client, admin)
        assert (cat["module"], cat["isSystem"], cat["systemKey"]) == ("general", False, None)
        res = _issue(client, admin, category="Kebersihan")
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["categoryId"] == cat["id"]
        assert len(body["taskIds"]) == 1

    def test_custom_category_in_the_maintenance_module_creates_a_work_order(self, client, admin):
        cat = _new_category(client, admin, name="HVAC", module="maintenance")
        res = _issue(client, admin, category="HVAC", generateWorkOrder=True)
        assert res.status_code == 201, res.text
        assert res.json()["workOrderId"] is not None

    def test_work_order_flag_is_ignored_outside_the_maintenance_module(self, client, admin):
        _new_category(client, admin)
        res = _issue(client, admin, category="Kebersihan", generateWorkOrder=True)
        assert res.status_code == 201
        assert res.json()["workOrderId"] is None

    def test_custom_guest_service_module_opens_a_guest_case(self, client, admin, db):
        from app.models.guest_case import GuestCase
        _new_category(client, admin, name="Keluhan Delivery", module="guest_service")
        issue = _issue(client, admin, category="Keluhan Delivery").json()
        assert db.query(GuestCase).filter(GuestCase.issue_id == uuid.UUID(issue["id"])).count() == 1

    def test_approval_uses_the_category_approval_type(self, client, admin, db):
        from app.models.approval import ApprovalRequest
        _new_category(client, admin, name="Renovasi", approvalType="asset-purchase", defaultGenerateApproval=True)
        issue = _issue(client, admin, category="Renovasi", generateApproval=True, approvalAmount=1_000_000).json()
        appr = db.query(ApprovalRequest).filter(ApprovalRequest.issue_id == uuid.UUID(issue["id"])).one()
        assert (appr.type.value if hasattr(appr.type, "value") else appr.type) == "asset-purchase"

    def test_legacy_type_field_maps_to_module(self, client, admin):
        cat = _new_category(client, admin, name="Plumbing", type="maintenance")
        assert (cat["module"], cat["type"]) == ("maintenance", "maintenance")

    def test_invalid_module_is_422(self, client, admin):
        res = client.post("/api/categories", json={"name": "X", "module": "kitchen"}, headers=admin)
        assert res.status_code == 422

    def test_inactive_category_cannot_be_used(self, client, admin):
        cat = _new_category(client, admin)
        res = client.patch(f"/api/categories/{cat['id']}", json={"isActive": False}, headers=admin)
        assert res.status_code == 200 and res.json()["isActive"] is False
        assert _issue(client, admin, category="Kebersihan").status_code == 422
        # Still listed in master data so it can be reactivated.
        assert any(c["id"] == cat["id"] for c in _categories(client, admin))

    def test_custom_category_can_be_deleted_and_issues_keep_their_name(self, client, admin):
        cat = _new_category(client, admin)
        issue = _issue(client, admin, category="Kebersihan").json()
        assert client.delete(f"/api/categories/{cat['id']}", headers=admin).status_code == 204
        assert client.get(f"/api/issues/{issue['id']}", headers=admin).json()["category"] == "Kebersihan"

    def test_staff_cannot_manage_categories(self, client, staff):
        assert client.post("/api/categories", json={"name": "X"}, headers=staff).status_code == 403


# ── Creating issues ──────────────────────────────────────────────────────────

class TestCreateIssue:
    def test_by_category_id_only(self, client, admin):
        fs = _by_key(client, admin, "food_safety")
        body = {"title": "Freezer hangat", "outlet": "Jakarta", "categoryId": fs["id"], "priority": "high"}
        res = client.post("/api/issues", json=body, headers=admin)
        assert res.status_code == 201, res.text
        assert (res.json()["category"], res.json()["categoryModule"]) == ("Food Safety", "compliance")

    def test_unknown_category_is_422(self, client, admin):
        assert _issue(client, admin, category="Tidak Ada").status_code == 422

    def test_missing_category_is_422(self, client, admin):
        body = {"title": "x", "outlet": "Jakarta", "priority": "low"}
        assert client.post("/api/issues", json=body, headers=admin).status_code == 422

    def test_category_name_is_case_insensitive(self, client, admin):
        res = _issue(client, admin, category="guest service")
        assert res.status_code == 201, res.text
        assert res.json()["category"] == "Guest Service"


# ── Filtering ────────────────────────────────────────────────────────────────

class TestFilter:
    def test_filter_by_module_spans_categories(self, client, admin):
        _new_category(client, admin, name="HVAC", module="maintenance")
        a = _issue(client, admin, category="Maintenance").json()
        b = _issue(client, admin, category="HVAC").json()
        c = _issue(client, admin, category="Marketing").json()
        ids = {i["id"] for i in client.get("/api/issues?module=maintenance", headers=admin).json()}
        assert {a["id"], b["id"]} <= ids
        assert c["id"] not in ids

    def test_filter_by_category_name_still_works_after_rename(self, client, admin):
        issue = _issue(client, admin).json()
        m = _by_key(client, admin, "maintenance")
        client.patch(f"/api/categories/{m['id']}", json={"name": "Perbaikan"}, headers=admin)
        ids = {i["id"] for i in client.get("/api/issues?category=Perbaikan", headers=admin).json()}
        assert issue["id"] in ids


# ── Tenancy ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def beta_admin(client, db):
    from app.models.outlet import Outlet
    from app.services.category_service import ensure_system_categories
    from app.services.role_service import ensure_default_roles
    with bypass_tenant(db):
        company = Company(name="PT Beta Cat", slug=f"beta-cat-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        ensure_system_categories(db)
        db.add(Outlet(name="Beta Dago", code="BDG", status="operational"))
        db.flush()
        return seed_user_headers(db, "admin@betacat.test", "admin", name="Beta Admin")


class TestTenancy:
    def test_custom_category_is_invisible_to_another_company(self, client, admin, beta_admin):
        cat = _new_category(client, admin, name="Alpha Only")
        assert all(c["id"] != cat["id"] for c in _categories(client, beta_admin))

    def test_another_companys_category_id_is_422(self, client, admin, beta_admin):
        beta_fs = _by_key(client, beta_admin, "food_safety")
        body = {"title": "x", "outlet": "Jakarta", "categoryId": beta_fs["id"], "priority": "low"}
        assert client.post("/api/issues", json=body, headers=admin).status_code == 422

    def test_same_name_is_allowed_in_two_companies(self, client, admin, beta_admin):
        _new_category(client, admin, name="Kebersihan")
        _new_category(client, beta_admin, name="Kebersihan")
