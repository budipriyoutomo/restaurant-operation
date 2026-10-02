"""Integration tests — WhatsApp notifications via the outbox (Todo-Pilot §4).

Uses the "memory" backend: queue_whatsapp writes outbox rows in the request's
transaction, and the test drains them with process_due() (what the after-commit
thread / retry runner do in production), so delivered messages land in
whatsapp_service.OUTBOX.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.user import User
from app.models.whatsapp import WhatsAppMessage
from app.services import whatsapp_service
from app.services.whatsapp_service import process_due, queue_whatsapp
from tests.conftest import seed_user_headers

MGR_PHONE = "6281111111111"
ADMIN_PHONE = "6282222222222"


@pytest.fixture()
def wa(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_BACKEND", "memory")
    whatsapp_service.OUTBOX.clear()
    yield whatsapp_service.OUTBOX
    whatsapp_service.OUTBOX.clear()


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_wa@test.test", "admin", name="Admin WA")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_wa@test.test", "manager", name="Manager WA")


def _opt_in(client, headers, number, **prefs):
    assert client.patch("/api/auth/me/whatsapp", json={"number": number}, headers=headers).status_code == 200
    client.patch("/api/auth/me/preferences", json={"preferences": {"waEnabled": True, **prefs}}, headers=headers)


def _to(outbox, phone):
    return [m for m in outbox if m.phone == phone]


def _approval_issue(client, headers, **kw):
    payload = {
        "title": "Beli chiller", "category": "Procurement", "priority": "medium",
        "outlet": "Jakarta", "generateTask": False, "generateApproval": True, "approvalAmount": 2_000_000,
    }
    payload.update(kw)
    res = client.post("/api/issues", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Number + opt-in
# ---------------------------------------------------------------------------

class TestNumber:
    def test_number_is_normalised_and_returned(self, client, wa, manager):
        res = client.patch("/api/auth/me/whatsapp", json={"number": "0811-1111-1111"}, headers=manager)
        assert res.status_code == 200
        assert res.json()["whatsapp_number"] == MGR_PHONE
        assert client.get("/api/auth/me", headers=manager).json()["whatsapp_number"] == MGR_PHONE

    def test_invalid_number_is_422(self, client, wa, manager):
        assert client.patch("/api/auth/me/whatsapp", json={"number": "12ab"}, headers=manager).status_code == 422

    def test_empty_clears(self, client, wa, manager):
        client.patch("/api/auth/me/whatsapp", json={"number": MGR_PHONE}, headers=manager)
        res = client.patch("/api/auth/me/whatsapp", json={"number": ""}, headers=manager)
        assert res.json()["whatsapp_number"] is None

    def test_admin_can_set_someone_elses_number(self, client, db, wa, admin, manager):
        uid = client.get("/api/auth/me", headers=manager).json()["id"]
        res = client.patch(f"/api/auth/users/{uid}", json={"whatsapp_number": "+62 811 1111 1111"}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["whatsapp_number"] == MGR_PHONE

    def test_without_opt_in_nothing_is_sent(self, client, db, wa, admin, manager):
        client.patch("/api/auth/me/whatsapp", json={"number": MGR_PHONE}, headers=manager)   # number, no waEnabled
        _approval_issue(client, admin)
        process_due(db)
        assert wa == []
        assert db.query(WhatsAppMessage).count() == 0


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class TestEvents:
    def test_approval_waiting_for_my_step(self, client, db, wa, admin, manager):
        _opt_in(client, manager, MGR_PHONE)
        _approval_issue(client, admin)
        assert process_due(db)["sent"] >= 1
        msgs = _to(wa, MGR_PHONE)
        assert len(msgs) == 1
        assert "Approval menunggu Anda" in msgs[0].body

    def test_my_request_decided(self, client, db, wa, admin, manager):
        # The requester is matched by display name (assignee of the Issue).
        _opt_in(client, manager, MGR_PHONE, waApprovalPending=False)
        issue = _approval_issue(client, admin, assignee="Manager WA")
        client.patch(f"/api/approvals/{issue['approvalId']}/decide",
                     json={"decision": "rejected", "comment": "x"}, headers=manager)
        process_due(db)
        assert any("Rejected" in m.body for m in _to(wa, MGR_PHONE))

    def test_work_order_assigned_to_me(self, client, db, wa, admin, manager):
        _opt_in(client, manager, MGR_PHONE)
        asset = client.post("/api/assets", json={
            "name": "Kompor WA", "category": "Kitchen", "outlet": "Jakarta", "status": "operational",
        }, headers=admin).json()
        client.post("/api/work-orders", json={
            "assetId": asset["id"], "type": "corrective", "title": "Servis kompor", "priority": "high",
            "assignee": "Manager WA",
        }, headers=admin)
        process_due(db)
        assert any("Work order ditugaskan" in m.body for m in _to(wa, MGR_PHONE))

    def test_issue_ready_to_close(self, client, db, wa, admin, manager):
        _opt_in(client, manager, MGR_PHONE)
        issue = client.post("/api/issues", json={
            "title": "Wastafel mampet", "category": "Other", "priority": "low", "outlet": "Jakarta",
            "assignee": "Budi", "generateTask": True,
        }, headers=admin).json()
        client.patch(f"/api/tasks/{issue['taskIds'][0]}", json={"status": "resolved"}, headers=admin)
        process_due(db)
        assert any(f"Issue siap ditutup: {issue['number']}" in m.body for m in _to(wa, MGR_PHONE))

    def test_escalation_goes_to_admin(self, client, db, wa, admin, manager):
        _opt_in(client, admin, ADMIN_PHONE)
        _approval_issue(client, admin)
        client.post("/api/approvals/escalate-stale", json={"thresholdDays": 0}, headers=admin)
        process_due(db)
        assert any("macet" in m.body.lower() for m in _to(wa, ADMIN_PHONE))

    def test_per_event_opt_out(self, client, db, wa, admin, manager):
        _opt_in(client, manager, MGR_PHONE, waApprovalPending=False)
        _approval_issue(client, admin)
        process_due(db)
        assert _to(wa, MGR_PHONE) == []

    def test_outlet_scoping(self, client, db, wa, admin):
        from app.models.outlet import Outlet
        bandung = db.query(Outlet).filter(Outlet.name != "Jakarta").first()
        other = seed_user_headers(db, "mgr_other_wa@test.test", "manager", name="Other WA", outlets=[bandung])
        _opt_in(client, other, "6283333333333")
        _approval_issue(client, admin)                     # Jakarta issue
        process_due(db)
        assert _to(wa, "6283333333333") == []


# ---------------------------------------------------------------------------
# Reliability: after commit, rollback, retries, dedup, rate limit
# ---------------------------------------------------------------------------

def _user(db, email):
    return db.query(User).filter(User.email == email).one()


class TestReliability:
    def test_rollback_drops_the_message(self, client, db, wa, manager):
        _opt_in(client, manager, MGR_PHONE)
        user = _user(db, "mgr_wa@test.test")
        savepoint = db.begin_nested()
        assert queue_whatsapp(db, user, "approval_pending", "x") is not None
        assert db.query(WhatsAppMessage).count() == 1
        savepoint.rollback()          # only the work that queued the message is undone
        assert db.query(WhatsAppMessage).count() == 0
        assert _user(db, "mgr_wa@test.test").whatsapp_number == MGR_PHONE   # earlier work kept

    def test_failure_is_retried_with_backoff_then_failed(self, client, db, wa, manager, monkeypatch):
        _opt_in(client, manager, MGR_PHONE)
        user = _user(db, "mgr_wa@test.test")
        row = queue_whatsapp(db, user, "approval_pending", "hello", "approvals", None)
        db.commit()

        def boom(phone, body):
            raise whatsapp_service.WhatsAppSendError("WuzAPI down")
        monkeypatch.setattr(whatsapp_service, "_send", boom)

        now = datetime.now(timezone.utc)
        assert process_due(db, now=now) == {"sent": 0, "retry": 1, "failed": 0}
        db.refresh(row)
        assert row.status == "pending" and row.attempts == 1
        assert row.next_attempt_at >= now + timedelta(seconds=59)
        assert process_due(db, now=now)["retry"] == 0          # not due yet

        for i in range(settings.WHATSAPP_MAX_ATTEMPTS - 1):
            process_due(db, now=now + timedelta(hours=i + 1))
        db.refresh(row)
        assert row.status == "failed"
        assert row.attempts == settings.WHATSAPP_MAX_ATTEMPTS
        assert "WuzAPI down" in row.last_error

    def test_send_failure_never_fails_the_request(self, client, db, wa, admin, manager, monkeypatch):
        _opt_in(client, manager, MGR_PHONE)
        monkeypatch.setattr(whatsapp_service, "_send",
                            lambda p, b: (_ for _ in ()).throw(RuntimeError("boom")))
        issue = _approval_issue(client, admin)
        assert issue["approvalId"]
        process_due(db)                                        # failure is recorded, not raised
        assert db.query(WhatsAppMessage).filter(WhatsAppMessage.attempts == 1).count() >= 1

    def test_retry_runner_sends_due_rows(self, client, db, wa, manager):
        from scripts.run_whatsapp_retry import run
        _opt_in(client, manager, MGR_PHONE)
        queue_whatsapp(db, _user(db, "mgr_wa@test.test"), "work_order_assigned", "hi")
        db.commit()
        assert run(db)["sent"] == 1
        assert run(db)["sent"] == 0                            # never twice
        assert len(_to(wa, MGR_PHONE)) == 1

    def test_same_event_and_record_is_deduplicated(self, client, db, wa, manager):
        import uuid
        _opt_in(client, manager, MGR_PHONE)
        user = _user(db, "mgr_wa@test.test")
        entity = uuid.uuid4()
        assert queue_whatsapp(db, user, "approval_pending", "a", "approvals", entity) is not None
        assert queue_whatsapp(db, user, "approval_pending", "a", "approvals", entity) is None
        assert queue_whatsapp(db, user, "approval_pending", "b", "approvals", uuid.uuid4()) is not None

    def test_hourly_rate_limit_skips_extra_messages(self, client, db, wa, manager, monkeypatch):
        import uuid
        monkeypatch.setattr(settings, "WHATSAPP_MAX_PER_HOUR", 2)
        _opt_in(client, manager, MGR_PHONE)
        user = _user(db, "mgr_wa@test.test")
        rows = [queue_whatsapp(db, user, "work_order_assigned", f"m{i}", "work_orders", uuid.uuid4())
                for i in range(3)]
        assert [r.status for r in rows] == ["pending", "pending", "skipped"]
        db.commit()
        process_due(db)
        assert len(_to(wa, MGR_PHONE)) == 2

    def test_disabled_backend_queues_nothing(self, client, db, manager, monkeypatch):
        monkeypatch.setattr(settings, "WHATSAPP_BACKEND", "disabled")
        _opt_in(client, manager, MGR_PHONE)
        assert queue_whatsapp(db, _user(db, "mgr_wa@test.test"), "approval_pending", "x") is None


class TestTestMessage:
    def test_send_test(self, client, db, wa, manager):
        client.patch("/api/auth/me/whatsapp", json={"number": MGR_PHONE}, headers=manager)
        res = client.post("/api/auth/me/whatsapp/test", headers=manager)
        assert res.status_code == 202, res.text
        process_due(db)
        assert len(_to(wa, MGR_PHONE)) == 1

    def test_send_test_needs_number(self, client, wa, manager):
        assert client.post("/api/auth/me/whatsapp/test", headers=manager).status_code == 409

    def test_send_test_needs_server_config(self, client, manager, monkeypatch):
        monkeypatch.setattr(settings, "WHATSAPP_BACKEND", "disabled")
        assert client.post("/api/auth/me/whatsapp/test", headers=manager).status_code == 409

    def test_status_endpoint(self, client, wa, manager):
        assert client.get("/api/auth/whatsapp/status", headers=manager).json() == {"enabled": True, "backend": "memory"}


class TestWuzapiBackend:
    def test_posts_to_wuzapi_with_token(self, monkeypatch):
        import httpx
        calls = {}

        def fake_post(url, headers, json, timeout):
            calls.update(url=url, headers=headers, json=json)
            return httpx.Response(200, json={"code": 200, "success": True, "data": {}})
        monkeypatch.setattr(settings, "WHATSAPP_BACKEND", "wuzapi")
        monkeypatch.setattr(settings, "WUZAPI_URL", "http://wuzapi:8080/")
        monkeypatch.setattr(settings, "WUZAPI_TOKEN", "tok")
        monkeypatch.setattr(whatsapp_service.httpx, "post", fake_post)
        whatsapp_service._send(MGR_PHONE, "halo")
        assert calls == {"url": "http://wuzapi:8080/chat/send/text", "headers": {"Token": "tok"},
                         "json": {"Phone": MGR_PHONE, "Body": "halo"}}

    def test_wuzapi_error_raises(self, monkeypatch):
        import httpx
        monkeypatch.setattr(settings, "WHATSAPP_BACKEND", "wuzapi")
        monkeypatch.setattr(settings, "WUZAPI_URL", "http://wuzapi:8080")
        monkeypatch.setattr(whatsapp_service.httpx, "post",
                            lambda *a, **k: httpx.Response(500, text="not paired"))
        with pytest.raises(whatsapp_service.WhatsAppSendError):
            whatsapp_service._send(MGR_PHONE, "halo")
