"""Integration tests — user↔outlet membership + outlet_id FK (Tier 4.1).

Tier 4.1 is deliberately inert: it adds the ownership model but does NOT yet
scope any query (that is 4.2). So these tests pin two things:
  1. the membership model works and is admin-guarded
  2. referential integrity now exists — an unknown outlet is rejected, where
     before it was silently accepted as a free-text string
"""

import pytest
from sqlalchemy.exc import IntegrityError
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_outlet@test.test", "admin")


@pytest.fixture()
def outlets(client, admin):
    """Two outlets created just for these tests. Codes are unique so they don't
    collide with the ones conftest seeds for the rest of the suite."""
    a = client.post("/api/outlets", json={"name": "Membership Outlet A", "code": "MEMA", "status": "operational"}, headers=admin)
    b = client.post("/api/outlets", json={"name": "Membership Outlet B", "code": "MEMB", "status": "operational"}, headers=admin)
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    return a.json(), b.json()


class TestMembership:
    def test_unassigned_user_has_no_outlets(self, client, admin, db):
        # outlets=[] pins the "not yet assigned" state (the suite otherwise
        # assigns non-admins to every seeded outlet for convenience).
        staff = seed_user_headers(db, "staff_om@test.test", "staff", outlets=[])
        me = client.get("/api/auth/me", headers=staff).json()
        # Deny-by-default: unassigned means "no outlets", never "all outlets".
        assert me["outlet_ids"] == []

    def test_admin_assigns_multiple_outlets(self, client, admin, db, outlets):
        a, b = outlets
        seed_user_headers(db, "area_mgr@test.test", "manager")
        uid = next(u["id"] for u in client.get("/api/auth/users", headers=admin).json()
                   if u["email"] == "area_mgr@test.test")

        res = client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": [a["id"], b["id"]]}, headers=admin)
        assert res.status_code == 200, res.text
        assert set(res.json()["outlet_ids"]) == {a["id"], b["id"]}

    def test_assignment_is_replaced_not_appended(self, client, admin, db, outlets):
        a, b = outlets
        seed_user_headers(db, "mgr_replace@test.test", "manager")
        uid = next(u["id"] for u in client.get("/api/auth/users", headers=admin).json()
                   if u["email"] == "mgr_replace@test.test")

        client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": [a["id"], b["id"]]}, headers=admin)
        res = client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": [a["id"]]}, headers=admin)
        assert res.json()["outlet_ids"] == [a["id"]]

    def test_can_clear_outlets_with_empty_list(self, client, admin, db, outlets):
        a, _ = outlets
        seed_user_headers(db, "mgr_clear@test.test", "manager")
        uid = next(u["id"] for u in client.get("/api/auth/users", headers=admin).json()
                   if u["email"] == "mgr_clear@test.test")
        client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": [a["id"]]}, headers=admin)
        res = client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": []}, headers=admin)
        assert res.json()["outlet_ids"] == []

    def test_unknown_outlet_rejected(self, client, admin, db):
        seed_user_headers(db, "mgr_bad@test.test", "manager")
        uid = next(u["id"] for u in client.get("/api/auth/users", headers=admin).json()
                   if u["email"] == "mgr_bad@test.test")
        res = client.patch(f"/api/auth/users/{uid}",
                           json={"outlet_ids": ["00000000-0000-0000-0000-000000000000"]}, headers=admin)
        assert res.status_code == 422

    def test_omitting_outlet_ids_leaves_assignment_untouched(self, client, admin, db, outlets):
        a, _ = outlets
        seed_user_headers(db, "mgr_keep@test.test", "manager")
        uid = next(u["id"] for u in client.get("/api/auth/users", headers=admin).json()
                   if u["email"] == "mgr_keep@test.test")
        client.patch(f"/api/auth/users/{uid}", json={"outlet_ids": [a["id"]]}, headers=admin)
        res = client.patch(f"/api/auth/users/{uid}", json={"name": "Renamed"}, headers=admin)
        assert res.json()["outlet_ids"] == [a["id"]]


