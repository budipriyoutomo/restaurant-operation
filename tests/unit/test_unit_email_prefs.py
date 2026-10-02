"""Unit tests — email notification preferences (Todo-Next §4).

Purely in-memory. Every email event is on by default; a user switches one off
with its preference key. Events without an email mapping never send email.
"""

from app.services.notification_service import EMAIL_EVENT_PREFS, wants_email


def test_events_default_on():
    for event in EMAIL_EVENT_PREFS:
        assert wants_email({}, event) is True
        assert wants_email(None, event) is True


def test_preference_switches_event_off():
    assert wants_email({"emailApprovalPending": False}, "approval_pending") is False


def test_other_events_unaffected_by_one_switch():
    assert wants_email({"emailApprovalPending": False}, "work_order_assigned") is True


def test_unknown_or_missing_event_never_emails():
    assert wants_email({}, None) is False
    assert wants_email({}, "issue_created") is False
