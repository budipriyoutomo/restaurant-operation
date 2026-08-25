"""Integration tests — the scheduled PM generator entry point (scripts/run_pm_generator).

The systemd timer invokes this module daily, so the contract that matters is:
running it repeatedly must never duplicate work orders.
"""

from datetime import date

import pytest
from scripts.run_pm_generator import run
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_runner@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Exhaust Fan", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


def _preventive(client, admin, asset_id):
    wos = client.get(f"/api/work-orders?asset_id={asset_id}", headers=admin).json()
    return [w for w in wos if w["type"] == "preventive"]


def test_runner_generates_due_schedule(client, db, admin, asset_id):
    client.post("/api/pm-schedules", json={
        "assetId": asset_id, "name": "Bersihkan exhaust", "intervalType": "days",
        "intervalValue": 30, "nextDueDate": date.today().isoformat(), "isActive": True,
    }, headers=admin)

    created = run(db)
    assert len(created) == 1
    assert len(_preventive(client, admin, asset_id)) == 1


def test_runner_is_idempotent_across_runs(client, db, admin, asset_id):
    client.post("/api/pm-schedules", json={
        "assetId": asset_id, "name": "Bersihkan exhaust", "intervalType": "days",
        "intervalValue": 30, "nextDueDate": date.today().isoformat(), "isActive": True,
    }, headers=admin)

    first = run(db)
    second = run(db)      # simulates the timer firing again / a retry after failure

    assert len(first) == 1
    assert second == []
    assert len(_preventive(client, admin, asset_id)) == 1


def test_runner_no_schedules_is_a_noop(client, db, admin):
    assert run(db) == []
