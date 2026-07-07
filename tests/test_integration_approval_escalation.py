"""Integration tests — approval delegation + escalation (Tier 3)."""

import pytest
from tests.conftest import seed_user_headers


def _seed_with_id(db, email, role):
    """Seed a user, returning (headers, user_id)."""
    from app.models.user import User
    from app.services.auth_service import create_access_token, hash_password
    user = User(email=email, name=f"Test {role}", password_hash=hash_password("Pass1234!"), role=role)
    db.add(user)
    db.flush()
    token = create_access_token(str(user.id), user.email, role)
    return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_esc@test.test", "admin")


@pytest.fixture()
def approval_id(client, admin):
    asset = client.post("/api/assets", json={
        "name": "Boiler", "category": "Utility", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    issue = client.post("/api/issues", json={
        "title": "Boiler bocor", "category": "Maintenance", "priority": "critical",
        "reportedBy": "Teknisi", "outlet": "Jakarta",
        "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": 3_000_000,
    }, headers=admin).json()
    return issue["approvalId"]


def _notes(client, headers, contains):
    items = client.get("/api/notifications?limit=100", headers=headers).json()
    return [n for n in items if contains.lower() in (n["title"] + n["message"]).lower()]


class TestDelegation:
    def test_delegate_active_step_to_user(self, client, db, admin, approval_id):
        target_headers, target_id = _seed_with_id(db, "deleg_target@test.test", "manager")

        r = client.patch(f"/api/approvals/{approval_id}/delegate",
                         json={"toUserId": target_id}, headers=admin)
        assert r.status_code == 200, r.text
        step1 = next(s for s in r.json()["steps"] if s["stepOrder"] == 1)
        assert step1["approverUserId"] == target_id

        # the delegate target is notified
        assert len(_notes(client, target_headers, "menunggu")) == 1

    def test_delegate_requires_a_target(self, client, admin, approval_id):
        r = client.patch(f"/api/approvals/{approval_id}/delegate", json={}, headers=admin)
        assert r.status_code == 422


class TestEscalation:
    def test_escalate_stale_flags_and_notifies(self, client, admin, approval_id):
        # threshold 0 → any pending step counts as stale
        r = client.post("/api/approvals/escalate-stale", json={"thresholdDays": 0}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["escalated"] >= 1
        assert approval_id in r.json()["approvalIds"]

        # request marked escalated
        appr = client.get(f"/api/approvals/{approval_id}", headers=admin).json()
        assert appr["escalated"] is True
        # admin got a critical escalation notice
        assert len(_notes(client, admin, "macet")) >= 1

    def test_escalation_is_idempotent(self, client, admin, approval_id):
        first = client.post("/api/approvals/escalate-stale", json={"thresholdDays": 0}, headers=admin).json()
        second = client.post("/api/approvals/escalate-stale", json={"thresholdDays": 0}, headers=admin).json()
        assert first["escalated"] >= 1
        assert approval_id not in second["approvalIds"]

    def test_fresh_approval_not_escalated_with_high_threshold(self, client, admin, approval_id):
        r = client.post("/api/approvals/escalate-stale", json={"thresholdDays": 30}, headers=admin).json()
        assert approval_id not in r["approvalIds"]
