"""Integration tests — daily ops checklist (Todo-Ops-Checklist Fase 1 §1.7).

The generator takes `now`, so dates are fixed; for answering/submitting
through the API (real clock) the run's window is moved around the real now.
"""

import io
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from PIL import Image

from app.models.issue import Issue
from app.models.issue_comment import IssueComment
from app.models.ops_checklist import OpsChecklistRun
from app.models.outlet import Outlet
from app.models.task import Task
from app.services import ops_checklist_service as svc
from tests.conftest import seed_user_headers

UTC = timezone.utc
# Friday 16 Oct 2026, 10:00 WIB.
FRI_10_WIB = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_ops@test.test", "admin", name="Admin Ops")


@pytest.fixture()
def staff_dago(client, db):
    dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
    return seed_user_headers(db, "staff_dago_ops@test.test", "staff", name="Staff Dago", outlets=[dago])


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 100, 50)).save(buf, "JPEG")
    return buf.getvalue()


ITEMS = [
    {"title": "Sabun di wastafel cuci tangan", "section": "Dapur"},
    {"title": "Toilet tamu bersih", "section": "Area tamu", "requiresPhoto": True},
    {"title": "Suhu chiller", "section": "Dapur", "itemType": "temperature", "minValue": 0, "maxValue": 4, "unit": "°C"},
]


def _template(client, h, items=ITEMS, **over):
    res = client.post("/api/ops-checklist-templates", headers=h,
                      json={"name": "Opening Checklist", "shift": "opening", "items": items, **over})
    assert res.status_code == 201, res.text
    return res.json()


def _schedule(client, h, template_id, outlet="Dago", due="09:30", **over):
    res = client.post("/api/ops-checklist-schedules", headers=h,
                      json={"templateId": template_id, "outlet": outlet, "dueTime": due, **over})
    assert res.status_code == 201, res.text
    return res.json()


def _open_now(db, run_id, *, due_in=timedelta(hours=1), grace=timedelta(hours=2)):
    """Move a run's window around the real clock (the API uses it)."""
    r = db.get(OpsChecklistRun, uuid.UUID(run_id) if isinstance(run_id, str) else run_id)
    now = datetime.now(UTC)
    r.due_at = now + due_in
    r.opens_at = r.due_at - timedelta(hours=2)
    r.closes_at = r.due_at + grace
    db.flush()
    return r


def _run(client, db, h, outlet="Dago", items=ITEMS, now=FRI_10_WIB):
    t = _template(client, h, items=items)
    _schedule(client, h, t["id"], outlet=outlet)
    created = svc.generate_due_runs(db, now=now)
    run = next(r for r in created if r.outlet == outlet)
    _open_now(db, run.id)
    return client.get(f"/api/ops-checklists/{run.id}", headers=h).json()


def _answer(client, h, run, idx, **body):
    item = run["items"][idx]
    return client.patch(f"/api/ops-checklists/{run['id']}/items/{item['id']}", json=body, headers=h)


def _photo(client, h, run, idx, **headers):
    item = run["items"][idx]
    return client.post(f"/api/ops-checklists/{run['id']}/items/{item['id']}/photos", headers={**h, **headers},
                       files={"file": ("p.jpg", _jpeg(), "image/jpeg")})


def _fill(client, h, run, *, sabun="pass", chiller=3):
    assert _answer(client, h, run, 0, result=sabun).status_code == 200
    assert _answer(client, h, run, 1, result="pass").status_code == 200
    assert _photo(client, h, run, 1).status_code == 201
    body = {"value": chiller}
    if chiller is not None and not (0 <= chiller <= 4):
        body["correctiveAction"] = "Produk dipindah ke chiller 2"
    assert _answer(client, h, run, 2, **body).status_code == 200


def _submit(client, h, run, **headers):
    return client.post(f"/api/ops-checklists/{run['id']}/submit", headers={**h, **headers})


# ── Templates & schedules ────────────────────────────────────────────────────

