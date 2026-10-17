"""Integration tests — food safety temperature log (Todo-Ops-Checklist Fase 2 §2.3).

Freezer Dago: −22 … −15 °C. A reading out of range needs a corrective action and
raises a Food Safety issue (one open issue per asset). Two excursions in a row,
or one ≥ 3 °C beyond a limit, raise a Maintenance issue + corrective WO on the
freezer — not a second one while that WO is active.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.asset import WorkOrder
from app.models.issue import Issue
from app.models.issue_comment import IssueComment
from app.models.notification import Notification
from app.models.outlet import Outlet
from app.models.temperature_log import TemperatureLog
from app.models.user import User
from app.services import ops_checklist_service as svc
from tests.conftest import seed_user_headers
from tests.test_integration_ops_checklist import FRI_10_WIB, _open_now, _schedule, _submit

UTC = timezone.utc


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_temp@test.test", "admin", name="Admin Suhu")


@pytest.fixture()
def freezer(client, admin):
    res = client.post("/api/assets", headers=admin, json={
        "name": "Freezer Dago", "category": "Refrigeration", "outlet": "Dago",
        "tempMonitored": True, "tempMin": -22, "tempMax": -15})
    assert res.status_code == 201, res.text
    return res.json()


def _template(client, h, freezer, name="Log Suhu", **item):
    res = client.post("/api/ops-checklist-templates", headers=h, json={"name": name, "items": [
        {"title": "Suhu freezer", "itemType": "temperature", "assetId": freezer["id"], **item}]})
    assert res.status_code == 201, res.text
    return res.json()


def _run(client, db, h, template_id, day=0, outlet="Dago"):
    created = svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(days=day))
    run = next(r for r in created if r.template_id == uuid.UUID(template_id) and r.outlet == outlet)
    _open_now(db, run.id)
    return client.get(f"/api/ops-checklists/{run.id}", headers=h).json()


def _read(client, h, run, value, action=None, idx=0):
    body = {"value": value}
    if action:
        body["correctiveAction"] = action
    res = client.patch(f"/api/ops-checklists/{run['id']}/items/{run['items'][idx]['id']}", json=body, headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def _issues(db, prefix):
    return db.query(Issue).filter(Issue.title.like(f"{prefix}%")).all()


def _wos(db, asset_id):
    return db.query(WorkOrder).filter(WorkOrder.asset_id == uuid.UUID(asset_id), WorkOrder.type == "corrective").all()


def _p(issue):
    return issue.priority.value if hasattr(issue.priority, "value") else issue.priority


# ── Asset setup ──────────────────────────────────────────────────────────────

class TestAssetLimits:
    def test_asset_carries_its_limits(self, client, admin, freezer):
        assert (freezer["tempMonitored"], freezer["tempMin"], freezer["tempMax"]) == (True, -22.0, -15.0)

    def test_min_must_be_below_max(self, client, admin, freezer):
        res = client.patch(f"/api/assets/{freezer['id']}", json={"tempMin": -10}, headers=admin)
        assert res.status_code == 422

    def test_only_monitored_assets_can_be_bound(self, client, admin):
        plain = client.post("/api/assets", headers=admin, json={"name": "Kompor", "category": "Kitchen", "outlet": "Dago"}).json()
        res = client.post("/api/ops-checklist-templates", headers=admin, json={"name": "X", "items": [
            {"title": "Suhu", "itemType": "temperature", "assetId": plain["id"]}]})
        assert res.status_code == 422

    def test_only_temperature_items_can_be_bound(self, client, admin, freezer):
        res = client.post("/api/ops-checklist-templates", headers=admin, json={"name": "X", "items": [
            {"title": "Pintu tertutup", "itemType": "check", "assetId": freezer["id"]}]})
        assert res.status_code == 422

    def test_schedule_must_be_at_the_assets_outlet(self, client, admin, freezer):
        t = _template(client, admin, freezer)
        res = client.post("/api/ops-checklist-schedules", headers=admin,
                          json={"templateId": t["id"], "outlet": "Bandung", "dueTime": "09:30"})
        assert res.status_code == 422

    def test_run_takes_the_assets_limits_unless_the_item_overrides(self, client, db, admin, freezer):
        t = _template(client, admin, freezer, maxValue=-18)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        item = run["items"][0]
        assert (item["minValue"], item["maxValue"], item["unit"], item["assetId"], item["assetName"]) == \
            (-22.0, -18.0, "°C", freezer["id"], "Freezer Dago")


# ── Checklist readings ───────────────────────────────────────────────────────

class TestChecklistReadings:
    def test_answer_writes_one_log_and_reanswer_updates_it(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        _read(client, admin, run, -18)
        _read(client, admin, run, -17)
        logs = db.query(TemperatureLog).filter(TemperatureLog.asset_id == uuid.UUID(freezer["id"])).all()
        assert len(logs) == 1 and float(logs[0].value) == -17 and logs[0].in_range and logs[0].source == "checklist"
        client.patch(f"/api/ops-checklists/{run['id']}/items/{run['items'][0]['id']}", json={"result": "na"}, headers=admin)
        assert db.query(TemperatureLog).filter(TemperatureLog.asset_id == uuid.UUID(freezer["id"])).count() == 0

    def test_out_of_range_needs_corrective_action_then_raises_food_safety(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        _read(client, admin, run, -13)
        res = _submit(client, admin, run)
        assert res.status_code == 409
        _read(client, admin, run, -13, action="Produk dipindah ke freezer 2")
        assert _submit(client, admin, run).status_code == 200
        issues = _issues(db, "[Suhu] Freezer Dago")
        assert len(issues) == 1 and issues[0].category == "Food Safety"
        assert "Produk dipindah ke freezer 2" in issues[0].description
        log = db.query(TemperatureLog).one()
        assert (log.in_range, log.issue_id, log.corrective_action) == (False, issues[0].id, "Produk dipindah ke freezer 2")
        assert _wos(db, freezer["id"]) == []         # one minor excursion: no WO yet

    def test_two_in_a_row_raise_maintenance_and_a_wo_once(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        for day, value in ((0, -13), (1, -14), (2, -13.5)):
            run = _run(client, db, admin, t["id"], day=day)
            _read(client, admin, run, value, action="Pintu ditutup rapat")
            assert _submit(client, admin, run).status_code == 200
        assert len(_issues(db, "[Suhu] Freezer Dago")) == 1                 # food safety: deduped per asset
        maintenance = _issues(db, "[Suhu] Perbaikan Freezer Dago")
        assert len(maintenance) == 1 and maintenance[0].category == "Maintenance"
        wos = _wos(db, freezer["id"])
        assert len(wos) == 1 and wos[0].issue_id == maintenance[0].id
        # Third excursion while the WO is active: a comment, not a second WO.
        assert db.query(IssueComment).filter(IssueComment.issue_id == maintenance[0].id).count() == 1

    def test_a_normal_reading_in_between_resets_the_count(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        for day, value in ((0, -13), (1, -18), (2, -13)):
            run = _run(client, db, admin, t["id"], day=day)
            _read(client, admin, run, value, action="Dicek" if value > -15 else None)
            assert _submit(client, admin, run).status_code == 200
        assert _wos(db, freezer["id"]) == []

    def test_one_major_excursion_is_enough_for_a_wo(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        _read(client, admin, run, -10, action="Semua produk dipindah")      # 5 °C above max
        _submit(client, admin, run)
        assert len(_wos(db, freezer["id"])) == 1

    def test_same_freezer_in_two_templates_shares_one_issue(self, client, db, admin, freezer):
        a = _template(client, admin, freezer, name="Opening")
        b = _template(client, admin, freezer, name="Log Suhu")
        _schedule(client, admin, a["id"])
        _schedule(client, admin, b["id"], due="10:30")
        run_a = _run(client, db, admin, a["id"])
        run_b = client.get(f"/api/ops-checklists/{db.query(svc.OpsChecklistRun).filter(svc.OpsChecklistRun.template_id == uuid.UUID(b['id'])).one().id}", headers=admin).json()
        _open_now(db, run_b["id"])
        _read(client, admin, run_a, -14, action="Dicek")
        _submit(client, admin, run_a)
        _read(client, admin, run_b, -14.5, action="Dicek lagi")
        _submit(client, admin, run_b)
        issues = _issues(db, "[Suhu] Freezer Dago")
        assert len(issues) == 1
        assert db.query(IssueComment).filter(IssueComment.issue_id == issues[0].id).count() == 1

    def test_changing_the_asset_limits_keeps_old_logs(self, client, db, admin, freezer):
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        _read(client, admin, run, -16)
        client.patch(f"/api/assets/{freezer['id']}", json={"tempMax": -17}, headers=admin)
        log = db.query(TemperatureLog).one()
        db.refresh(log)
        assert (log.in_range, float(log.max_value)) == (True, -15.0)

    def test_managers_of_the_outlet_are_notified(self, client, db, admin, freezer):
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
        seed_user_headers(db, "mgr_dago_temp@test.test", "manager", outlets=[dago])
        seed_user_headers(db, "mgr_bdg_temp@test.test", "manager", outlets=[bdg])
        t = _template(client, admin, freezer)
        _schedule(client, admin, t["id"])
        run = _run(client, db, admin, t["id"])
        _read(client, admin, run, -13, action="Dicek")
        _submit(client, admin, run)
        def count(email):
            uid = db.query(User).filter(User.email == email).one().id
            return db.query(Notification).filter(Notification.user_id == uid, Notification.title.like("Suhu%")).count()
        assert count("mgr_dago_temp@test.test") == 1
        assert count("mgr_bdg_temp@test.test") == 0


# ── Manual readings & the log endpoint ───────────────────────────────────────

class TestManual:
    def _post(self, client, h, freezer, **body):
        return client.post(f"/api/assets/{freezer['id']}/temperature-logs", json=body, headers=h)

    def test_in_range(self, client, db, admin, freezer):
        res = self._post(client, admin, freezer, value=-18)
        assert res.status_code == 201
        body = res.json()
        assert (body["inRange"], body["source"], body["minValue"], body["maxValue"]) == (True, "manual", -22.0, -15.0)
        assert _issues(db, "[Suhu]") == []

    def test_out_of_range_needs_an_action_and_escalates(self, client, db, admin, freezer):
        assert self._post(client, admin, freezer, value=-13).status_code == 409
        res = self._post(client, admin, freezer, value=-13, correctiveAction="Dipindah")
        assert res.status_code == 201 and res.json()["issueId"]
        issue = _issues(db, "[Suhu] Freezer Dago")[0]
        assert _p(issue) == "high"

    def test_not_monitored_is_422(self, client, admin):
        plain = client.post("/api/assets", headers=admin, json={"name": "Kompor", "category": "Kitchen", "outlet": "Dago"}).json()
        assert client.post(f"/api/assets/{plain['id']}/temperature-logs", json={"value": 3}, headers=admin).status_code == 422

    def test_idempotent(self, client, db, admin, freezer):
        key = {"Idempotency-Key": str(uuid.uuid4())}
        a = client.post(f"/api/assets/{freezer['id']}/temperature-logs", json={"value": -18}, headers={**admin, **key})
        b = client.post(f"/api/assets/{freezer['id']}/temperature-logs", json={"value": -18}, headers={**admin, **key})
        assert a.json()["id"] == b.json()["id"]
        assert db.query(TemperatureLog).count() == 1

    def test_future_reading_is_422(self, client, admin, freezer):
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert self._post(client, admin, freezer, value=-18, recordedAt=future).status_code == 422

    def test_list_last_days_and_scoping(self, client, db, admin, freezer):
        self._post(client, admin, freezer, value=-18)
        self._post(client, admin, freezer, value=-19)
        rows = client.get(f"/api/assets/{freezer['id']}/temperature-logs?days=30", headers=admin).json()
        assert [r["value"] for r in rows] == [-18.0, -19.0]
        bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
        staff_bdg = seed_user_headers(db, "staff_bdg_temp@test.test", "staff", outlets=[bdg])
        assert client.get(f"/api/assets/{freezer['id']}/temperature-logs", headers=staff_bdg).status_code == 404
        assert client.post(f"/api/assets/{freezer['id']}/temperature-logs", json={"value": -18},
                           headers=staff_bdg).status_code == 404
