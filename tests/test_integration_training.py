"""Integration tests — training programs, enrollment, attendance (Todo-Pilot §9).

Written before the implementation (TDD). The training API is snake_case.

  POST/PATCH/DELETE /api/training-programs[/{id}]            (training:manage)
  GET   /api/training-programs/{id}/enrollments
  POST  /api/training-programs/{id}/enrollments   {user_ids}  bulk, all-or-nothing
  PATCH /api/training-programs/{id}/enrollments/{eid} {status, score, notes}
  DELETE /api/training-programs/{id}/enrollments/{eid}       only while registered
  GET   /api/training-programs/attendance                    recap by outlet / role
"""

from datetime import date, timedelta

import pytest

from tests.conftest import TEST_OUTLETS, seed_user_headers

TODAY = date.today()
OTHER = next(n for n, _ in TEST_OUTLETS if n != "Jakarta")


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_train@test.test", "admin", name="Admin Train")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_train@test.test", "manager", name="Manager Train")


def _user(client, db, email, role="staff", name=None, outlets=None):
    from app.models.user import User
    seed_user_headers(db, email, role, name=name or email.split("@")[0], outlets=outlets)
    return str(db.query(User).filter(User.email == email).one().id)


def _outlet(db, name):
    from app.models.outlet import Outlet
    return db.query(Outlet).filter(Outlet.name == name).one()