class TestSetup:
    def test_template_and_schedule(self, client, admin):
        t = _template(client, admin)
        assert [i["title"] for i in t["items"]] == [i["title"] for i in ITEMS]
        s = _schedule(client, admin, t["id"], daysOfWeek=[5, 1, 1])
        assert (s["outlet"], s["dueTime"], s["daysOfWeek"], s["timezone"]) == ("Dago", "09:30", [1, 5], "Asia/Jakarta")
        assert client.get("/api/ops-checklist-schedules", headers=admin).json()[0]["id"] == s["id"]

    def test_unknown_outlet_is_422_and_bad_days_are_422(self, client, admin):
        t = _template(client, admin)
        res = client.post("/api/ops-checklist-schedules", headers=admin,
                          json={"templateId": t["id"], "outlet": "Atlantis", "dueTime": "09:30"})
        assert res.status_code == 422
        res = client.post("/api/ops-checklist-schedules", headers=admin,
                          json={"templateId": t["id"], "outlet": "Dago", "dueTime": "09:30", "daysOfWeek": [8]})
        assert res.status_code == 422

    def test_check_items_take_no_range(self, client, admin):
        res = client.post("/api/ops-checklist-templates", headers=admin, json={
            "name": "X", "items": [{"title": "Lantai", "itemType": "check", "minValue": 1}]})
        assert res.status_code == 422

    def test_staff_cannot_manage(self, client, admin, staff_dago):
        assert client.post("/api/ops-checklist-templates", headers=staff_dago,
                           json={"name": "X", "items": [{"title": "a"}]}).status_code == 403
        t = _template(client, admin)
        assert client.post("/api/ops-checklist-schedules", headers=staff_dago,
                           json={"templateId": t["id"], "outlet": "Dago", "dueTime": "09:30"}).status_code == 403

    def test_manager_cannot_schedule_another_outlet(self, client, db, admin):
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        mgr = seed_user_headers(db, "mgr_dago_ops@test.test", "manager", outlets=[dago])
        t = _template(client, admin)
        res = client.post("/api/ops-checklist-schedules", headers=mgr,
                          json={"templateId": t["id"], "outlet": "Bandung", "dueTime": "09:30"})
        assert res.status_code == 403

    def test_editing_a_template_does_not_change_existing_runs(self, client, db, admin):
        run = _run(client, db, admin)
        t_id = run["templateId"]
        tpl = client.get("/api/ops-checklist-templates", headers=admin).json()[0]
        res = client.patch(f"/api/ops-checklist-templates/{t_id}", headers=admin, json={"items": [
            {"id": tpl["items"][0]["id"], "title": "Sabun & tisu"}]})
        assert res.status_code == 200
        assert [i["title"] for i in res.json()["items"]] == ["Sabun & tisu"]      # the others deactivated
        again = client.get(f"/api/ops-checklists/{run['id']}", headers=admin).json()
        assert [i["title"] for i in again["items"]] == [i["title"] for i in ITEMS]


# ── Generator ────────────────────────────────────────────────────────────────

