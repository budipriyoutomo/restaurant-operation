"""Integration tests — Guest Service API (Todo-Pilot §8). Written before the
implementation (TDD). Contract:

  POST  /api/guest-cases                 new complaint → Issue (Guest Service) + case
  GET   /api/guest-cases                 list (outlet-scoped), ?channel= ?open=true
  GET   /api/guest-cases/kpis            response / resolution KPIs
  GET   /api/guest-cases/{id}
  PATCH /api/guest-cases/{id}            guest details
  POST  /api/guest-cases/{id}/respond    first response (kept on repeat calls)
  PATCH /api/guest-cases/{id}/recovery   compensation (guest-service:manage)
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import TEST_OUTLETS, seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_guest@test.test", "admin", name="Admin Guest")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_guest@test.test", "manager", name="Manager Guest")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_guest@test.test", "staff", name="Staff Guest")


def _iso(dt):
    return dt.isoformat()


def _case(client, headers, **over):
    body = {
        "title": "Makanan dingin", "description": "Steak datang dingin", "outlet": "Jakarta",
        "priority": "high", "guestName": "Ibu Sari", "guestContact": "0812-0000-1111",
        "channel": "google-review",
    }
    body.update(over)
    res = client.post("/api/guest-cases", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _other_outlet():
    return next(n for n, _ in TEST_OUTLETS if n != "Jakarta")


# ---------------------------------------------------------------------------
# Creating complaints
# ---------------------------------------------------------------------------

class TestCreate:
    def test_creates_guest_issue_with_task_and_case(self, client, staff, admin):
        case = _case(client, staff)                      # staff can log a complaint
        assert case["guestName"] == "Ibu Sari"
        assert case["channel"] == "google-review"
        assert case["compensationType"] == "none" and case["compensationValue"] == 0
        assert case["currency"] == "IDR"
        assert case["firstResponseAt"] is None and case["firstResponseMinutes"] is None
        issue = client.get(f"/api/issues/{case['issueId']}", headers=admin).json()
        assert issue["category"] == "Guest Service"
        assert issue["number"] == case["issueNumber"]
        assert len(issue["taskIds"]) == 1

    def test_reported_at_can_be_in_the_past_not_future(self, client, staff):
        past = datetime.now(timezone.utc) - timedelta(hours=5)
        case = _case(client, staff, reportedAt=_iso(past))
        assert datetime.fromisoformat(case["reportedAt"]) == past
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        res = client.post("/api/guest-cases", json={
            "title": "x", "outlet": "Jakarta", "priority": "low", "channel": "phone", "reportedAt": _iso(future),
        }, headers=staff)
        assert res.status_code == 422

    def test_unknown_channel_is_422(self, client, staff):
        res = client.post("/api/guest-cases", json={
            "title": "x", "outlet": "Jakarta", "priority": "low", "channel": "tiktok",
        }, headers=staff)
        assert res.status_code == 422

    def test_idempotent(self, client, staff):
        h = {**staff, "Idempotency-Key": str(uuid.uuid4())}
        body = {"title": "Dobel", "outlet": "Jakarta", "priority": "low", "channel": "walk-in"}
        a = client.post("/api/guest-cases", json=body, headers=h).json()
        b = client.post("/api/guest-cases", json=body, headers=h).json()
        assert a["id"] == b["id"]

    def test_plain_guest_service_issue_also_gets_a_case(self, client, admin):
        issue = client.post("/api/issues", json={
            "title": "Komplain lewat form issue", "category": "Guest Service", "priority": "medium",
            "outlet": "Jakarta", "generateTask": True,
        }, headers=admin).json()
        cases = client.get("/api/guest-cases", headers=admin).json()
        case = next(c for c in cases if c["issueId"] == issue["id"])
        assert case["channel"] == "other"

    def test_other_categories_get_no_case(self, client, admin):
        issue = client.post("/api/issues", json={
            "title": "AC rusak", "category": "Maintenance", "priority": "medium", "outlet": "Jakarta",
        }, headers=admin).json()
        assert all(c["issueId"] != issue["id"] for c in client.get("/api/guest-cases", headers=admin).json())


# ---------------------------------------------------------------------------
# Response, resolution, recovery
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_first_response_minutes(self, client, staff):
        reported = datetime.now(timezone.utc) - timedelta(minutes=90)
        case = _case(client, staff, reportedAt=_iso(reported))
        res = client.post(f"/api/guest-cases/{case['id']}/respond", json={"note": "Telepon tamu, minta maaf"}, headers=staff)
        assert res.status_code == 200, res.text
        body = res.json()
        assert 89 <= body["firstResponseMinutes"] <= 91
        assert body["firstResponseBy"] == "Staff Guest"
        assert body["firstResponseNote"] == "Telepon tamu, minta maaf"

    def test_respond_twice_keeps_the_first(self, client, staff):
        case = _case(client, staff)
        first = client.post(f"/api/guest-cases/{case['id']}/respond", json={}, headers=staff).json()
        second = client.post(f"/api/guest-cases/{case['id']}/respond", json={"note": "lagi"}, headers=staff).json()
        assert second["firstResponseAt"] == first["firstResponseAt"]

    def test_resolution_is_stamped_when_issue_resolves_and_cleared_on_reopen(self, client, staff, admin):
        reported = datetime.now(timezone.utc) - timedelta(hours=3)
        case = _case(client, staff, reportedAt=_iso(reported))
        issue = client.get(f"/api/issues/{case['issueId']}", headers=admin).json()
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)   # roll-up

        resolved = client.get(f"/api/guest-cases/{case['id']}", headers=admin).json()
        assert resolved["resolvedAt"] is not None
        assert 179 <= resolved["resolutionMinutes"] <= 181
        # resolved without an explicit response → the resolution counts as the first response
        assert resolved["firstResponseMinutes"] == resolved["resolutionMinutes"]

        client.post(f"/api/issues/{case['issueId']}/reopen", json={"reason": "Tamu komplain lagi"}, headers=admin)
        reopened = client.get(f"/api/guest-cases/{case['id']}", headers=admin).json()
        assert reopened["resolvedAt"] is None and reopened["resolutionMinutes"] is None
        assert reopened["firstResponseAt"] is not None                 # response history stays

    def test_cancel_is_not_a_resolution(self, client, staff, admin):
        case = _case(client, staff)
        client.post(f"/api/issues/{case['issueId']}/cancel", json={"reason": "Salah input"}, headers=admin)
        assert client.get(f"/api/guest-cases/{case['id']}", headers=admin).json()["resolvedAt"] is None

    def test_recovery_by_manager(self, client, staff, manager):
        case = _case(client, staff)
        res = client.patch(f"/api/guest-cases/{case['id']}/recovery", json={
            "compensationType": "voucher", "compensationValue": 100_000, "note": "Voucher makan berikutnya",
        }, headers=manager)
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["compensationType"], body["compensationValue"], body["currency"]) == ("voucher", 100_000, "IDR")
        assert body["compensationNote"] == "Voucher makan berikutnya"

    def test_recovery_needs_manage(self, client, staff):
        case = _case(client, staff)
        res = client.patch(f"/api/guest-cases/{case['id']}/recovery", json={
            "compensationType": "discount", "compensationValue": 20_000}, headers=staff)
        assert res.status_code == 403

    def test_recovery_validation(self, client, staff, manager):
        case = _case(client, staff)
        for body in ({"compensationType": "refund", "compensationValue": 0},
                     {"compensationType": "none", "compensationValue": 5_000},
                     {"compensationType": "discount", "compensationValue": -1}):
            assert client.patch(f"/api/guest-cases/{case['id']}/recovery", json=body, headers=manager).status_code == 422

    def test_recovery_is_audited(self, client, staff, manager, admin):
        case = _case(client, staff)
        client.patch(f"/api/guest-cases/{case['id']}/recovery", json={
            "compensationType": "refund", "compensationValue": 150_000}, headers=manager)
        logs = client.get(f"/api/audit-logs?table_name=guest_cases&record_id={case['id']}", headers=admin).json()
        assert any(l["action"] == "recovery" and (l["new_value"] or {}).get("compensationValue") == 150_000 for l in logs)

    def test_edit_guest_details(self, client, staff):
        case = _case(client, staff)
        res = client.patch(f"/api/guest-cases/{case['id']}", json={"guestName": "Bu Sari W.", "channel": "whatsapp"},
                           headers=staff)
        assert res.status_code == 200
        assert (res.json()["guestName"], res.json()["channel"]) == ("Bu Sari W.", "whatsapp")


# ---------------------------------------------------------------------------
# List, scoping, KPIs
# ---------------------------------------------------------------------------

class TestListAndKpis:
    def _scoped_manager(self, db, outlet_name):
        from app.models.outlet import Outlet
        o = db.query(Outlet).filter(Outlet.name == outlet_name).one()
        return seed_user_headers(db, f"gmgr_{o.code.lower()}@test.test", "manager", outlets=[o])

    def test_filters(self, client, staff):
        a = _case(client, staff, channel="whatsapp")
        b = _case(client, staff, channel="phone")
        client.post(f"/api/guest-cases/{b['id']}/respond", json={}, headers=staff)
        ids = [c["id"] for c in client.get("/api/guest-cases?channel=whatsapp", headers=staff).json()]
        assert a["id"] in ids and b["id"] not in ids

    def test_outlet_scoping(self, client, db, admin):
        theirs = _case(client, admin, outlet=_other_outlet())
        mgr = self._scoped_manager(db, "Jakarta")
        assert client.get(f"/api/guest-cases/{theirs['id']}", headers=mgr).status_code == 404
        assert all(c["id"] != theirs["id"] for c in client.get("/api/guest-cases", headers=mgr).json())
        res = client.post("/api/guest-cases", json={
            "title": "x", "outlet": _other_outlet(), "priority": "low", "channel": "phone"}, headers=mgr)
        assert res.status_code == 403

    def test_kpis(self, client, staff, manager):
        now = datetime.now(timezone.utc)
        fast = _case(client, staff, reportedAt=_iso(now - timedelta(minutes=30)), channel="whatsapp")
        slow = _case(client, staff, reportedAt=_iso(now - timedelta(minutes=120)), channel="google-review")
        _case(client, staff, channel="google-review")                       # not responded yet
        client.post(f"/api/guest-cases/{fast['id']}/respond", json={}, headers=staff)
        client.post(f"/api/guest-cases/{slow['id']}/respond", json={}, headers=staff)
        client.patch(f"/api/guest-cases/{slow['id']}/recovery", json={
            "compensationType": "voucher", "compensationValue": 50_000}, headers=manager)

        res = client.get("/api/guest-cases/kpis", headers=staff)
        assert res.status_code == 200, res.text
        k = res.json()
        assert k["cases"] == 3 and k["responded"] == 2 and k["open"] == 3
        assert 74 <= k["avgFirstResponseMinutes"] <= 76
        assert k["targetMinutes"] == 60
        assert k["withinTargetPct"] == 50.0
        assert k["byChannel"] == {"whatsapp": 1, "google-review": 2}
        assert k["compensationTotal"] == {"IDR": 50_000}
        assert k["perOutlet"][0]["outlet"] == "Jakarta"

    def test_kpis_respect_scope(self, client, db, admin):
        _case(client, admin, outlet=_other_outlet())
        mgr = self._scoped_manager(db, "Jakarta")
        assert client.get("/api/guest-cases/kpis", headers=mgr).json()["cases"] == 0