def _program(client, headers, **over):
    body = {"title": "Food Safety Level 1", "outlet": "Jakarta", "target_role": "staff",
            "scheduled_date": TODAY.isoformat(), "max_participants": 3}
    body.update(over)
    res = client.post("/api/training-programs", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _enroll(client, headers, program_id, user_ids):
    return client.post(f"/api/training-programs/{program_id}/enrollments", json={"user_ids": user_ids}, headers=headers)


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

class TestPrograms:
    def test_counts_on_program(self, client, db, admin):
        p = _program(client, admin)
        assert (p["enrolled_count"], p["attended_count"]) == (0, 0)
        _enroll(client, admin, p["id"], [_user(client, db, "a1@train.test")])
        listed = next(x for x in client.get("/api/training-programs", headers=admin).json() if x["id"] == p["id"])
        assert listed["enrolled_count"] == 1

    def test_max_participants_must_be_positive(self, client, admin):
        res = client.post("/api/training-programs", json={"title": "x", "max_participants": 0}, headers=admin)
        assert res.status_code == 422

    def test_cannot_shrink_below_enrolled(self, client, db, admin):
        p = _program(client, admin)
        _enroll(client, admin, p["id"], [_user(client, db, "s1@train.test"), _user(client, db, "s2@train.test")])
        res = client.patch(f"/api/training-programs/{p['id']}", json={"max_participants": 1}, headers=admin)
        assert res.status_code == 409

    def test_outlet_scoping(self, client, db, admin):
        theirs = _program(client, admin, outlet=OTHER)
        mgr = seed_user_headers(db, "mgr_jkt_train@test.test", "manager", outlets=[_outlet(db, "Jakarta")])
        assert all(p["id"] != theirs["id"] for p in client.get("/api/training-programs", headers=mgr).json())
        assert client.patch(f"/api/training-programs/{theirs['id']}", json={"title": "x"}, headers=mgr).status_code == 404
        res = client.post("/api/training-programs", json={"title": "x", "outlet": OTHER}, headers=mgr)
        assert res.status_code == 403

    def test_delete_removes_enrollments(self, client, db, admin):
        p = _program(client, admin)
        _enroll(client, admin, p["id"], [_user(client, db, "d1@train.test")])
        assert client.delete(f"/api/training-programs/{p['id']}", headers=admin).status_code == 204
        from app.models.training_enrollment import TrainingEnrollment
        assert db.query(TrainingEnrollment).count() == 0


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class TestEnrollment:
    def test_bulk_enroll_snapshots_role_and_outlet(self, client, db, admin):
        p = _program(client, admin)
        uid = _user(client, db, "e1@train.test", name="Eka")
        res = _enroll(client, admin, p["id"], [uid])
        assert res.status_code == 201, res.text
        (e,) = res.json()
        assert e["user_id"] == uid and e["user_name"] == "Eka"
        assert e["status"] == "registered" and e["score"] is None
        assert e["outlet"] == "Jakarta"              # program outlet
        assert e["role_name"]                         # snapshot of the role name

    def test_outlet_from_user_when_program_is_shared(self, client, db, admin):
        p = _program(client, admin, outlet=None)
        uid = _user(client, db, "e2@train.test", outlets=[_outlet(db, OTHER)])
        (e,) = _enroll(client, admin, p["id"], [uid]).json()
        assert e["outlet"] == OTHER

    def test_capacity_is_all_or_nothing(self, client, db, admin):
        p = _program(client, admin, max_participants=2)
        ids = [_user(client, db, f"c{i}@train.test") for i in range(3)]
        res = _enroll(client, admin, p["id"], ids)
        assert res.status_code == 409
        assert "2" in res.json()["detail"]
        assert client.get(f"/api/training-programs/{p['id']}/enrollments", headers=admin).json() == []
        assert _enroll(client, admin, p["id"], ids[:2]).status_code == 201
        assert _enroll(client, admin, p["id"], ids[2:]).status_code == 409   # now full

    def test_duplicate_enrollment_is_409(self, client, db, admin):
        p = _program(client, admin)
        uid = _user(client, db, "dup@train.test")
        _enroll(client, admin, p["id"], [uid])
        assert _enroll(client, admin, p["id"], [uid]).status_code == 409

    def test_unknown_or_inactive_user_is_422(self, client, db, admin):
        from app.models.user import User
        p = _program(client, admin)
        assert _enroll(client, admin, p["id"], ["00000000-0000-0000-0000-000000000000"]).status_code == 422
        uid = _user(client, db, "gone@train.test")
        db.query(User).filter(User.id == uid).update({"is_active": False})
        db.flush()
        assert _enroll(client, admin, p["id"], [uid]).status_code == 422

    def test_cannot_enroll_into_cancelled_or_completed(self, client, db, admin):
        for status in ("cancelled", "completed"):
            p = _program(client, admin)
            client.patch(f"/api/training-programs/{p['id']}", json={"status": status}, headers=admin)
            assert _enroll(client, admin, p["id"], [_user(client, db, f"x{status}@train.test")]).status_code == 409

    def test_staff_cannot_enroll(self, client, db, admin):
        staff = seed_user_headers(db, "staff_train@test.test", "staff")
        p = _program(client, admin)
        assert _enroll(client, staff, p["id"], [_user(client, db, "y@train.test")]).status_code == 403

    def test_unenroll_only_while_registered(self, client, db, admin):
        p = _program(client, admin)
        e1, e2 = _enroll(client, admin, p["id"], [_user(client, db, "u1@train.test"),
                                                  _user(client, db, "u2@train.test")]).json()
        assert client.delete(f"/api/training-programs/{p['id']}/enrollments/{e1['id']}", headers=admin).status_code == 204
        client.patch(f"/api/training-programs/{p['id']}/enrollments/{e2['id']}", json={"status": "attended"}, headers=admin)
        assert client.delete(f"/api/training-programs/{p['id']}/enrollments/{e2['id']}", headers=admin).status_code == 409


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

class TestAttendance:
    def _one(self, client, db, admin, **program):
        p = _program(client, admin, **program)
        (e,) = _enroll(client, admin, p["id"], [_user(client, db, f"att{p['id'][:6]}@train.test")]).json()
        return p, e

    def test_mark_attended_with_score(self, client, db, admin):
        p, e = self._one(client, db, admin)
        res = client.patch(f"/api/training-programs/{p['id']}/enrollments/{e['id']}",
                           json={"status": "attended", "score": 88, "notes": "Lulus"}, headers=admin)
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["status"], body["score"], body["notes"]) == ("attended", 88, "Lulus")
        assert body["marked_at"] is not None

    def test_not_before_the_training_day(self, client, db, admin):
        p, e = self._one(client, db, admin, scheduled_date=(TODAY + timedelta(days=3)).isoformat())
        res = client.patch(f"/api/training-programs/{p['id']}/enrollments/{e['id']}", json={"status": "attended"}, headers=admin)
        assert res.status_code == 409

    def test_score_only_when_attended(self, client, db, admin):
        p, e = self._one(client, db, admin)
        res = client.patch(f"/api/training-programs/{p['id']}/enrollments/{e['id']}",
                           json={"status": "no-show", "score": 50}, headers=admin)
        assert res.status_code == 422

    def test_switching_to_no_show_clears_score(self, client, db, admin):
        p, e = self._one(client, db, admin)
        url = f"/api/training-programs/{p['id']}/enrollments/{e['id']}"
        client.patch(url, json={"status": "attended", "score": 70}, headers=admin)
        body = client.patch(url, json={"status": "no-show"}, headers=admin).json()
        assert (body["status"], body["score"]) == ("no-show", None)

    def test_marking_is_audited(self, client, db, admin):
        p, e = self._one(client, db, admin)
        client.patch(f"/api/training-programs/{p['id']}/enrollments/{e['id']}", json={"status": "attended"}, headers=admin)
        logs = client.get(f"/api/audit-logs?table_name=training_enrollments&record_id={e['id']}", headers=admin).json()
        assert any(l["action"] == "attendance" for l in logs)