class TestGenerator:
    def test_twice_is_one_run(self, client, db, admin):
        t = _template(client, admin)
        _schedule(client, admin, t["id"])
        first = svc.generate_due_runs(db, now=FRI_10_WIB)
        second = svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(minutes=15))
        assert len(first) == 1 and second == []
        run = first[0]
        assert run.number.startswith("OPS-") and run.run_date == date(2026, 10, 16)
        assert len(run.items) == 3 and run.status == "pending"
        # 09:30 WIB, opens 60 min before, closes 120 min after
        assert run.due_at == datetime(2026, 10, 16, 2, 30, tzinfo=UTC)

    def test_weekdays_only_skips_saturday(self, client, db, admin):
        t = _template(client, admin)
        _schedule(client, admin, t["id"], daysOfWeek=[1, 2, 3, 4, 5])
        saturday = FRI_10_WIB + timedelta(days=1) - timedelta(hours=2)    # Sat 08:00 WIB
        assert svc.generate_due_runs(db, now=saturday) == []
        assert len(svc.generate_due_runs(db, now=FRI_10_WIB - timedelta(hours=2))) == 1

    def test_wita_outlet_is_already_on_the_next_date(self, client, db, admin):
        db.query(Outlet).filter(Outlet.name == "Dago").one().timezone = "Asia/Makassar"
        db.flush()
        t = _template(client, admin)
        _schedule(client, admin, t["id"], outlet="Dago", due="09:30")
        _schedule(client, admin, t["id"], outlet="Jakarta", due="23:59")
        now = datetime(2026, 10, 16, 16, 30, tzinfo=UTC)     # 23:30 WIB, 00:30 WITA on the 17th
        runs = {r.outlet: r for r in svc.generate_due_runs(db, now=now)}
        assert runs["Dago"].run_date == date(2026, 10, 17)
        assert runs["Dago"].due_at == datetime(2026, 10, 17, 1, 30, tzinfo=UTC)
        assert runs["Jakarta"].run_date == date(2026, 10, 16)

    def test_closing_checklist_after_midnight(self, client, db, admin):
        t = _template(client, admin)
        _schedule(client, admin, t["id"], due="00:30")
        run = svc.generate_due_runs(db, now=FRI_10_WIB)[0]
        assert run.run_date == date(2026, 10, 16)
        assert run.due_at == datetime(2026, 10, 16, 17, 30, tzinfo=UTC)      # 17 Oct 00:30 WIB

    def test_window_already_closed_is_not_generated(self, client, db, admin):
        t = _template(client, admin)
        _schedule(client, admin, t["id"], due="06:00")       # closed at 08:00 WIB
        assert svc.generate_due_runs(db, now=FRI_10_WIB) == []

    def test_missed_runs(self, client, db, admin):
        t = _template(client, admin)
        _schedule(client, admin, t["id"])
        run = svc.generate_due_runs(db, now=FRI_10_WIB)[0]
        assert svc.mark_missed_runs(db, now=FRI_10_WIB) == []
        after = run.closes_at + timedelta(minutes=1)
        assert [r.id for r in svc.mark_missed_runs(db, now=after)] == [run.id]
        assert svc.mark_missed_runs(db, now=after) == []     # idempotent
        assert run.status == "missed"

    def test_run_now_endpoint(self, client, db, admin, staff_dago):
        t = _template(client, admin)
        _schedule(client, admin, t["id"], due="23:59")
        assert client.post("/api/ops-checklists/run-now", headers=staff_dago).status_code == 403
        res = client.post("/api/ops-checklists/run-now", headers=admin)
        assert res.status_code == 200 and res.json()["created"] in (0, 1)


# ── Filling in ───────────────────────────────────────────────────────────────

class TestAnswering:
    def test_staff_fills_in(self, client, db, admin, staff_dago):
        run = _run(client, db, admin)
        res = _answer(client, staff_dago, run, 0, result="pass")
        assert res.status_code == 200 and res.json()["result"] == "pass"
        assert client.get(f"/api/ops-checklists/{run['id']}", headers=staff_dago).json()["status"] == "in_progress"

    def test_temperature_result_is_computed(self, client, db, admin):
        run = _run(client, db, admin)
        assert _answer(client, admin, run, 2, value=3.5).json()["result"] == "pass"
        assert _answer(client, admin, run, 2, value=7).json()["result"] == "fail"
        assert _answer(client, admin, run, 2, value=-1.5).json()["result"] == "fail"

    def test_wrong_answer_shape_is_422(self, client, db, admin):
        run = _run(client, db, admin)
        assert _answer(client, admin, run, 0, value=3).status_code == 422
        assert _answer(client, admin, run, 2, result="pass").status_code == 422
        assert _answer(client, admin, run, 2, result="na").json()["result"] == "na"

    def test_recorded_at_in_the_future_is_422(self, client, db, admin):
        run = _run(client, db, admin)
        future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
        assert _answer(client, admin, run, 0, result="pass", recordedAt=future).status_code == 422
        recent = datetime.now(UTC) - timedelta(minutes=5)
        res = _answer(client, admin, run, 0, result="pass", recordedAt=recent.isoformat())
        assert res.status_code == 200
        assert abs(datetime.fromisoformat(res.json()["recordedAt"]) - recent) < timedelta(seconds=1)

    def test_not_yet_open_is_409(self, client, db, admin):
        run = _run(client, db, admin)
        _open_now(db, run["id"], due_in=timedelta(hours=5))
        assert _answer(client, admin, run, 0, result="pass").status_code == 409


