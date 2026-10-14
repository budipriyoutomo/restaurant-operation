"""Integration — notifications speak the recipient's language (Todo-Pilot §16)."""

import pytest

from app.models.user import User
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_lang@test.test", "admin")


@pytest.fixture()
def manager_en(client, db):
    h = seed_user_headers(db, "mgr_en@test.test", "manager")
    db.query(User).filter(User.email == "mgr_en@test.test").update({"preferences": {"language": "en"}})
    db.flush()
    return h


@pytest.fixture()
def manager_id(client, db):
    return seed_user_headers(db, "mgr_id@test.test", "manager")


def _issue(client, headers):
    res = client.post("/api/issues", json={
        "title": "Wastafel bocor", "description": "x", "category": "Other", "priority": "high",
        "outlet": "Jakarta", "generateTask": False,
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _note_for(client, headers, issue_id):
    return next(n for n in client.get("/api/notifications", headers=headers).json() if n["entity_id"] == issue_id)


def test_new_issue_notice_is_indonesian_by_default(client, admin, manager_id):
    issue = _issue(client, admin)
    n = _note_for(client, manager_id, issue["id"])
    assert n["title"] == f"Laporan baru: {issue['number']}"
    assert "dilaporkan di Jakarta" in n["message"]


def test_new_issue_notice_is_english_for_an_english_user(client, admin, manager_en):
    issue = _issue(client, admin)
    n = _note_for(client, manager_en, issue["id"])
    assert n["title"] == f"New issue: {issue['number']}"
    assert "reported at Jakarta" in n["message"]


def test_status_change_uses_status_labels(client, admin, manager_id):
    issue = _issue(client, admin)
    client.patch(f"/api/issues/{issue['id']}", json={"status": "in-progress"}, headers=admin)
    notes = [n for n in client.get("/api/notifications", headers=manager_id).json() if n["entity_id"] == issue["id"]]
    assert any(n["title"] == f"Laporan {issue['number']} → Dikerjakan" for n in notes)