# ---------------------------------------------------------------------------
# Recap
# ---------------------------------------------------------------------------

class TestRecap:
    def test_recap_by_outlet_and_role(self, client, db, admin):
        p = _program(client, admin, max_participants=None)
        staff_ids = [_user(client, db, f"r{i}@train.test") for i in range(3)]
        mgr_id = _user(client, db, "rm@train.test", role="manager")
        enrolled = _enroll(client, admin, p["id"], staff_ids + [mgr_id]).json()
        by_user = {e["user_id"]: e for e in enrolled}
        mark = lambda uid, s: client.patch(f"/api/training-programs/{p['id']}/enrollments/{by_user[uid]['id']}",
                                           json={"status": s}, headers=admin)
        mark(staff_ids[0], "attended")
        mark(staff_ids[1], "no-show")
        mark(mgr_id, "attended")

        res = client.get("/api/training-programs/attendance", headers=admin)
        assert res.status_code == 200, res.text
        r = res.json()
        assert r["totals"] == {"enrolled": 4, "attended": 2, "no_show": 1, "pending": 1, "attendance_rate": 66.7}
        jakarta = next(o for o in r["by_outlet"] if o["key"] == "Jakarta")
        assert jakarta["enrolled"] == 4
        roles = {x["key"]: x for x in r["by_role"]}
        staff_role = by_user[staff_ids[0]]["role_name"]
        assert roles[staff_role]["enrolled"] == 3 and roles[staff_role]["attendance_rate"] == 50.0

    def test_recap_respects_scope(self, client, db, admin):
        p = _program(client, admin, outlet=OTHER)
        _enroll(client, admin, p["id"], [_user(client, db, "sc@train.test")])
        mgr = seed_user_headers(db, "mgr_jkt2_train@test.test", "manager", outlets=[_outlet(db, "Jakarta")])
        assert client.get("/api/training-programs/attendance", headers=mgr).json()["totals"]["enrolled"] == 0


class TestEnumFilters:
    """status is a native Postgres enum: an unknown filter value must be a 422,
    not a database error."""

    def test_unknown_program_status_filter_is_422(self, client, admin):
        assert client.get("/api/training-programs?status=bogus", headers=admin).status_code == 422

    def test_unknown_campaign_status_filter_is_422(self, client, admin):
        assert client.get("/api/campaigns?status=bogus", headers=admin).status_code == 422

    def test_create_program_and_campaign_store_enum_values(self, client, admin):
        p = _program(client, admin)
        assert client.patch(f"/api/training-programs/{p['id']}", json={"status": "ongoing"},
                            headers=admin).json()["status"] == "ongoing"
        res = client.post("/api/campaigns", json={"title": "Promo", "type": "social-media"}, headers=admin)
        assert res.status_code == 201, res.text
        assert res.json()["type"] == "social-media"
