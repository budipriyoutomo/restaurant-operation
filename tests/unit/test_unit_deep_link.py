"""Unit tests — deep links in outgoing notifications (Todo-Pilot §14 / UX-1). No database.

Email and WhatsApp must link straight to the record (approval, work order,
issue), not to the app's home page, so an approver can act in one tap.
"""

import uuid

import pytest

from app.config import settings
from app.services import notification_service, whatsapp_service
from app.services.deep_link import record_url

RID = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture
def app_url(monkeypatch):
    monkeypatch.setattr(settings, "APP_URL", "https://ops.example.com/")


@pytest.mark.parametrize("entity_type,path", [
    ("issues", "issues"),
    ("approvals", "approvals"),
    ("work_orders", "work-orders"),
])
def test_record_url_per_entity(app_url, entity_type, path):
    assert record_url(entity_type, RID) == f"https://ops.example.com/{path}/{RID}"


def test_record_url_accepts_string_id(app_url):
    assert record_url("issues", str(RID)) == f"https://ops.example.com/issues/{RID}"


def test_unknown_entity_falls_back_to_home(app_url):
    assert record_url("budgets", RID) == "https://ops.example.com"


def test_missing_id_falls_back_to_home(app_url):
    assert record_url("issues", None) == "https://ops.example.com"
    assert record_url(None, None) == "https://ops.example.com"


def test_no_app_url_means_no_link(monkeypatch):
    monkeypatch.setattr(settings, "APP_URL", "")
    assert record_url("issues", RID) is None


def test_whatsapp_body_links_to_record(app_url):
    body = whatsapp_service.format_body("Approval menunggu", "APR-2026-00001", "approvals", RID)
    assert body.endswith(f"https://ops.example.com/approvals/{RID}")


def test_whatsapp_body_without_entity_links_home(app_url):
    body = whatsapp_service.format_body("Tes", "Halo")
    assert body.endswith("https://ops.example.com")


def test_email_body_links_to_record(app_url):
    body = notification_service._email_body("WO-2026-00002 ditugaskan", "work_orders", RID)
    assert f"https://ops.example.com/work-orders/{RID}" in body


def test_email_body_without_app_url_has_no_link(monkeypatch):
    monkeypatch.setattr(settings, "APP_URL", "")
    assert "http" not in notification_service._email_body("Pesan", "issues", RID)
