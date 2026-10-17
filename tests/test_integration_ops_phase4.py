"""Integration tests — Todo-Ops-Checklist Fase 4.

  - weekly / monthly schedules
  - manager's daily sign-off
  - PDF export for inspections
  - IoT temperature sensors
  - built-in templates per outlet type
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.asset import Asset
from app.models.ops_checklist import OpsChecklistRun
from app.models.outlet import Outlet
from app.models.temperature_log import TemperatureLog
from app.services import ops_checklist_service as svc
from app.services.ops_checklist_rules import local_date
from tests.conftest import seed_user_headers
from tests.test_integration_ops_checklist import (  # noqa: F401 — fixtures
    FRI_10_WIB, ITEMS, _answer, _open_now, _photo, _schedule, _submit, _template, admin, staff_dago,
)

UTC = timezone.utc
CHECK = [{"title": "Hood dibersihkan", "section": "Dapur"}]


def _runs(db, schedule_id):
    return (db.query(OpsChecklistRun).filter(OpsChecklistRun.schedule_id == schedule_id)
            .order_by(OpsChecklistRun.run_date).all())


# ── Weekly / monthly schedules ───────────────────────────────────────────────

class TestFrequency:
    def test_daily_is_the_default(self, client, admin):
        t = _template(client, admin)
        s = _schedule(client, admin, t["id"])
        assert (s["frequency"], s["dayOfMonth"]) == ("daily", None)

    def test_weekly_run_exists_from_when_it_opens(self, client, db, admin):
        # Deep cleaning due Sunday 18 Oct 15:00 WIB, opens 2 days before (Fri 15:00).
        t = _template(client, admin, items=CHECK, name="Deep Cleaning")
        s = _schedule(client, admin, t["id"], due="15:00", frequency="weekly", daysOfWeek=[7],
                      openMinutesBefore=2 * 1440, graceMinutes=1440)
        assert svc.generate_due_runs(db, now=FRI_10_WIB) == []           # Fri 10:00: not open yet
        created = svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(hours=6))   # Fri 16:00
        assert [r.run_date for r in created] == [date(2026, 10, 18)]
        assert svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(days=2)) == []   # Sunday: already there
        assert len(_runs(db, s["id"])) == 1

    def test_weekly_open_run_shows_today_before_its_date(self, client, db, admin, staff_dago):
        t = _template(client, admin, items=CHECK, name="Deep Cleaning")
        _schedule(client, admin, t["id"], due="15:00", frequency="weekly", daysOfWeek=[7],
                  openMinutesBefore=2 * 1440, graceMinutes=1440)
        now = FRI_10_WIB + timedelta(hours=6)
        svc.generate_due_runs(db, now=now)
        user = svc_user(db, "staff_dago_ops@test.test")
        today = svc.list_today(db, user, now=now)
        assert [r.template_name for r in today] == ["Deep Cleaning"]
        assert [r.template_name for r in svc.my_open_runs(db, user, now=now)] == ["Deep Cleaning"]

    def test_monthly_on_the_last_day_of_a_short_month(self, client, db, admin):
        t = _template(client, admin, items=CHECK, name="Kalibrasi")
        s = _schedule(client, admin, t["id"], due="14:00", frequency="monthly", dayOfMonth=31)
        assert s["dayOfMonth"] == 31
        # 30 Nov 2026 13:30 WIB
        created = svc.generate_due_runs(db, now=datetime(2026, 11, 30, 6, 30, tzinfo=UTC))
        assert [r.run_date for r in created] == [date(2026, 11, 30)]
        assert svc.generate_due_runs(db, now=datetime(2026, 11, 29, 6, 30, tzinfo=UTC)) == []

    @pytest.mark.parametrize("body", [
        {"frequency": "weekly", "daysOfWeek": [6, 7]},
        {"frequency": "weekly"},
        {"frequency": "monthly"},
        {"frequency": "monthly", "dayOfMonth": 32},
        {"frequency": "daily", "dayOfMonth": 3},
        {"frequency": "daily", "openMinutesBefore": 1000, "graceMinutes": 500},
        {"frequency": "weekly", "daysOfWeek": [1], "openMinutesBefore": 7 * 1440, "graceMinutes": 60},
        {"frequency": "yearly"},
    ])
    def test_invalid_shapes_are_422(self, client, admin, body):
        t = _template(client, admin)
        res = client.post("/api/ops-checklist-schedules", headers=admin,
                          json={"templateId": t["id"], "outlet": "Dago", "dueTime": "09:30", **body})
        assert res.status_code == 422, res.text

    def test_update_checks_the_merged_shape(self, client, admin):
        t = _template(client, admin)
        s = _schedule(client, admin, t["id"])
        res = client.patch(f"/api/ops-checklist-schedules/{s['id']}", headers=admin, json={"frequency": "monthly"})
        assert res.status_code == 422
        res = client.patch(f"/api/ops-checklist-schedules/{s['id']}", headers=admin,
                           json={"frequency": "monthly", "dayOfMonth": 1, "openMinutesBefore": 3 * 1440})
        assert res.status_code == 200, res.text
        assert (res.json()["frequency"], res.json()["dayOfMonth"], res.json()["openMinutesBefore"]) == ("monthly", 1, 4320)
        res = client.patch(f"/api/ops-checklist-schedules/{s['id']}", headers=admin,
                           json={"frequency": "daily", "openMinutesBefore": 60})
        assert res.status_code == 200, res.text
        assert res.json()["dayOfMonth"] is None


def svc_user(db, email):
    from app.models.user import User
    from app.services.auth_service import _user_to_response
    return _user_to_response(db.query(User).filter(User.email == email).one())


# ── Daily sign-off ───────────────────────────────────────────────────────────

def _past_day(client, db, h, days_ago=3, outlet="Dago"):
    """A run of a past business day that was never filled in → missed."""
    now = datetime.now(UTC)
    t = _template(client, h, items=CHECK, name=f"Opening {uuid.uuid4().hex[:6]}")
    _schedule(client, h, t["id"], outlet=outlet, due="09:30")
    when = now - timedelta(days=days_ago)
    day = local_date(when, "Asia/Jakarta")
    # 09:00 WIB that day: the run exists and is open.
    gen_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=2)
    created = svc.generate_due_runs(db, now=gen_at)
    assert created, "no run generated"
    svc.mark_missed_runs(db, now=now)
    return day


def _sign(client, h, day, outlet="Dago", note="Sudah dicek"):
    return client.post(f"/api/ops-checklists/days/{day.isoformat()}/signoff", headers=h,
                       json={"outlet": outlet, "note": note})


def _day(client, h, day, outlet="Dago"):
    res = client.get(f"/api/ops-checklists/days/{day.isoformat()}?outlet={outlet}", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


class TestSignoff:
    def test_manager_signs_a_closed_day(self, client, db, admin):
        day = _past_day(client, db, admin)
        before = _day(client, admin, day)
        assert (before["canSign"], before["signoff"], before["summary"]["missed"]) == (True, None, 1)
        assert [r["status"] for r in before["runs"]] == ["missed"]
        res = _sign(client, admin, day)
        assert res.status_code == 201, res.text
        body = res.json()
        assert (body["signedByName"], body["note"], body["businessDate"], body["outlet"]) == \
            ("Admin Ops", "Sudah dicek", day.isoformat(), "Dago")
        assert len(body["digest"]) == 64 and body["summary"]["missed"] == 1
        after = _day(client, admin, day)
        assert after["signoff"]["id"] == body["id"]
        assert (after["canSign"], after["isIntact"]) == (False, True)

    def test_signed_once(self, client, db, admin):
        day = _past_day(client, db, admin)
        assert _sign(client, admin, day).status_code == 201
        assert _sign(client, admin, day).status_code == 409

    def test_reading_added_afterwards_breaks_the_seal(self, client, db, admin):
        day = _past_day(client, db, admin)
        assert _sign(client, admin, day).status_code == 201
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        asset = Asset(number="AST-T-1", name="Chiller", category="Refrigeration", outlet="Dago", outlet_id=dago.id,
                      status="operational", temp_monitored=True, temp_min=0, temp_max=5)
        db.add(asset)
        db.flush()
        at = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=5)   # 12:00 WIB
        db.add(TemperatureLog(asset_id=asset.id, outlet="Dago", outlet_id=dago.id, value=3, in_range=True,
                              source="manual", recorded_at=at, received_at=at))
        db.flush()
        after = _day(client, admin, day)
        assert after["isIntact"] is False
        assert after["summary"]["temperatureReadings"] == 1
        assert after["signoff"]["summary"]["temperatureReadings"] == 0

    def test_day_with_a_running_checklist_is_409(self, client, db, admin):
        t = _template(client, admin, items=CHECK)
        _schedule(client, admin, t["id"])
        run = svc.generate_due_runs(db, now=FRI_10_WIB)
        r = _open_now(db, run[0].id)          # its window now spans the real clock
        res = _sign(client, admin, r.run_date)
        assert res.status_code == 409
        assert "berjalan" in res.json()["detail"]
        assert _day(client, admin, r.run_date)["canSign"] is False

    def test_future_day_is_409(self, client, admin):
        assert _sign(client, admin, date.today() + timedelta(days=3)).status_code == 409

    def test_only_managers_of_the_outlet(self, client, db, admin, staff_dago):
        day = _past_day(client, db, admin)
        assert _sign(client, staff_dago, day).status_code == 403
        bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
        mgr_bdg = seed_user_headers(db, "mgr_bdg_sign@test.test", "manager", outlets=[bdg])
        assert _sign(client, mgr_bdg, day).status_code == 404     # outlet not visible to them
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        mgr_dago = seed_user_headers(db, "mgr_dago_sign@test.test", "manager", name="Mgr Dago", outlets=[dago])
        assert _sign(client, mgr_dago, day).status_code == 201

    def test_list_by_period(self, client, db, admin):
        d1 = _past_day(client, db, admin, days_ago=3)
        d2 = _past_day(client, db, admin, days_ago=2)
        _sign(client, admin, d1)
        _sign(client, admin, d2)
        rows = client.get(f"/api/ops-checklists/signoffs?outlet=Dago&from={d1.isoformat()}&to={d1.isoformat()}",
                          headers=admin).json()
        assert [r["businessDate"] for r in rows] == [d1.isoformat()]
        rows = client.get("/api/ops-checklists/signoffs?outlet=Dago", headers=admin).json()
        assert [r["businessDate"] for r in rows] == [d2.isoformat(), d1.isoformat()]

    def test_other_outlet_day_is_hidden_from_staff(self, client, db, admin):
        day = _past_day(client, db, admin, outlet="Bandung")
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        staff = seed_user_headers(db, "staff_dago_sign@test.test", "staff", outlets=[dago])
        res = client.get(f"/api/ops-checklists/days/{day.isoformat()}?outlet=Bandung", headers=staff)
        assert res.status_code == 404


# ── PDF export ───────────────────────────────────────────────────────────────

def _export(client, h, frm, to, outlet="Dago"):
    return client.get(f"/api/ops-checklists/export.pdf?outlet={outlet}&from={frm.isoformat()}&to={to.isoformat()}",
                      headers=h)


class TestExport:
    def test_pdf_download(self, client, db, admin):
        day = _past_day(client, db, admin)
        _sign(client, admin, day)
        res = _export(client, admin, day - timedelta(days=1), day)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF")
        assert f'ops-log-Dago-{(day - timedelta(days=1)).isoformat()}_{day.isoformat()}.pdf' in \
            res.headers["content-disposition"]

    def test_content(self, client, db, admin):
        from app.services import ops_report_service as report
        from app.services.ops_signoff_service import outlet_or_404
        from tests.test_integration_ops_phase4 import svc_user
        day = _past_day(client, db, admin)
        _sign(client, admin, day, note="Semua aman")
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        asset = Asset(number="AST-T-2", name="Freezer Dapur", category="Refrigeration", outlet="Dago",
                      outlet_id=dago.id, status="operational", temp_monitored=True, temp_min=-22, temp_max=-15)
        db.add(asset)
        db.flush()
        at = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=6)   # 13:00 WIB
        db.add(TemperatureLog(asset_id=asset.id, outlet="Dago", outlet_id=dago.id, value=-12, min_value=-22,
                              max_value=-15, in_range=False, corrective_action="Pindah ke freezer 2 \U0001F44D",
                              source="manual", recorded_at=at, received_at=at))
        db.flush()
        user = svc_user(db, "admin_ops@test.test")
        data = report.report_data(db, outlet_or_404(db, "Dago", user), day, day)
        assert [d["date"] for d in data["days"]] == [day.isoformat()]
        d = data["days"][0]
        assert d["signoff"]["signedByName"] == "Admin Ops" and d["isIntact"] is False
        assert [(log["asset"], log["time"], log["inRange"]) for log in d["logs"]] == [("Freezer Dapur", "13:00", False)]
        pdf = report.render_pdf(data, compress=False)
        for text in (b"Dago", b"Freezer Dapur", b"-12.0", b"Admin Ops", b"Semua aman", b"Pindah ke",
                     b"TERLEWAT"):
            assert text in pdf, text

    def test_range_limits(self, client, admin):
        today = date.today()
        assert _export(client, admin, today, today - timedelta(days=1)).status_code == 422
        assert _export(client, admin, today - timedelta(days=31), today).status_code == 422
        assert _export(client, admin, today - timedelta(days=30), today).status_code == 200

    def test_scoped(self, client, db, admin):
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        staff = seed_user_headers(db, "staff_dago_pdf@test.test", "staff", outlets=[dago])
        today = date.today()
        assert _export(client, staff, today, today, outlet="Bandung").status_code == 404
        assert _export(client, staff, today, today).status_code == 200


# ── IoT temperature sensors ──────────────────────────────────────────────────

@pytest.fixture()
def freezer(client, admin):
    res = client.post("/api/assets", headers=admin, json={
        "name": "Freezer Sensor", "category": "Refrigeration", "outlet": "Dago",
        "tempMonitored": True, "tempMin": -22, "tempMax": -15})
    assert res.status_code == 201, res.text
    return res.json()


def _sensor(client, h, freezer, name="Probe 1"):
    res = client.post(f"/api/assets/{freezer['id']}/temperature-sensors", headers=h, json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


def _push(client, key, *readings):
    return client.post("/api/temperature-sensors/readings", headers={"X-Sensor-Key": key},
                       json={"readings": [{"value": v, "recordedAt": at.isoformat()} for v, at in readings]})


def _food_safety(db):
    from app.models.issue import Issue
    return db.query(Issue).filter(Issue.title.like("[Suhu] Freezer Sensor%")).all()


class TestSensors:
    def test_key_is_shown_once(self, client, admin, freezer):
        s = _sensor(client, admin, freezer)
        assert s["key"].startswith("rsk_") and len(s["key"]) > 30
        assert s["keyPrefix"] == s["key"][:12]
        listed = client.get(f"/api/assets/{freezer['id']}/temperature-sensors", headers=admin).json()
        assert [x["id"] for x in listed] == [s["id"]]
        assert "key" not in listed[0] and listed[0]["isActive"] is True

    def test_only_monitored_assets(self, client, admin):
        plain = client.post("/api/assets", headers=admin, json={"name": "Kompor", "category": "Kitchen", "outlet": "Dago"}).json()
        res = client.post(f"/api/assets/{plain['id']}/temperature-sensors", headers=admin, json={"name": "x"})
        assert res.status_code == 422

    def test_staff_cannot_add(self, client, admin, staff_dago, freezer):
        res = client.post(f"/api/assets/{freezer['id']}/temperature-sensors", headers=staff_dago, json={"name": "x"})
        assert res.status_code == 403

    def test_readings_are_logged(self, client, db, admin, freezer):
        s = _sensor(client, admin, freezer)
        now = datetime.now(UTC)
        res = _push(client, s["key"], (-18.2, now - timedelta(minutes=10)), (-18.0, now - timedelta(minutes=5)))
        assert res.status_code == 200, res.text
        assert (res.json()["accepted"], res.json()["duplicates"], res.json()["escalated"]) == (2, 0, False)
        logs = db.query(TemperatureLog).filter(TemperatureLog.asset_id == uuid.UUID(freezer["id"])).all()
        assert {(log.source, float(log.value), log.in_range) for log in logs} == {("sensor", -18.2, True), ("sensor", -18.0, True)}
        rows = client.get(f"/api/assets/{freezer['id']}/temperature-logs", headers=admin).json()
        assert [r["source"] for r in rows] == ["sensor", "sensor"]
        listed = client.get(f"/api/assets/{freezer['id']}/temperature-sensors", headers=admin).json()[0]
        assert listed["lastValue"] == -18.0 and listed["lastSeenAt"]

    def test_retry_is_ignored(self, client, db, admin, freezer):
        s = _sensor(client, admin, freezer)
        at = datetime.now(UTC) - timedelta(minutes=1)
        _push(client, s["key"], (-18, at))
        res = _push(client, s["key"], (-18, at), (-18, at))
        assert (res.json()["accepted"], res.json()["duplicates"]) == (0, 2)
        assert db.query(TemperatureLog).count() == 1

    def test_bad_key_and_revoked_key_are_401(self, client, admin, freezer):
        now = datetime.now(UTC)
        assert _push(client, "rsk_nope", (-18, now)).status_code == 401
        assert client.post("/api/temperature-sensors/readings", json={"readings": []}).status_code == 401
        s = _sensor(client, admin, freezer)
        assert client.delete(f"/api/assets/{freezer['id']}/temperature-sensors/{s['id']}", headers=admin).status_code == 204
        assert _push(client, s["key"], (-18, now)).status_code == 401

    def test_future_and_stale_readings_are_rejected(self, client, admin, freezer):
        s = _sensor(client, admin, freezer)
        now = datetime.now(UTC)
        res = _push(client, s["key"], (-18, now + timedelta(hours=1)), (-18, now - timedelta(hours=25)), (-18, now))
        assert res.status_code == 200
        assert res.json()["accepted"] == 1 and len(res.json()["rejected"]) == 2

    def test_short_spike_does_not_escalate(self, client, db, admin, freezer):
        s = _sensor(client, admin, freezer)
        now = datetime.now(UTC)
        res = _push(client, s["key"], (-18, now - timedelta(minutes=12)), (-10, now - timedelta(minutes=8)),
                    (-11, now - timedelta(minutes=2)))
        assert res.json()["escalated"] is False
        assert _food_safety(db) == []

    def test_sustained_excursion_escalates_once_per_cooldown(self, client, db, admin, freezer):
        s = _sensor(client, admin, freezer)
        now = datetime.now(UTC)
        res = _push(client, s["key"], (-18, now - timedelta(minutes=40)), (-10, now - timedelta(minutes=30)),
                    (-10, now - timedelta(minutes=14)))
        assert res.json()["escalated"] is True
        issues = _food_safety(db)
        assert len(issues) == 1
        # Still out 10 minutes later: within the cooldown → no new comment/notice.
        res = _push(client, s["key"], (-9, now - timedelta(minutes=4)))
        assert res.json()["escalated"] is False
        log = (db.query(TemperatureLog).filter(TemperatureLog.issue_id.isnot(None)).one())
        assert log.issue_id == issues[0].id

    def test_tenant_comes_from_the_key(self, client, db, admin, freezer):
        s = _sensor(client, admin, freezer)
        _push(client, s["key"], (-18, datetime.now(UTC)))
        log = db.query(TemperatureLog).one()
        asset = db.get(Asset, uuid.UUID(freezer["id"]))
        assert log.company_id == asset.company_id


# ── Built-in templates ───────────────────────────────────────────────────────

class TestPresets:
    def test_catalogue(self, client, admin):
        rows = client.get("/api/ops-checklist-templates/presets", headers=admin).json()
        assert [r["key"] for r in rows] == ["cafe", "resto", "cloud_kitchen"]
        resto = rows[1]
        assert resto["label"] == "Restoran"
        assert {"Opening Checklist", "Closing Checklist", "Deep Cleaning Mingguan"} <= {t["name"] for t in resto["templates"]}
        assert all(t["itemCount"] > 0 for t in resto["templates"])

    def test_apply_creates_templates_once(self, client, admin):
        res = client.post("/api/ops-checklist-templates/presets/cafe", headers=admin, json={})
        assert res.status_code == 201, res.text
        first = res.json()
        assert "Opening Checklist" in first["created"] and first["schedules"] == 0
        names = {t["name"] for t in client.get("/api/ops-checklist-templates", headers=admin).json()}
        assert set(first["created"]) <= names
        again = client.post("/api/ops-checklist-templates/presets/cafe", headers=admin, json={}).json()
        assert again["created"] == [] and set(again["skipped"]) == set(first["created"])

    def test_apply_with_outlet_schedules_them(self, client, admin):
        res = client.post("/api/ops-checklist-templates/presets/resto", headers=admin, json={"outlet": "Dago"})
        body = res.json()
        assert body["schedules"] == len(body["created"])
        rows = client.get("/api/ops-checklist-schedules?outlet=Dago", headers=admin).json()
        by_name = {r["templateName"]: r for r in rows}
        assert by_name["Opening Checklist"]["dueTime"] == "09:30"
        assert (by_name["Deep Cleaning Mingguan"]["frequency"], by_name["Deep Cleaning Mingguan"]["daysOfWeek"]) == ("weekly", [1])
        assert (by_name["Kalibrasi Termometer Bulanan"]["frequency"], by_name["Kalibrasi Termometer Bulanan"]["dayOfMonth"]) == ("monthly", 1)
        again = client.post("/api/ops-checklist-templates/presets/resto", headers=admin, json={"outlet": "Dago"}).json()
        assert again["schedules"] == 0

    def test_unknown_preset_404_and_staff_403(self, client, admin, staff_dago):
        assert client.post("/api/ops-checklist-templates/presets/warung", headers=admin, json={}).status_code == 404
        assert client.post("/api/ops-checklist-templates/presets/cafe", headers=staff_dago, json={}).status_code == 403

    def test_onboarding_step(self, client, admin):
        def step():
            steps = client.get("/api/onboarding", headers=admin).json()["steps"]
            return next(s for s in steps if s["key"] == "opsChecklist")
        assert step()["done"] is False
        client.post("/api/ops-checklist-templates/presets/cafe", headers=admin, json={})
        assert step()["done"] is True