class TestWritePathResolvesOutlet:
    """Tier 4.2a: writes must populate outlet_id, otherwise scoping (4.2b) would
    be a no-op — every new record would look 'shared' and stay globally visible."""

    def test_asset_create_populates_outlet_id(self, client, db, admin):
        from app.models.asset import Asset
        from app.models.outlet import Outlet

        res = client.post("/api/assets", json={
            "name": "Chiller Scoped", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
        }, headers=admin)
        assert res.status_code == 201, res.text

        asset = db.query(Asset).filter(Asset.id == res.json()["id"]).first()
        jakarta = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
        assert asset.outlet_id == jakarta.id

    def test_work_order_inherits_asset_outlet_id(self, client, db, admin):
        from app.models.asset import WorkOrder
        from app.models.outlet import Outlet

        asset = client.post("/api/assets", json={
            "name": "Genset Scoped", "category": "Electrical", "outlet": "Bandung", "status": "operational",
        }, headers=admin).json()
        wo = client.post("/api/work-orders", json={
            "assetId": asset["id"], "type": "corrective", "title": "Perbaikan", "priority": "high",
        }, headers=admin).json()

        row = db.query(WorkOrder).filter(WorkOrder.id == wo["id"]).first()
        bandung = db.query(Outlet).filter(Outlet.name == "Bandung").first()
        assert row.outlet_id == bandung.id

    def test_unknown_outlet_rejected_on_create(self, client, admin):
        """A typo must not become a NULL (= globally visible) record."""
        res = client.post("/api/assets", json={
            "name": "Typo Asset", "category": "HVAC",
            "outlet": "Jakartaa", "status": "operational",
        }, headers=admin)
        assert res.status_code == 422
        assert "Unknown outlet" in res.text

    def test_all_outlets_sentinel_maps_to_null(self, client, db, admin):
        """'All Outlets' is a legitimate 'not tied to one outlet' value, not a typo."""
        from app.models.issue import Issue

        res = client.post("/api/issues", json={
            "title": "Pengumuman global", "category": "Other", "priority": "low",
            "reportedBy": "HQ", "outlet": "All Outlets",
        }, headers=admin)
        assert res.status_code == 201, res.text
        issue = db.query(Issue).filter(Issue.id == res.json()["id"]).first()
        assert issue.outlet_id is None

    def test_changing_asset_outlet_updates_outlet_id(self, client, db, admin):
        from app.models.asset import Asset
        from app.models.outlet import Outlet

        asset = client.post("/api/assets", json={
            "name": "Pindah Outlet", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
        }, headers=admin).json()
        client.patch(f"/api/assets/{asset['id']}", json={"outlet": "Bandung"}, headers=admin)

        row = db.query(Asset).filter(Asset.id == asset["id"]).first()
        bandung = db.query(Outlet).filter(Outlet.name == "Bandung").first()
        assert row.outlet_id == bandung.id, "FK must follow the display name, else scoping goes stale"


class TestReferentialIntegrity:
    def test_outlet_id_fk_rejects_unknown_outlet(self, db, client, admin):
        """Before 024 an outlet was a free string; a typo created an orphan row.
        Now the FK refuses it."""
        from app.models.issue import Issue

        # SAVEPOINT: the failed INSERT must be unwound without rolling back the
        # per-test transaction that conftest owns.
        nested = db.begin_nested()
        db.add(Issue(
            number="ISS-FK-TEST", title="bad outlet", outlet="Typo Outlet",
            outlet_id="00000000-0000-0000-0000-000000000000",
            category="Maintenance", priority="medium", status="open",
        ))
        with pytest.raises(IntegrityError):
            db.flush()
        nested.rollback()

        # The outer transaction is still usable.
        assert db.query(Issue).filter(Issue.number == "ISS-FK-TEST").first() is None
