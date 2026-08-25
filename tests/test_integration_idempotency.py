"""Integration tests — idempotency keys for offline-safe retries (Tier 5.3-A)."""

import uuid

import pytest
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_idem@test.test", "admin")


@pytest.fixture()
def asset_id(client, admin):
    return client.post("/api/assets", json={
        "name": "Chiller Idem", "category": "HVAC", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()["id"]


@pytest.fixture()
def wo_id(client, admin, asset_id):
    return client.post("/api/work-orders", json={
        "assetId": asset_id, "type": "corrective", "title": "Servis", "priority": "high",
    }, headers=admin).json()["id"]


def _part(client, admin, sku="IDEM-1", stock=10):
    return client.post("/api/parts", json={"sku": sku, "name": "Filter", "stockQty": stock, "unitCost": 1000},
                       headers=admin).json()


class TestConsumePartIdempotency:
    def test_retry_with_same_key_does_not_double_decrement(self, client, admin, wo_id):
        part = _part(client, admin, stock=10)
        key = str(uuid.uuid4())
        headers = {**admin, "Idempotency-Key": key}

        r1 = client.post(f"/api/work-orders/{wo_id}/parts",
                         json={"partId": part["id"], "quantity": 3}, headers=headers)
        r2 = client.post(f"/api/work-orders/{wo_id}/parts",
                         json={"partId": part["id"], "quantity": 3}, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]          # same stored response

        # stock decremented exactly once (10 - 3), not twice
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 7
        # exactly one consumption line recorded
        assert len(client.get(f"/api/work-orders/{wo_id}/parts", headers=admin).json()) == 1

    def test_different_key_executes_again(self, client, admin, wo_id):
        part = _part(client, admin, stock=10)
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 2},
                    headers={**admin, "Idempotency-Key": str(uuid.uuid4())})
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 2},
                    headers={**admin, "Idempotency-Key": str(uuid.uuid4())})
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 6  # two decrements

    def test_no_key_behaves_normally(self, client, admin, wo_id):
        part = _part(client, admin, stock=10)
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 1}, headers=admin)
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 1}, headers=admin)
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 8


class TestChecklistIdempotency:
    def test_retry_does_not_duplicate_checklist_item(self, client, admin, wo_id):
        key = str(uuid.uuid4())
        headers = {**admin, "Idempotency-Key": key}
        client.post(f"/api/work-orders/{wo_id}/checklist", json={"title": "Cek freon"}, headers=headers)
        client.post(f"/api/work-orders/{wo_id}/checklist", json={"title": "Cek freon"}, headers=headers)

        detail = client.get(f"/api/work-orders/{wo_id}", headers=admin).json()
        assert len(detail["checklistItems"]) == 1


class TestKeyReuseGuard:
    def test_same_key_different_request_conflicts(self, client, admin, wo_id):
        part = _part(client, admin, stock=10)
        key = str(uuid.uuid4())
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 1},
                    headers={**admin, "Idempotency-Key": key})
        # Same key, different endpoint → client bug, rejected.
        res = client.post(f"/api/work-orders/{wo_id}/checklist", json={"title": "X"},
                          headers={**admin, "Idempotency-Key": key})
        assert res.status_code == 409

    def test_key_is_scoped_per_user(self, client, db, admin, wo_id):
        """One user's key must not replay another user's response."""
        part = _part(client, admin, stock=10)
        key = str(uuid.uuid4())
        client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 4},
                    headers={**admin, "Idempotency-Key": key})

        other = seed_user_headers(db, "admin_idem2@test.test", "admin")
        # Same key value, different user → treated as a fresh request, executes.
        r = client.post(f"/api/work-orders/{wo_id}/parts", json={"partId": part["id"], "quantity": 4},
                        headers={**other, "Idempotency-Key": key})
        assert r.status_code == 201
        assert client.get(f"/api/parts/{part['id']}", headers=admin).json()["stockQty"] == 2  # 10-4-4
