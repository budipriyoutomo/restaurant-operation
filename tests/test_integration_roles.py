"""Integration tests — dynamic roles (migration 031).

A role decides two things: which modules a user may use (none/view/manage per
module) and which outlets' data they see (all, the role's default outlets, or
the user's personal override).
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_roles@test.test", "admin")


@pytest.fixture()
def outlets(db):
    from app.models.outlet import Outlet
    jkt = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
    bdg = db.query(Outlet).filter(Outlet.name == "Bandung").first()
    return jkt, bdg


@pytest.fixture()
def assets(client, admin):
    for name, outlet in (("Oven Jakarta", "Jakarta"), ("Oven Bandung", "Bandung")):
        res = client.post("/api/assets", json={
            "name": name, "category": "Equipment", "outlet": outlet, "status": "operational",
        }, headers=admin)
        assert res.status_code == 201, res.text


def _create_role(client, headers, **body):
    body.setdefault("name", body["key"].title())
    return client.post("/api/roles", json=body, headers=headers)


def _role_user(db, email, role_key, outlets=None):
    """Seed a user with a custom role. outlets=None keeps no personal override."""
    headers = seed_user_headers(db, email, role_key, outlets=outlets or [])
    return headers


class TestModulePermissions:
    def test_me_exposes_resolved_permissions(self, client, db, admin):
        _create_role(client, admin, key="technician",
                     permissions={"cmms": "manage", "assets": "view"})
        h = _role_user(db, "tech@test.test", "technician")
        me = client.get("/api/auth/me", headers=h).json()
        assert me["permissions"]["cmms"] == "manage"
        assert me["permissions"]["assets"] == "view"
        assert me["permissions"]["users"] == "none"

    def test_view_allows_read_but_not_write(self, client, db, admin, assets):
        _create_role(client, admin, key="auditor", permissions={"assets": "view"}, all_outlets=True)
        h = _role_user(db, "auditor@test.test", "auditor")
        assert client.get("/api/assets", headers=h).status_code == 200
        res = client.post("/api/assets", json={
            "name": "X", "category": "Equipment", "outlet": "Jakarta", "status": "operational",
        }, headers=h)
        assert res.status_code == 403

    def test_no_permission_blocks_module(self, client, db, admin):
        _create_role(client, admin, key="marketer", permissions={"marketing": "manage"})
        h = _role_user(db, "marketer@test.test", "marketer")
        assert client.get("/api/campaigns", headers=h).status_code == 200
        assert client.get("/api/assets", headers=h).status_code == 403
        assert client.get("/api/issues", headers=h).status_code == 403

    def test_permission_change_applies_immediately(self, client, db, admin):
        _create_role(client, admin, key="temp", permissions={})
        h = _role_user(db, "temp@test.test", "temp")
        assert client.get("/api/campaigns", headers=h).status_code == 403
        client.patch("/api/roles/temp", json={"permissions": {"marketing": "view"}}, headers=admin)
        assert client.get("/api/campaigns", headers=h).status_code == 200

    def test_view_only_module_rejects_manage(self, client, admin):
        res = _create_role(client, admin, key="bad", permissions={"dashboard": "manage"})
        assert res.status_code == 422

    def test_unknown_module_rejected(self, client, admin):
        res = _create_role(client, admin, key="bad", permissions={"nope": "view"})
        assert res.status_code == 422

    def test_default_roles_keep_old_behaviour(self, client, db):
        staff = seed_user_headers(db, "staff_roles@test.test", "staff")
        manager = seed_user_headers(db, "mgr_roles@test.test", "manager")
        assert client.get("/api/assets", headers=staff).status_code == 200
        assert client.get("/api/audit-logs", headers=staff).status_code == 403
        assert client.get("/api/audit-logs", headers=manager).status_code == 200
        assert client.post("/api/outlets", json={"name": "N", "code": "NN"}, headers=manager).status_code == 403


class TestOutletAccess:
    def _names(self, client, h):
        return {a["name"] for a in client.get("/api/assets", headers=h).json()}

    def test_role_default_outlets(self, client, db, admin, outlets, assets):
        jkt, _ = outlets
        _create_role(client, admin, key="jkt-staff", permissions={"assets": "view"},
                     outlet_ids=[str(jkt.id)])
        h = _role_user(db, "jkt@test.test", "jkt-staff")
        names = self._names(client, h)
        assert "Oven Jakarta" in names and "Oven Bandung" not in names

    def test_all_outlets_role(self, client, db, admin, assets):
        _create_role(client, admin, key="regional", permissions={"assets": "view"}, all_outlets=True)
        h = _role_user(db, "regional@test.test", "regional")
        assert {"Oven Jakarta", "Oven Bandung"} <= self._names(client, h)

    def test_user_override_beats_role(self, client, db, admin, outlets, assets):
        jkt, bdg = outlets
        _create_role(client, admin, key="jkt-staff", permissions={"assets": "view"},
                     outlet_ids=[str(jkt.id)])
        h = _role_user(db, "moved@test.test", "jkt-staff", outlets=[bdg])
        names = self._names(client, h)
        assert "Oven Bandung" in names and "Oven Jakarta" not in names
        me = client.get("/api/auth/me", headers=h).json()
        assert me["all_outlets"] is False
        assert me["effective_outlet_ids"] == [str(bdg.id)]

    def test_no_outlets_sees_nothing(self, client, db, admin, assets):
        _create_role(client, admin, key="nowhere", permissions={"assets": "view"})
        h = _role_user(db, "nowhere@test.test", "nowhere")
        assert not ({"Oven Jakarta", "Oven Bandung"} & self._names(client, h))


class TestRoleManagement:
    def test_admin_role_cannot_be_restricted(self, client, admin):
        res = client.patch("/api/roles/admin", json={"permissions": {}}, headers=admin)
        assert res.status_code == 400

    def test_system_role_cannot_be_deleted(self, client, admin):
        assert client.delete("/api/roles/staff", headers=admin).status_code == 400

    def test_role_in_use_cannot_be_deleted(self, client, db, admin):
        _create_role(client, admin, key="busy", permissions={})
        _role_user(db, "busy@test.test", "busy")
        assert client.delete("/api/roles/busy", headers=admin).status_code == 409

    def test_unused_role_can_be_deleted(self, client, admin):
        _create_role(client, admin, key="gone", permissions={})
        assert client.delete("/api/roles/gone", headers=admin).status_code == 204

    def test_assign_unknown_role_rejected(self, client, db, admin):
        target = seed_user_headers(db, "t@test.test", "staff")
        uid = client.get("/api/auth/me", headers=target).json()["id"]
        res = client.patch(f"/api/auth/users/{uid}", json={"role": "ghost"}, headers=admin)
        assert res.status_code == 422


class TestPrivilegeEscalation:
    @pytest.fixture()
    def hr(self, client, db, admin, outlets):
        jkt, _ = outlets
        _create_role(client, admin, key="hr", outlet_ids=[str(jkt.id)],
                     permissions={"users": "manage", "issues": "view"})
        return _role_user(db, "hr@test.test", "hr")

    def test_cannot_create_role_above_own_rights(self, client, hr):
        res = _create_role(client, hr, key="sneaky", permissions={"master-data": "manage"})
        assert res.status_code == 403

    def test_cannot_grant_all_outlets(self, client, hr):
        res = _create_role(client, hr, key="sneaky", permissions={"issues": "view"}, all_outlets=True)
        assert res.status_code == 403

    def test_can_create_role_within_own_rights(self, client, hr):
        res = _create_role(client, hr, key="reporter", permissions={"issues": "view"})
        assert res.status_code == 201, res.text

    def test_cannot_assign_admin_role(self, client, db, hr):
        target = seed_user_headers(db, "victim@test.test", "staff")
        uid = client.get("/api/auth/me", headers=target).json()["id"]
        res = client.patch(f"/api/auth/users/{uid}", json={"role": "admin"}, headers=hr)
        assert res.status_code == 403

    def test_cannot_deactivate_admin(self, client, db, hr, admin):
        uid = client.get("/api/auth/me", headers=admin).json()["id"]
        res = client.patch(f"/api/auth/users/{uid}", json={"is_active": False}, headers=hr)
        assert res.status_code == 403

    def test_cannot_edit_own_role(self, client, hr):
        res = client.patch("/api/roles/hr", json={"permissions": {"users": "manage", "settings": "manage"}},
                           headers=hr)
        assert res.status_code == 403