# ── Submit ───────────────────────────────────────────────────────────────────

class TestSubmit:
    def test_incomplete_is_409_with_problems(self, client, db, admin):
        run = _run(client, db, admin)
        _answer(client, admin, run, 0, result="pass")
        _answer(client, admin, run, 1, result="pass")          # photo missing
        res = _submit(client, admin, run)
        assert res.status_code == 409
        problems = res.json()["detail"]["problems"]
        assert "Toilet tamu bersih: foto wajib" in problems
        assert "Suhu chiller: belum dijawab" in problems

    def test_failed_temperature_needs_corrective_action(self, client, db, admin):
        run = _run(client, db, admin)
        _answer(client, admin, run, 0, result="pass")
        _answer(client, admin, run, 1, result="na")
        _answer(client, admin, run, 2, value=9)
        res = _submit(client, admin, run)
        assert res.status_code == 409
        assert "Suhu chiller: tindakan koreksi wajib" in res.json()["detail"]["problems"]

    def test_on_time_submit(self, client, db, admin, staff_dago):
        run = _run(client, db, admin)
        _fill(client, staff_dago, run)
        res = _submit(client, staff_dago, run)
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["status"], body["isLate"], body["score"], body["submittedByName"]) == \
            ("submitted", False, 100.0, "Staff Dago")
        assert _answer(client, staff_dago, run, 0, result="fail").status_code == 409     # locked

    def test_late_but_before_close(self, client, db, admin):
        run = _run(client, db, admin)
        _open_now(db, run["id"], due_in=-timedelta(minutes=10))
        _fill(client, admin, run)
        assert _submit(client, admin, run).json()["isLate"] is True

    def test_after_close_the_run_is_missed(self, client, db, admin):
        run = _run(client, db, admin)
        _fill(client, admin, run)
        r = _open_now(db, run["id"], due_in=-timedelta(hours=3), grace=timedelta(hours=1))
        assert _submit(client, admin, run).status_code == 409
        db.refresh(r)
        assert r.status == "missed"

    def test_retry_with_the_same_key(self, client, db, admin):
        run = _run(client, db, admin)
        key = str(uuid.uuid4())
        a = _answer(client, admin, run, 0, result="fail", **{})
        assert a.status_code == 200
        _answer(client, admin, run, 1, result="pass")
        p1 = _photo(client, admin, run, 1, **{"Idempotency-Key": key})
        p2 = _photo(client, admin, run, 1, **{"Idempotency-Key": key})
        assert p1.json()["id"] == p2.json()["id"]
        _answer(client, admin, run, 2, value=2)
        skey = str(uuid.uuid4())
        s1 = _submit(client, admin, run, **{"Idempotency-Key": skey})
        s2 = _submit(client, admin, run, **{"Idempotency-Key": skey})
        assert s1.status_code == s2.status_code == 200
        detail = client.get(f"/api/ops-checklists/{run['id']}", headers=admin).json()
        assert len(detail["items"][1]["photos"]) == 1
        assert db.query(Issue).filter(Issue.title.like("[Checklist]%")).count() == 1


# ── Issues from failures ─────────────────────────────────────────────────────

def _checklist_issues(db):
    return db.query(Issue).filter(Issue.title.like("[Checklist]%")).all()


