"""Unit tests — WhatsApp helpers (Todo-Pilot §4). No database."""

from datetime import timedelta

import pytest

from app.services.whatsapp_service import (
    WA_EVENT_PREFS,
    backoff_delay,
    normalize_phone,
    wants_whatsapp,
)


@pytest.mark.parametrize("raw,expected", [
    ("081234567890", "6281234567890"),
    ("0812-3456-7890", "6281234567890"),
    ("+62 812 3456 7890", "6281234567890"),
    ("6281234567890", "6281234567890"),
    ("81234567890", "6281234567890"),
    ("+60 12-345 6789", "60123456789"),         # other country with +
    ("(0812) 3456.7890", "6281234567890"),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_number_is_none(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize("raw", ["abc", "0812abc", "12345", "+0812345678901", "6281234567890123456"])
def test_invalid_numbers_raise(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_whatsapp_is_opt_in():
    assert not wants_whatsapp({}, "6281234567890", "approval_pending")
    assert not wants_whatsapp({"waEnabled": False}, "6281234567890", "approval_pending")
    assert wants_whatsapp({"waEnabled": True}, "6281234567890", "approval_pending")


def test_needs_number_and_known_event():
    assert not wants_whatsapp({"waEnabled": True}, None, "approval_pending")
    assert not wants_whatsapp({"waEnabled": True}, "6281234567890", "issue_created")
    assert not wants_whatsapp({"waEnabled": True}, "6281234567890", None)


@pytest.mark.parametrize("event,pref", list(WA_EVENT_PREFS.items()))
def test_each_event_can_be_switched_off(event, pref):
    prefs = {"waEnabled": True}
    assert wants_whatsapp(prefs, "6281234567890", event)
    assert not wants_whatsapp({**prefs, pref: False}, "6281234567890", event)


def test_required_events_covered():
    assert set(WA_EVENT_PREFS) == {
        "approval_pending", "approval_escalated", "approval_decided",
        "work_order_assigned", "issue_ready_to_close",
    }


def test_backoff_grows_then_caps():
    assert backoff_delay(1) == timedelta(minutes=1)
    assert backoff_delay(2) == timedelta(minutes=5)
    assert backoff_delay(3) == timedelta(minutes=30)
    assert backoff_delay(9) == timedelta(minutes=30)
