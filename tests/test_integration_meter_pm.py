"""Integration tests — meter-based PM (Tier 3)."""

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_meter@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Genset 100kVA", "category": "Electrical", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _meter_schedule(client, admin, asset_id, interval=500):
    r = client.post("/api/pm-schedules", json={
        "assetId": asset_id, "name": "Servis tiap 500 jam", "triggerType": "meter",
        "meterInterval": interval, "checklist": ["Ganti oli"], "isActive": True,
    }, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


def _reading(client, admin, asset_id, value):
    return client.post(f"/api/assets/{asset_id}/meter-readings",
                       json={"value": value}, headers=admin)


def _preventive_wos(client, admin, asset_id):
    wos = client.get(f"/api/work-orders?asset_id={asset_id}", headers=admin).json()
    return [w for w in wos if w["type"] == "preventive"]


class TestMeterSchedule:
    def test_create_requires_meter_interval(self, client, admin, asset_id):
        r = client.post("/api/pm-schedules", json={
            "assetId": asset_id, "name": "bad", "triggerType": "meter", "isActive": True,
        }, headers=admin)
        assert r.status_code == 422

    def test_meter_schedule_generates_when_usage_reached(self, client, admin, asset_id):
        sched = _meter_schedule(client, admin, asset_id, interval=500)
        _reading(client, admin, asset_id, 600)   # past first 500-hour interval

        res = client.post("/api/pm-schedules/run-now", headers=admin).json()
        assert res["generated"] == 1
        pms = _preventive_wos(client, admin, asset_id)
        assert len(pms) == 1
        # last_meter_value advanced to the crossed threshold (500)
        refreshed = client.get(f"/api/pm-schedules/{sched['id']}", headers=admin).json()
        assert refreshed["lastMeterValue"] == 500

    def test_idempotent_until_next_interval(self, client, admin, asset_id):
        _meter_schedule(client, admin, asset_id, interval=500)
        _reading(client, admin, asset_id, 600)

        first = client.post("/api/pm-schedules/run-now", headers=admin).json()
        second = client.post("/api/pm-schedules/run-now", headers=admin).json()
        assert first["generated"] == 1
        assert second["generated"] == 0     # 600 < next threshold (1000)
        assert len(_preventive_wos(client, admin, asset_id)) == 1

        # cross the next interval
        _reading(client, admin, asset_id, 1100)
        third = client.post("/api/pm-schedules/run-now", headers=admin).json()
        assert third["generated"] == 1
        assert len(_preventive_wos(client, admin, asset_id)) == 2

    def test_no_reading_no_generation(self, client, admin, asset_id):
        _meter_schedule(client, admin, asset_id, interval=500)
        res = client.post("/api/pm-schedules/run-now", headers=admin).json()
        assert res["generated"] == 0
