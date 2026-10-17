"""Integration tests — missed/due-soon notifications and compliance KPIs
(Todo-Ops-Checklist Fase 3). Run states are set directly; the generator and
the API are covered in test_integration_ops_checklist.py."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification
from app.models.ops_checklist import OpsChecklistRun
from app.models.outlet import Outlet
from app.models.temperature_log import TemperatureLog
from app.models.user import User
from app.services import ops_checklist_service as svc
from tests.conftest import seed_user_headers
from tests.test_integration_ops_checklist import FRI_10_WIB, _schedule, _template

UTC = timezone.utc


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_kpi@test.test", "admin", name="Admin KPI")


@pytest.fixture()
def managers(client, db):
    dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
    bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
    return {
        "dago": seed_user_headers(db, "mgr_dago_kpi@test.test", "manager", outlets=[dago]),
        "bdg": seed_user_headers(db, "mgr_bdg_kpi@test.test", "manager", outlets=[bdg]),
        "staff_dago": seed_user_headers(db, "staff_dago_kpi@test.test", "staff", outlets=[dago]),
    }


def _notes(db, email, like):
    uid = db.query(User).filter(User.email == email).one().id
    return db.query(Notification).filter(Notification.user_id == uid, Notification.title.like(like)).count()


def _runs(client, db, admin, outlets=("Dago",), days=(0,)):
    t = _template(client, admin)
    for o in outlets:
        _schedule(client, admin, t["id"], outlet=o)
    runs = []
    for d in days:
        runs += svc.generate_due_runs(db, now=FRI_10_WIB + timedelta(days=d))
    return runs


# ── Missed ───────────────────────────────────────────────────────────────────

class TestMissed:
    def test_managers_of_the_outlet_are_told_once(self, client, db, admin, managers):
        run = _runs(client, db, admin)[0]
        after = run.closes_at + timedelta(minutes=1)
        svc.mark_missed_runs(db, now=after)
        svc.mark_missed_runs(db, now=after + timedelta(minutes=15))      # job runs again
        assert _notes(db, "mgr_dago_kpi@test.test", "Checklist terlewat%") == 1
        assert _notes(db, "mgr_bdg_kpi@test.test", "Checklist terlewat%") == 0
        note = db.query(Notification).filter(Notification.title.like("Checklist terlewat%")).first()
        assert (note.entity_type, note.entity_id) == ("ops_checklists", run.id)


# ── Due soon ─────────────────────────────────────────────────────────────────

class TestDueSoon:
    def test_staff_get_one_reminder_30_minutes_before(self, client, db, admin, managers):
        run = _runs(client, db, admin)[0]
        too_early = run.due_at - timedelta(minutes=45)
        assert svc.remind_due_soon(db, now=too_early) == []
        at = run.due_at - timedelta(minutes=20)
        assert [r.id for r in svc.remind_due_soon(db, now=at)] == [run.id]
        assert svc.remind_due_soon(db, now=at + timedelta(minutes=5)) == []
        assert _notes(db, "staff_dago_kpi@test.test", "Checklist belum diisi%") == 1

    def test_no_reminder_once_someone_started(self, client, db, admin, managers):
        run = _runs(client, db, admin)[0]
        run.status = "in_progress"
        db.flush()
        assert svc.remind_due_soon(db, now=run.due_at - timedelta(minutes=10)) == []

    def test_the_job_runs_reminders(self, client, db, admin, managers, monkeypatch):
        from scripts import run_ops_checklists
        called = []
        monkeypatch.setattr(run_ops_checklists, "remind_due_soon", lambda db: called.append(1) or [])
        run_ops_checklists.run(db)
        assert called


# ── KPIs ─────────────────────────────────────────────────────────────────────

def _set(run, status, late=False, score=None, closed=True):
    run.status, run.is_late, run.score = status, late, score
    if closed:
        run.closes_at = datetime.now(UTC) - timedelta(hours=1)


class TestKpis:
    @pytest.fixture()
    def data(self, client, db, admin):
        runs = _runs(client, db, admin, outlets=("Dago", "Bandung"), days=(0, 1, 2))
        dago = sorted([r for r in runs if r.outlet == "Dago"], key=lambda r: r.run_date)
        bdg = sorted([r for r in runs if r.outlet == "Bandung"], key=lambda r: r.run_date)
        _set(dago[0], "submitted", score=100)
        _set(dago[1], "submitted", late=True, score=50)
        _set(dago[2], "missed")
        dago[1].items[0].result = "fail"
        _set(bdg[0], "submitted", score=80)
        _set(bdg[1], "submitted", score=90)
        _set(bdg[2], "pending", closed=False)
        bdg[2].closes_at = datetime.now(UTC) + timedelta(hours=2)     # still open: not counted
        db.add(TemperatureLog(asset_id=_asset(client, admin), outlet="Dago", outlet_id=dago[0].outlet_id, value=9,
                              min_value=0, max_value=4, in_range=False, source="manual",
                              recorded_at=datetime(2026, 10, 17, 3, 0, tzinfo=UTC),
                              received_at=datetime(2026, 10, 17, 3, 0, tzinfo=UTC)))
        db.flush()
        return dago, bdg

    def test_per_outlet_worst_first(self, client, db, admin, data):
        body = client.get("/api/ops-checklists/kpis?from=2026-10-16&to=2026-10-18", headers=admin).json()
        assert [o["outlet"] for o in body["outlets"]] == ["Dago", "Bandung"]
        dago, bdg = body["outlets"]
        assert (dago["scheduled"], dago["onTime"], dago["late"], dago["missed"], dago["compliance"]) == (3, 1, 1, 1, 33.3)
        assert dago["avgScore"] == 75.0
        assert dago["topFailing"] == [{"title": "Sabun di wastafel cuci tangan", "count": 1}]
        assert dago["excursions"] == 1
        assert (bdg["scheduled"], bdg["onTime"], bdg["compliance"]) == (2, 2, 100.0)
        assert body["totals"]["scheduled"] == 5 and body["totals"]["compliance"] == 60.0
        assert len(body["trend"]) == 8
        assert body["trend"][-1]["weekStart"] == "2026-10-12"

    def test_manager_sees_only_their_outlet(self, client, db, admin, managers, data):
        body = client.get("/api/ops-checklists/kpis?from=2026-10-16&to=2026-10-18", headers=managers["bdg"]).json()
        assert [o["outlet"] for o in body["outlets"]] == ["Bandung"]
        assert body["totals"]["excursions"] == 0

    def test_bad_range_is_422(self, client, admin):
        assert client.get("/api/ops-checklists/kpis?from=2026-10-18&to=2026-10-16", headers=admin).status_code == 422


def _asset(client, h):
    res = client.post("/api/assets", headers=h, json={"name": "Chiller KPI", "category": "Refrigeration",
                                                      "outlet": "Dago", "tempMonitored": True, "tempMin": 0, "tempMax": 4})
    return res.json()["id"]
