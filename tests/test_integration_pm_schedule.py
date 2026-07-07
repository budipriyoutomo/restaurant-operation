"""Integration tests — PM scheduling generator (Todo-CMMS.md §2.1).

Verifies:
  - run-now generates a preventive WO for a due schedule, copying the checklist
  - the generator is idempotent: a second run on the same day creates nothing
  - the schedule's next_due_date advances by the interval
  - completing a preventive WO refreshes the asset's last_pm / next_pm
"""

from datetime import date, timedelta

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_pm@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    res = client.post("/api/assets", json={
        "name": "AC Central Lobby",
        "category": "HVAC",
        "outlet": "Bandung",
        "status": "operational",
    }, headers=admin)
    assert res.status_code == 201
    return res.json()["id"]


def _make_schedule(client, admin, asset_id, next_due, interval_type="days", interval_value=30):
    res = client.post("/api/pm-schedules", json={
        "assetId": asset_id,
        "name": "Servis AC bulanan",
        "intervalType": interval_type,
        "intervalValue": interval_value,
        "checklist": ["Bersihkan filter", "Cek freon"],
        "leadTimeDays": 0,
        "nextDueDate": next_due,
        "isActive": True,
    }, headers=admin)
    assert res.status_code == 201, res.text
    return res.json()


def _preventive_wos(client, admin, asset_id):
    wos = client.get(f"/api/work-orders?asset_id={asset_id}", headers=admin).json()
    return [w for w in wos if w["type"] == "preventive"]


class TestGenerator:
    def test_run_now_generates_preventive_wo(self, client, admin, asset_id):
        today = date.today().isoformat()
        _make_schedule(client, admin, asset_id, next_due=today)

        res = client.post("/api/pm-schedules/run-now", headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["generated"] == 1

        pms = _preventive_wos(client, admin, asset_id)
        assert len(pms) == 1
        wo = client.get(f"/api/work-orders/{pms[0]['id']}", headers=admin).json()
        titles = [c["title"] for c in wo["checklistItems"]]
        assert titles == ["Bersihkan filter", "Cek freon"]
        assert wo["scheduledDate"] == today

    def test_generator_is_idempotent(self, client, admin, asset_id):
        today = date.today().isoformat()
        _make_schedule(client, admin, asset_id, next_due=today)

        first = client.post("/api/pm-schedules/run-now", headers=admin).json()
        second = client.post("/api/pm-schedules/run-now", headers=admin).json()

        assert first["generated"] == 1
        assert second["generated"] == 0
        assert len(_preventive_wos(client, admin, asset_id)) == 1

    def test_next_due_date_advances(self, client, admin, asset_id):
        today = date.today()
        sched = _make_schedule(
            client, admin, asset_id, next_due=today.isoformat(),
            interval_type="days", interval_value=30,
        )
        client.post("/api/pm-schedules/run-now", headers=admin)

        refreshed = client.get(f"/api/pm-schedules/{sched['id']}", headers=admin).json()
        assert refreshed["nextDueDate"] == (today + timedelta(days=30)).isoformat()
        assert refreshed["lastGeneratedAt"] is not None

    def test_future_schedule_not_generated(self, client, admin, asset_id):
        future = (date.today() + timedelta(days=10)).isoformat()
        _make_schedule(client, admin, asset_id, next_due=future)

        res = client.post("/api/pm-schedules/run-now", headers=admin).json()
        assert res["generated"] == 0
        assert _preventive_wos(client, admin, asset_id) == []


class TestPreventiveCompletionUpdatesAssetPM:
    def test_completing_preventive_wo_sets_last_and_next_pm(self, client, admin, asset_id):
        today = date.today()
        _make_schedule(client, admin, asset_id, next_due=today.isoformat(),
                       interval_type="days", interval_value=30)
        client.post("/api/pm-schedules/run-now", headers=admin)
        wo_id = _preventive_wos(client, admin, asset_id)[0]["id"]

        # scheduled → in-progress → completed
        r1 = client.patch(f"/api/work-orders/{wo_id}/transition",
                          json={"targetStatus": "in-progress"}, headers=admin)
        assert r1.status_code == 200, r1.text
        r2 = client.patch(f"/api/work-orders/{wo_id}/transition",
                          json={"targetStatus": "completed"}, headers=admin)
        assert r2.status_code == 200, r2.text

        asset = client.get(f"/api/assets/{asset_id}", headers=admin).json()
        assert asset["lastPM"] == today.isoformat()
        assert asset["nextPM"] == (today + timedelta(days=30)).isoformat()