class TestIssues:
    def test_failed_item_raises_an_issue_with_a_task(self, client, db, admin):
        run = _run(client, db, admin)
        _fill(client, admin, run, sabun="fail")
        body = _submit(client, admin, run).json()
        issues = _checklist_issues(db)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.title == "[Checklist] Sabun di wastafel cuci tangan — Dago"
        assert issue.category == "Compliance" and str(issue.priority.value if hasattr(issue.priority, "value") else issue.priority) == "medium"
        assert run["number"] in issue.description
        assert db.query(Task).filter(Task.issue_id == issue.id).count() == 1
        assert body["items"][0]["issueNumber"] == issue.number
        assert body["score"] == 66.7

    def test_item_category_and_critical_priority(self, client, db, admin):
        fs = next(c for c in client.get("/api/categories", headers=admin).json() if c["systemKey"] == "food_safety")
        run = _run(client, db, admin, items=[{"title": "Freezer", "itemType": "temperature", "minValue": -22,
                                              "maxValue": -15, "unit": "°C", "isCritical": True,
                                              "issueCategoryId": fs["id"]}])
        _answer(client, admin, run, 0, value=-11, correctiveAction="Dipindah ke freezer 2")
        body = _submit(client, admin, run).json()
        assert body["criticalFailed"] is True
        issue = _checklist_issues(db)[0]
        assert issue.category == "Food Safety"
        assert (issue.priority.value if hasattr(issue.priority, "value") else issue.priority) == "critical"
        assert "Dipindah ke freezer 2" in issue.description and "-11 °C" in issue.description

    def test_issue_goes_to_the_routed_assignee(self, client, db, admin):
        from app.models.user import User
        seed_user_headers(db, "pic_ops@test.test", "staff", name="PIC Kepatuhan")
        pic_id = str(db.query(User).filter(User.email == "pic_ops@test.test").one().id)
        compliance = next(c for c in client.get("/api/categories", headers=admin).json() if c["systemKey"] == "compliance")
        client.post("/api/issue-assignment-rules", headers=admin,
                    json={"categoryId": compliance["id"], "outletId": None, "assigneeUserId": pic_id})
        run = _run(client, db, admin)
        _fill(client, admin, run, sabun="fail")
        _submit(client, admin, run)
        assert str(_checklist_issues(db)[0].assignee_user_id) == pic_id

    def test_failure_midway_rolls_back_everything(self, client, db, admin, monkeypatch):
        run = _run(client, db, admin, items=ITEMS + [{"title": "Lantai kering"}])
        _fill(client, admin, run, sabun="fail")
        _answer(client, admin, run, 3, result="fail")
        import app.services.issue_service as issue_service
        real, calls = issue_service.create_issue, []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(*a, **kw)

        monkeypatch.setattr(issue_service, "create_issue", flaky)
        r = db.get(OpsChecklistRun, uuid.UUID(run["id"]))
        nested = db.begin_nested()
        with pytest.raises(RuntimeError):
            svc.submit_run(db, r, str(uuid.uuid4()), "X", actor="test")
        nested.rollback()
        assert _checklist_issues(db) == []
        db.refresh(r)
        assert r.status == "in_progress"


class TestDedup:
    def _fail_again(self, client, db, admin, day, sabun="fail"):
        created = svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(days=day))
        run_id = created[0].id
        _open_now(db, run_id)
        run = client.get(f"/api/ops-checklists/{run_id}", headers=admin).json()
        _fill(client, admin, run, sabun=sabun)
        assert _submit(client, admin, run).status_code == 200
        return run

    def _first(self, client, db, admin):
        run = _run(client, db, admin)
        _fill(client, admin, run, sabun="fail")
        _submit(client, admin, run)
        return _checklist_issues(db)[0]

    def test_repeat_failure_comments_and_raises_priority_once(self, client, db, admin):
        issue = self._first(client, db, admin)
        self._fail_again(client, db, admin, 1)
        assert len(_checklist_issues(db)) == 1
        db.refresh(issue)
        assert (issue.priority.value if hasattr(issue.priority, "value") else issue.priority) == "high"
        comments = db.query(IssueComment).filter(IssueComment.issue_id == issue.id).all()
        assert len(comments) == 1 and comments[0].kind == "system"
        assert comments[0].body.startswith("Gagal lagi 17-10-2026")

        # A manager lowers it; the next failure only comments.
        issue.priority = "low"
        db.flush()
        self._fail_again(client, db, admin, 2)
        db.refresh(issue)
        assert (issue.priority.value if hasattr(issue.priority, "value") else issue.priority) == "low"
        assert db.query(IssueComment).filter(IssueComment.issue_id == issue.id).count() == 2

    def test_closed_issue_gets_a_new_one(self, client, db, admin):
        issue = self._first(client, db, admin)
        issue.status = "closed"
        db.flush()
        self._fail_again(client, db, admin, 1)
        assert len(_checklist_issues(db)) == 2

    def test_critical_issue_stays_critical(self, client, db, admin):
        issue = self._first(client, db, admin)
        issue.priority = "critical"
        db.flush()
        self._fail_again(client, db, admin, 1)
        db.refresh(issue)
        assert (issue.priority.value if hasattr(issue.priority, "value") else issue.priority) == "critical"

    def test_other_outlet_does_not_share_the_issue(self, client, db, admin):
        issue = self._first(client, db, admin)
        t_id = db.get(OpsChecklistRun, db.query(OpsChecklistRun.id).first()[0]).template_id
        _schedule(client, admin, str(t_id), outlet="Bandung")
        created = [r for r in svc.generate_due_runs(db, now=FRI_10_WIB) if r.outlet == "Bandung"]
        _open_now(db, created[0].id)
        run = client.get(f"/api/ops-checklists/{created[0].id}", headers=admin).json()
        _fill(client, admin, run, sabun="fail")
        _submit(client, admin, run)
        assert len(_checklist_issues(db)) == 2
        assert issue is not None


