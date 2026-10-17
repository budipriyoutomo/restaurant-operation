"""Integration tests — Issue comments (Todo-Ops-Checklist P3). Written before the
implementation (TDD). Contract:

  GET  /api/issues/{id}/comments   oldest first; issues:view; 404 outside the caller's outlets
  POST /api/issues/{id}/comments   {body}; Idempotency-Key; always kind "user"
  issue_comment_service.add_comment(..., kind="system", commit=False) — for
  services (checklist dedup) inside their own transaction.
  A new comment notifies the Issue's assignee, never the author themselves.
"""

import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.issue import Issue
from app.models.notification import Notification
from app.models.outlet import Outlet
from app.models.user import User
from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_cmt@test.test", "admin", name="Admin Komentar")


@pytest.fixture()
def andi(client, db):
    """Staff at every test outlet — the assignee."""
    h = seed_user_headers(db, "andi_cmt@test.test", "staff", name="Andi")
    return h, str(db.query(User).filter(User.email == "andi_cmt@test.test").one().id)


def _issue(client, h, outlet="Jakarta", **over):
    body = {"title": "AC bocor", "description": "x", "outlet": outlet, "category": "Maintenance",
            "priority": "medium", "generateTask": False, **over}
    res = client.post("/api/issues", json=body, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def _comment(client, h, issue_id, body="Sudah dicek, perlu ganti selang", **headers):
    return client.post(f"/api/issues/{issue_id}/comments", json={"body": body}, headers={**h, **headers})


class TestComments:
    def test_post_and_list_oldest_first(self, client, admin):
        issue = _issue(client, admin)
        first = _comment(client, admin, issue["id"], "Pertama")
        assert first.status_code == 201, first.text
        c = first.json()
        assert (c["body"], c["kind"], c["authorName"], c["issueId"]) == ("Pertama", "user", "Admin Komentar", issue["id"])
        assert c["authorUserId"] and c["createdAt"]
        _comment(client, admin, issue["id"], "Kedua")
        listed = client.get(f"/api/issues/{issue['id']}/comments", headers=admin).json()
        assert [x["body"] for x in listed] == ["Pertama", "Kedua"]

    def test_body_is_trimmed_and_required(self, client, admin):
        issue = _issue(client, admin)
        assert _comment(client, admin, issue["id"], "   ").status_code == 422
        assert _comment(client, admin, issue["id"], "x" * 4001).status_code == 422
        assert _comment(client, admin, issue["id"], "  ok  ").json()["body"] == "ok"

    def test_kind_cannot_be_forged(self, client, admin):
        issue = _issue(client, admin)
        res = client.post(f"/api/issues/{issue['id']}/comments", json={"body": "hai", "kind": "system"}, headers=admin)
        assert res.status_code == 201 and res.json()["kind"] == "user"

    def test_staff_with_issues_view_can_comment(self, client, admin, andi):
        h, _ = andi
        issue = _issue(client, admin)
        assert _comment(client, h, issue["id"]).status_code == 201

    def test_unknown_issue_is_404(self, client, admin):
        assert _comment(client, admin, str(uuid.uuid4())).status_code == 404
        assert client.get(f"/api/issues/{uuid.uuid4()}/comments", headers=admin).status_code == 404

    def test_audit_log(self, client, db, admin):
        issue = _issue(client, admin)
        _comment(client, admin, issue["id"])
        assert db.query(AuditLog).filter(AuditLog.table_name == "issue_comments",
                                         AuditLog.record_id == issue["id"]).count() == 1


class TestNotifications:
    def test_assignee_is_notified(self, client, db, admin, andi):
        _, andi_id = andi
        issue = _issue(client, admin, assigneeUserId=andi_id)
        before = db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).count()
        _comment(client, admin, issue["id"])
        notes = db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).all()
        assert len(notes) == before + 1
        assert notes[-1].entity_id == uuid.UUID(issue["id"])

    def test_author_is_not_notified_of_their_own_comment(self, client, db, admin, andi):
        h, andi_id = andi
        issue = _issue(client, admin, assigneeUserId=andi_id)
        before = db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).count()
        _comment(client, h, issue["id"])
        assert db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).count() == before


class TestSystemComments:
    def test_service_adds_a_system_comment_in_the_callers_transaction(self, client, db, admin, andi):
        from app.services.issue_comment_service import add_comment
        _, andi_id = andi
        issue_json = _issue(client, admin, assigneeUserId=andi_id)
        issue = db.query(Issue).filter(Issue.id == uuid.UUID(issue_json["id"])).one()
        before = db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).count()
        add_comment(db, issue, "Gagal lagi 16-10-2026 (OPS-2026-00042): -11 °C", kind="system", commit=False)
        db.flush()
        listed = client.get(f"/api/issues/{issue_json['id']}/comments", headers=admin).json()
        assert listed[-1]["kind"] == "system"
        assert listed[-1]["authorUserId"] is None
        assert listed[-1]["authorName"] == "Sistem"
        assert db.query(Notification).filter(Notification.user_id == uuid.UUID(andi_id)).count() == before + 1


class TestScopingAndIdempotency:
    def test_other_outlets_issue_is_404(self, client, db, admin):
        dago = db.query(Outlet).filter(Outlet.name == "Dago").one()
        staff = seed_user_headers(db, "dago_cmt@test.test", "staff", outlets=[dago])
        issue = _issue(client, admin, outlet="Jakarta")
        assert client.get(f"/api/issues/{issue['id']}/comments", headers=staff).status_code == 404
        assert _comment(client, staff, issue["id"]).status_code == 404

    def test_retry_with_the_same_key_adds_one_comment(self, client, admin):
        issue = _issue(client, admin)
        key = str(uuid.uuid4())
        a = _comment(client, admin, issue["id"], **{"Idempotency-Key": key})
        b = _comment(client, admin, issue["id"], **{"Idempotency-Key": key})
        assert a.json()["id"] == b.json()["id"]
        assert len(client.get(f"/api/issues/{issue['id']}/comments", headers=admin).json()) == 1
