"""Integration tests — email notifications (Todo-Next §4).

Uses the "memory" email backend, so delivered messages land in
email_service.OUTBOX. Covers: which events email whom, per-user opt-out,
nothing is sent when the action rolls back, and an SMTP failure never fails
the user's action.
"""

import pytest

from app.config import settings
from app.services import email_service
from tests.conftest import seed_user_headers


@pytest.fixture()
def outbox(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "memory")
    email_service.OUTBOX.clear()
    yield email_service.OUTBOX
    email_service.OUTBOX.clear()


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_mail@test.test", "admin", name="Admin Mail")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_mail@test.test", "manager", name="Manager Mail")


def _to(outbox, email):
    return [m for m in outbox if m.to == email]


def _approval_issue(client, headers):
    res = client.post("/api/issues", json={
        "title": "Beli oven baru", "category": "Procurement", "priority": "medium",
        "outlet": "Jakarta", "generateTask": False,
        "generateApproval": True, "approvalAmount": 2_000_000,
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_new_approval_emails_step1_approver(client, outbox, admin, manager):
    _approval_issue(client, admin)
    mails = _to(outbox, "mgr_mail@test.test")
    assert len(mails) == 1
    assert "Approval menunggu Anda" in mails[0].subject


def test_next_step_approver_emailed_after_step1(client, outbox, admin, manager):
    issue = _approval_issue(client, admin)
    outbox.clear()
    client.patch(f"/api/approvals/{issue['approvalId']}/decide",
                 json={"decision": "approved"}, headers=manager)
    assert any("Approval menunggu Anda" in m.subject for m in _to(outbox, "admin_mail@test.test"))


def test_opt_out_preference_stops_email(client, outbox, admin, manager):
    client.patch("/api/auth/me/preferences",
                 json={"preferences": {"emailApprovalPending": False}}, headers=manager)
    _approval_issue(client, admin)
    assert _to(outbox, "mgr_mail@test.test") == []

    # The in-app notification is still created.
    notes = client.get("/api/notifications?limit=50", headers=manager).json()
    assert any("menunggu" in n["title"].lower() for n in notes)


def test_escalation_emails_admin(client, outbox, admin, manager):
    _approval_issue(client, admin)
    outbox.clear()
    client.post("/api/approvals/escalate-stale", json={"thresholdDays": 0}, headers=admin)
    assert any("macet" in m.subject.lower() for m in _to(outbox, "admin_mail@test.test"))


def test_work_order_assignment_emails_assignee(client, db, outbox, admin):
    seed_user_headers(db, "tech_mail@test.test", "staff", name="Teknisi Mail")
    asset = client.post("/api/assets", json={
        "name": "Grill", "category": "Kitchen", "outlet": "Jakarta", "status": "operational",
    }, headers=admin).json()
    res = client.post("/api/work-orders", json={
        "assetId": asset["id"], "type": "corrective", "title": "Ganti burner",
        "priority": "high", "assignee": "Teknisi Mail",
    }, headers=admin)
    assert res.status_code == 201, res.text

    mails = _to(outbox, "tech_mail@test.test")
    assert len(mails) == 1
    assert "Work order ditugaskan" in mails[0].subject


def _own_session():
    """A session on its own connection, so commit/rollback can't disturb the
    shared test session. Nothing is written to the DB."""
    from tests.conftest import TestingSession, engine
    connection = engine.connect()
    return connection, TestingSession(bind=connection)


def test_commit_sends_queued_email(outbox):
    connection, session = _own_session()
    try:
        email_service.queue_email(session, "x@test.test", "Subject", "Body")
        assert outbox == []                 # nothing before commit
        session.commit()
        assert [m.to for m in outbox] == ["x@test.test"]
    finally:
        session.close()
        connection.close()


def test_rollback_drops_queued_email(outbox):
    connection, session = _own_session()
    try:
        email_service.queue_email(session, "x@test.test", "Subject", "Body")
        session.rollback()
        session.commit()                    # a later commit must not send stale mail
        assert outbox == []
    finally:
        session.close()
        connection.close()


def test_close_without_commit_drops_queued_email(outbox):
    connection, session = _own_session()
    try:
        session.connection()                # open a transaction, like a request does
        email_service.queue_email(session, "x@test.test", "Subject", "Body")
        session.close()
        assert outbox == []
        assert "pending_emails" not in session.info
    finally:
        connection.close()


def test_smtp_failure_does_not_fail_the_action(client, monkeypatch, admin, manager):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SMTP_PORT", 1)        # nothing listens here
    monkeypatch.setattr(settings, "SMTP_TIMEOUT", 1)

    _approval_issue(client, admin)                       # asserts 201 inside


def test_send_smtp_builds_message(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["addr"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def starttls(self):
            sent["tls"] = True
        def login(self, user, password):
            sent["login"] = user
        def send_message(self, mime):
            sent["mime"] = mime

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USER", "bot")
    monkeypatch.setattr(settings, "SMTP_STARTTLS", True)

    email_service._send_smtp(email_service.OutgoingEmail("a@b.c", "Hi", "Body"))

    assert sent["addr"] == ("smtp.example.com", settings.SMTP_PORT)
    assert sent["tls"] is True and sent["login"] == "bot"
    assert sent["mime"]["To"] == "a@b.c" and sent["mime"]["Subject"] == "Hi"