# ── Scoping & listing ────────────────────────────────────────────────────────

class TestScoping:
    def test_staff_cannot_see_or_fill_another_outlets_run(self, client, db, admin, staff_dago):
        run = _run(client, db, admin, outlet="Bandung")
        assert client.get(f"/api/ops-checklists/{run['id']}", headers=staff_dago).status_code == 404
        assert _answer(client, staff_dago, run, 0, result="pass").status_code == 404
        assert _submit(client, staff_dago, run).status_code == 404

    def test_list_by_date_and_outlet(self, client, db, admin, staff_dago):
        dago = _run(client, db, admin, outlet="Dago")
        _run(client, db, admin, outlet="Bandung")
        ids = [r["id"] for r in client.get("/api/ops-checklists?date=2026-10-16", headers=staff_dago).json()]
        assert ids == [dago["id"]]
        all_ = client.get("/api/ops-checklists?date=2026-10-16", headers=admin).json()
        assert {r["outlet"] for r in all_} == {"Dago", "Bandung"}
        only = client.get("/api/ops-checklists?date=2026-10-16&outlet=Bandung", headers=admin).json()
        assert [r["outlet"] for r in only] == ["Bandung"]


# ── My Work ──────────────────────────────────────────────────────────────────

class TestMyWork:
    def test_todays_open_checklist_of_my_outlet(self, client, db, admin, staff_dago):
        # Generate for the real "today" so the run is today's at the outlet.
        t = _template(client, admin)
        _schedule(client, admin, t["id"], outlet="Dago", due="12:00")
        _schedule(client, admin, t["id"], outlet="Bandung", due="12:00")
        now = datetime.now(UTC)
        tz_today_noon = datetime.combine(now.astimezone(svc.R.zone("Asia/Jakarta")).date(), time(8, 0),
                                         tzinfo=svc.R.zone("Asia/Jakarta"))
        created = svc.generate_due_runs(db, now=tz_today_noon.astimezone(UTC))
        for r in created:
            _open_now(db, r.id)
        section = client.get("/api/my-work", headers=staff_dago).json()["opsChecklists"]
        assert [i["outlet"] for i in section["items"]] == ["Dago"]
        assert section["items"][0]["itemCount"] == 3

        dago = next(r for r in created if r.outlet == "Dago")
        run = client.get(f"/api/ops-checklists/{dago.id}", headers=staff_dago).json()
        _fill(client, staff_dago, run)
        _submit(client, staff_dago, run)
        assert client.get("/api/my-work", headers=staff_dago).json()["opsChecklists"]["items"] == []

    def test_hidden_without_ops(self, client, db):
        from app.models.role import Role
        role = db.query(Role).filter(Role.key == "staff").one()
        role.permissions = {**role.permissions, "ops": "none"}
        db.flush()
        h = seed_user_headers(db, "noops@test.test", "staff")
        assert client.get("/api/my-work", headers=h).json()["opsChecklists"] is None
