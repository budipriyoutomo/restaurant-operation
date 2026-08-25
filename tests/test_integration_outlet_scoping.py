"""Integration tests — cross-outlet isolation (Tier 4.2b).

This is the tier's whole point: a manager at outlet A must not be able to see or
touch outlet B's records. A miss here is a data leak between branches, not a
cosmetic bug, so these assertions are deliberately blunt.
"""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_scope@test.test", "admin")


@pytest.fixture()
def outlet_rows(db):
    from app.models.outlet import Outlet
    jkt = db.query(Outlet).filter(Outlet.name == "Jakarta").first()
    bdg = db.query(Outlet).filter(Outlet.name == "Bandung").first()
    return jkt, bdg


@pytest.fixture()
def mgr_jakarta(client, db, outlet_rows):
    jkt, _ = outlet_rows
    return seed_user_headers(db, "mgr_jkt@test.test", "manager", outlets=[jkt])


@pytest.fixture()
def mgr_bandung(client, db, outlet_rows):
    _, bdg = outlet_rows
    return seed_user_headers(db, "mgr_bdg@test.test", "manager", outlets=[bdg])


@pytest.fixture()
def jakarta_asset(client, admin):
    return client.post("/api/assets", json={
        "name": "Chiller Jakarta", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()


@pytest.fixture()
def bandung_asset(client, admin):
    return client.post("/api/assets", json={
        "name": "Chiller Bandung", "category": "HVAC", "outlet": "Bandung", "status": "operational",
    }, headers=admin).json()


class TestAssetIsolation:
    def test_manager_lists_only_own_outlet(self, client, mgr_jakarta, jakarta_asset, bandung_asset):
        names = [a["name"] for a in client.get("/api/assets", headers=mgr_jakarta).json()]
        assert "Chiller Jakarta" in names
        assert "Chiller Bandung" not in names

    def test_manager_cannot_read_other_outlet_by_id(self, client, mgr_jakarta, bandung_asset):
        res = client.get(f"/api/assets/{bandung_asset['id']}", headers=mgr_jakarta)
        # 404 not 403: a 403 would confirm the record exists.
        assert res.status_code == 404

    def test_manager_cannot_update_other_outlet(self, client, mgr_jakarta, bandung_asset):
        res = client.patch(f"/api/assets/{bandung_asset['id']}",
                           json={"name": "Hijacked"}, headers=mgr_jakarta)
        assert res.status_code == 404

    def test_manager_cannot_delete_other_outlet(self, client, mgr_jakarta, bandung_asset):
        res = client.delete(f"/api/assets/{bandung_asset['id']}", headers=mgr_jakarta)
        assert res.status_code == 404

    def test_admin_sees_every_outlet(self, client, admin, jakarta_asset, bandung_asset):
        names = [a["name"] for a in client.get("/api/assets", headers=admin).json()]
        assert {"Chiller Jakarta", "Chiller Bandung"} <= set(names)

    def test_each_manager_sees_their_own(self, client, mgr_jakarta, mgr_bandung, jakarta_asset, bandung_asset):
        jkt = [a["name"] for a in client.get("/api/assets", headers=mgr_jakarta).json()]
        bdg = [a["name"] for a in client.get("/api/assets", headers=mgr_bandung).json()]
        assert "Chiller Jakarta" in jkt and "Chiller Bandung" not in jkt
        assert "Chiller Bandung" in bdg and "Chiller Jakarta" not in bdg


class TestWorkOrderIsolation:
    def test_manager_cannot_see_other_outlet_work_order(self, client, admin, mgr_jakarta, bandung_asset):
        wo = client.post("/api/work-orders", json={
            "assetId": bandung_asset["id"], "type": "corrective",
            "title": "Servis Bandung", "priority": "high",
        }, headers=admin).json()

        assert client.get(f"/api/work-orders/{wo['id']}", headers=mgr_jakarta).status_code == 404
        listed = [w["id"] for w in client.get("/api/work-orders", headers=mgr_jakarta).json()]
        assert wo["id"] not in listed

    def test_manager_cannot_transition_other_outlet_work_order(self, client, admin, mgr_jakarta, bandung_asset):
        wo = client.post("/api/work-orders", json={
            "assetId": bandung_asset["id"], "type": "corrective",
            "title": "Servis Bandung", "priority": "high",
        }, headers=admin).json()
        res = client.patch(f"/api/work-orders/{wo['id']}/transition",
                           json={"targetStatus": "in-progress"}, headers=mgr_jakarta)
        assert res.status_code == 404


class TestIssueAndApprovalIsolation:
    """Issue is the system's source of truth, so a leak here leaks everything
    downstream (tasks, approvals, work orders)."""

    def _make_issue(self, client, headers, outlet, title, cost=None):
        body = {
            "title": title, "category": "Maintenance", "priority": "high",
            "reportedBy": "Tester", "outlet": outlet,
        }
        if cost is not None:
            body["estimatedCost"] = cost
        return client.post("/api/issues", json=body, headers=headers).json()

    def test_manager_lists_only_own_outlet_issues(self, client, admin, mgr_jakarta):
        self._make_issue(client, admin, "Jakarta", "Isu Jakarta")
        self._make_issue(client, admin, "Bandung", "Isu Bandung")
        titles = [i["title"] for i in client.get("/api/issues", headers=mgr_jakarta).json()]
        assert "Isu Jakarta" in titles
        assert "Isu Bandung" not in titles

    def test_manager_lists_only_own_outlet_approvals(self, client, admin, mgr_jakarta):
        # Above threshold => an ApprovalRequest is generated for each issue.
        self._make_issue(client, admin, "Jakarta", "Besar Jakarta", cost=3_000_000)
        self._make_issue(client, admin, "Bandung", "Besar Bandung", cost=3_000_000)
        outlets = {a["outlet"] for a in client.get("/api/approvals", headers=mgr_jakarta).json()}
        assert "Bandung" not in outlets

    def test_global_issue_visible_to_all(self, client, admin, mgr_jakarta):
        """'All Outlets' maps to outlet_id NULL — a genuinely global announcement
        stays visible, which is the documented rule."""
        client.post("/api/issues", json={
            "title": "Pengumuman HQ", "category": "Other", "priority": "low",
            "reportedBy": "HQ", "outlet": "All Outlets",
        }, headers=admin)
        titles = [i["title"] for i in client.get("/api/issues", headers=mgr_jakarta).json()]
        assert "Pengumuman HQ" in titles


class TestMultiOutletManager:
    """Area managers cover several outlets — the union must be exact: everything
    they own, nothing they don't."""

    def test_sees_union_of_assigned_outlets(self, client, db, admin, outlet_rows,
                                            jakarta_asset, bandung_asset):
        jkt, bdg = outlet_rows
        area = seed_user_headers(db, "area_mgr_scope@test.test", "manager", outlets=[jkt, bdg])
        names = [a["name"] for a in client.get("/api/assets", headers=area).json()]
        assert {"Chiller Jakarta", "Chiller Bandung"} <= set(names)

    def test_still_excluded_from_unassigned_outlet(self, client, db, admin, outlet_rows,
                                                   jakarta_asset, bandung_asset):
        jkt, _ = outlet_rows
        from app.models.outlet import Outlet
        dago = db.query(Outlet).filter(Outlet.name == "Dago").first()
        # Assigned to Jakarta + Dago, so Bandung must stay invisible.
        area = seed_user_headers(db, "area_partial@test.test", "manager", outlets=[jkt, dago])
        names = [a["name"] for a in client.get("/api/assets", headers=area).json()]
        assert "Chiller Jakarta" in names
        assert "Chiller Bandung" not in names


class TestOutletPolicyStillResolves:
    def test_outlet_specific_policy_applies_after_fk_migration(self, client, admin):
        """Policy matching moved from outlet name to outlet_id — an outlet-scoped
        policy must still win over the global default."""
        client.post("/api/approval-policies", json={
            "approvalType": "maintenance", "minAmount": 1_000_001,
            "steps": [{"order": 1, "role": "manager"}],
            "outlet": "Bandung", "isActive": True,
        }, headers=admin)

        asset = client.post("/api/assets", json={
            "name": "Boiler Bandung", "category": "Utility", "outlet": "Bandung", "status": "operational",
        }, headers=admin).json()
        issue = client.post("/api/issues", json={
            "title": "Boiler rusak", "category": "Maintenance", "priority": "high",
            "reportedBy": "Teknisi", "outlet": "Bandung",
            "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": 3_000_000,
        }, headers=admin).json()

        approval = client.get(f"/api/approvals/{issue['approvalId']}", headers=admin).json()
        roles = [s["approverRole"] for s in approval["steps"]]
        assert roles == ["manager"], "outlet-scoped policy should override the 2-step default"


class TestUnassignedUser:
    def test_unassigned_manager_sees_no_outlet_specific_data(self, client, db, admin, jakarta_asset, bandung_asset):
        """Deny-by-default: no assignment must never mean 'see everything'."""
        nobody = seed_user_headers(db, "mgr_nobody@test.test", "manager", outlets=[])
        names = [a["name"] for a in client.get("/api/assets", headers=nobody).json()]
        assert "Chiller Jakarta" not in names
        assert "Chiller Bandung" not in names


class TestNotificationFanout:
    def test_issue_notification_only_reaches_that_outlet(self, client, db, admin, mgr_jakarta, mgr_bandung):
        """Without outlet scoping every manager in the company gets paged about
        one branch's issue."""
        client.post("/api/issues", json={
            "title": "Bocor di Bandung", "category": "Maintenance", "priority": "high",
            "reportedBy": "Staf", "outlet": "Bandung",
        }, headers=admin)

        def notes(headers):
            items = client.get("/api/notifications?limit=100", headers=headers).json()
            return [n for n in items if "Bocor di Bandung" in n["message"]]

        assert len(notes(mgr_bandung)) == 1
        assert notes(mgr_jakarta) == []


class TestWriteGuard:
    """Read scoping alone is only half the story: without a write guard a manager
    could create records inside another branch's outlet and then not even see
    them, silently polluting that branch's data."""

    def test_manager_cannot_create_asset_in_other_outlet(self, client, mgr_jakarta):
        res = client.post("/api/assets", json={
            "name": "Selundupan", "category": "HVAC", "outlet": "Bandung", "status": "operational",
        }, headers=mgr_jakarta)
        # 403, not 404: outlets are public master data, and the caller asked for
        # this action explicitly.
        assert res.status_code == 403

    def test_manager_cannot_file_issue_for_other_outlet(self, client, mgr_jakarta):
        res = client.post("/api/issues", json={
            "title": "Isu titipan", "category": "Maintenance", "priority": "low",
            "reportedBy": "X", "outlet": "Bandung",
        }, headers=mgr_jakarta)
        assert res.status_code == 403

    def test_manager_cannot_create_wo_on_other_outlet_asset(self, client, admin, mgr_jakarta, bandung_asset):
        res = client.post("/api/work-orders", json={
            "assetId": bandung_asset["id"], "type": "corrective",
            "title": "WO titipan", "priority": "high",
        }, headers=mgr_jakarta)
        assert res.status_code == 403

    def test_manager_can_still_write_in_own_outlet(self, client, mgr_jakarta):
        res = client.post("/api/assets", json={
            "name": "Aset Sah", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
        }, headers=mgr_jakarta)
        assert res.status_code == 201

    def test_admin_can_write_anywhere(self, client, admin):
        res = client.post("/api/assets", json={
            "name": "Aset Admin", "category": "HVAC", "outlet": "Bandung", "status": "operational",
        }, headers=admin)
        assert res.status_code == 201


class TestSharedRows:
    def test_shared_part_visible_to_every_outlet(self, client, admin, mgr_jakarta):
        """outlet_id NULL means 'not tied to one outlet' — a shared catalogue item
        stays visible, which is the documented rule, not a leak."""
        client.post("/api/parts", json={
            "sku": "SHARED-1", "name": "Oli Universal", "stockQty": 5,
        }, headers=admin)
        skus = [p["sku"] for p in client.get("/api/parts", headers=mgr_jakarta).json()]
        assert "SHARED-1" in skus

    def test_outlet_specific_part_hidden_from_other_outlet(self, client, admin, mgr_jakarta):
        client.post("/api/parts", json={
            "sku": "BDG-1", "name": "Filter Bandung", "stockQty": 5, "outlet": "Bandung",
        }, headers=admin)
        skus = [p["sku"] for p in client.get("/api/parts", headers=mgr_jakarta).json()]
        assert "BDG-1" not in skus
