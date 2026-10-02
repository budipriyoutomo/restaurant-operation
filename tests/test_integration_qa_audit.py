"""Integration tests — QA audit checklist API (Todo-Pilot §7).

Written before the implementation (TDD). Contract:

  /api/qa-audit-templates          GET (qa:view), POST/PATCH (qa:manage)
  /api/qa-audits                   POST start (qa:manage), GET list (scoped)
  /api/qa-audits/{id}              GET detail, DELETE draft
  /api/qa-audits/{id}/findings/{f} PATCH result/notes
  /api/qa-audits/{id}/findings/{f}/photos   POST multipart (Idempotency-Key)
  /api/qa-audits/{id}/submit       POST → score, Issues for fails, repeats
  /api/qa-audits/scores            GET per-outlet latest score + monthly trend
"""

import io
import uuid

import pytest
from PIL import Image

from tests.conftest import seed_user_headers


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_qa@test.test", "admin", name="Admin QA")


@pytest.fixture()
def manager(client, db):
    return seed_user_headers(db, "mgr_qa@test.test", "manager", name="Manager QA")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_qa@test.test", "staff", name="Staff QA")


ITEMS = [
    {"title": "Suhu chiller ≤ 5°C", "category": "Food Safety", "weight": 3, "isCritical": True},
    {"title": "Lantai dapur bersih", "category": "Kebersihan", "weight": 2, "requiresPhoto": True},
    {"title": "Seragam lengkap", "category": "SOP", "weight": 1},
]


def _template(client, headers, items=ITEMS, name="Audit Harian Dapur"):
    res = client.post("/api/qa-audit-templates", json={"name": name, "items": items}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _start(client, headers, template_id, outlet="Jakarta", **extra):
    res = client.post("/api/qa-audits", json={"templateId": template_id, "outlet": outlet, **extra}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _answer(client, headers, audit, title, result, notes=None):
    finding = next(f for f in audit["findings"] if f["title"] == title)
    body = {"result": result}
    if notes is not None:
        body["notes"] = notes
    res = client.patch(f"/api/qa-audits/{audit['id']}/findings/{finding['id']}", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


def _photo(client, headers, audit, title, key=None):
    finding = next(f for f in audit["findings"] if f["title"] == title)
    h = {**headers, **({"Idempotency-Key": key} if key else {})}
    return client.post(
        f"/api/qa-audits/{audit['id']}/findings/{finding['id']}/photos",
        files={"file": ("lantai.png", _png(), "image/png")}, headers=h,
    )


def _complete(client, headers, audit, chiller="pass", lantai="pass", seragam="pass"):
    _answer(client, headers, audit, "Suhu chiller ≤ 5°C", chiller)
    _answer(client, headers, audit, "Lantai dapur bersih", lantai)
    if lantai != "na":
        assert _photo(client, headers, audit, "Lantai dapur bersih").status_code == 201
    _answer(client, headers, audit, "Seragam lengkap", seragam)


def _submit(client, headers, audit):
    return client.post(f"/api/qa-audits/{audit['id']}/submit", headers=headers)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_create_and_list(self, client, admin, staff):
        tpl = _template(client, admin)
        assert [i["title"] for i in tpl["items"]] == [i["title"] for i in ITEMS]
        assert tpl["items"][0]["weight"] == 3 and tpl["items"][0]["isCritical"] is True
        assert tpl["items"][1]["requiresPhoto"] is True
        listed = client.get("/api/qa-audit-templates", headers=staff).json()   # qa:view
        assert any(t["id"] == tpl["id"] for t in listed)

    def test_staff_cannot_create(self, client, staff):
        res = client.post("/api/qa-audit-templates", json={"name": "x", "items": ITEMS}, headers=staff)
        assert res.status_code == 403

    def test_manager_can_create(self, client, manager):
        _template(client, manager)

    def test_validation(self, client, admin):
        assert client.post("/api/qa-audit-templates", json={"name": "x", "items": []}, headers=admin).status_code == 422
        bad = [{"title": "x", "weight": 0}]
        assert client.post("/api/qa-audit-templates", json={"name": "x", "items": bad}, headers=admin).status_code == 422

    def test_patch_items_keeps_ids_and_deactivates_removed(self, client, admin):
        tpl = _template(client, admin)
        first, second, _third = tpl["items"]
        res = client.patch(f"/api/qa-audit-templates/{tpl['id']}", json={"items": [
            {"id": first["id"], "title": "Suhu chiller ≤ 4°C", "weight": 3, "isCritical": True},
            {"id": second["id"], "title": second["title"], "weight": 2, "requiresPhoto": True},
            {"title": "APAR tersedia", "weight": 2},
        ]}, headers=admin)
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert [i["title"] for i in items] == ["Suhu chiller ≤ 4°C", second["title"], "APAR tersedia"]
        assert items[0]["id"] == first["id"]                       # updated in place

    def test_template_edit_does_not_rewrite_running_audit(self, client, admin):
        tpl = _template(client, admin)
        audit = _start(client, admin, tpl["id"])
        client.patch(f"/api/qa-audit-templates/{tpl['id']}", json={"items": [
            {"id": tpl["items"][0]["id"], "title": "Renamed", "weight": 1},
        ]}, headers=admin)
        detail = client.get(f"/api/qa-audits/{audit['id']}", headers=admin).json()
        assert [f["title"] for f in detail["findings"]] == [i["title"] for i in ITEMS]

    def test_inactive_template_cannot_start_audit(self, client, admin):
        tpl = _template(client, admin)
        client.patch(f"/api/qa-audit-templates/{tpl['id']}", json={"isActive": False}, headers=admin)
        res = client.post("/api/qa-audits", json={"templateId": tpl["id"], "outlet": "Jakarta"}, headers=admin)
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# Running an audit
# ---------------------------------------------------------------------------

class TestAuditRun:
    def test_start_snapshots_items(self, client, admin):
        tpl = _template(client, admin)
        audit = _start(client, admin, tpl["id"])
        assert audit["number"].startswith("AUD-")
        assert audit["status"] == "in_progress"
        assert audit["score"] is None
        assert audit["auditor"] == "Admin QA"
        assert audit["progress"] == {"answered": 0, "total": 3}
        assert all(f["result"] is None for f in audit["findings"])

    def test_answer_and_progress(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        f = _answer(client, admin, audit, "Seragam lengkap", "fail", notes="2 staf tanpa topi")
        assert f["result"] == "fail" and f["notes"] == "2 staf tanpa topi"
        detail = client.get(f"/api/qa-audits/{audit['id']}", headers=admin).json()
        assert detail["progress"] == {"answered": 1, "total": 3}

    def test_invalid_result_is_422(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        fid = audit["findings"][0]["id"]
        res = client.patch(f"/api/qa-audits/{audit['id']}/findings/{fid}", json={"result": "ok"}, headers=admin)
        assert res.status_code == 422

    def test_photo_upload_is_idempotent(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        key = str(uuid.uuid4())
        r1 = _photo(client, admin, audit, "Lantai dapur bersih", key)
        r2 = _photo(client, admin, audit, "Lantai dapur bersih", key)
        assert r1.status_code == r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]
        detail = client.get(f"/api/qa-audits/{audit['id']}", headers=admin).json()
        lantai = next(f for f in detail["findings"] if f["title"] == "Lantai dapur bersih")
        assert len(lantai["photos"]) == 1
        file_res = client.get(lantai["photos"][0]["fileUrl"], headers=admin)
        assert file_res.status_code == 200 and file_res.headers["content-type"] == "image/png"

    def test_staff_cannot_run_audit(self, client, admin, staff):
        tpl = _template(client, admin)
        res = client.post("/api/qa-audits", json={"templateId": tpl["id"], "outlet": "Jakarta"}, headers=staff)
        assert res.status_code == 403

    def test_start_is_idempotent(self, client, admin):
        tpl = _template(client, admin)
        h = {**admin, "Idempotency-Key": str(uuid.uuid4())}
        a1 = client.post("/api/qa-audits", json={"templateId": tpl["id"], "outlet": "Jakarta"}, headers=h).json()
        a2 = client.post("/api/qa-audits", json={"templateId": tpl["id"], "outlet": "Jakarta"}, headers=h).json()
        assert a1["id"] == a2["id"]

    def test_discard_draft(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        assert client.delete(f"/api/qa-audits/{audit['id']}", headers=admin).status_code == 204
        assert client.get(f"/api/qa-audits/{audit['id']}", headers=admin).status_code == 404


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_incomplete_audit_is_409_with_problems(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _answer(client, admin, audit, "Suhu chiller ≤ 5°C", "pass")
        _answer(client, admin, audit, "Lantai dapur bersih", "fail")          # no photo
        res = _submit(client, admin, audit)
        assert res.status_code == 409
        assert res.json()["detail"]["problems"] == [
            "Lantai dapur bersih: photo required",
            "Seragam lengkap: not answered",
        ]

    def test_all_pass_scores_100_and_raises_no_issue(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _complete(client, admin, audit)
        res = _submit(client, admin, audit)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "submitted"
        assert body["score"] == 100.0
        assert all(f["issueId"] is None for f in body["findings"])

    def test_fail_creates_compliance_issue_with_task(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _complete(client, admin, audit, seragam="fail")
        body = _submit(client, admin, audit).json()
        assert body["score"] == 83.3                                 # 5 of 6 weight
        seragam = next(f for f in body["findings"] if f["title"] == "Seragam lengkap")
        issue = client.get(f"/api/issues/{seragam['issueId']}", headers=admin).json()
        assert issue["category"] == "Compliance"
        assert issue["outlet"] == "Jakarta"
        assert issue["priority"] == "medium"
        assert "Seragam lengkap" in issue["title"]
        assert body["number"] in issue["description"]
        assert len(issue["taskIds"]) == 1

    def test_critical_fail_is_critical_issue(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _complete(client, admin, audit, chiller="fail")
        body = _submit(client, admin, audit).json()
        chiller = next(f for f in body["findings"] if f["title"] == "Suhu chiller ≤ 5°C")
        assert client.get(f"/api/issues/{chiller['issueId']}", headers=admin).json()["priority"] == "critical"

    def test_submitted_audit_is_frozen(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _complete(client, admin, audit)
        _submit(client, admin, audit)
        fid = audit["findings"][0]["id"]
        assert client.patch(f"/api/qa-audits/{audit['id']}/findings/{fid}", json={"result": "fail"},
                            headers=admin).status_code == 409
        assert _submit(client, admin, audit).status_code == 409
        assert client.delete(f"/api/qa-audits/{audit['id']}", headers=admin).status_code == 409

    def test_repeat_finding_is_flagged_and_bumped(self, client, admin):
        tpl = _template(client, admin)
        first = _start(client, admin, tpl["id"])
        _complete(client, admin, first, seragam="fail")
        _submit(client, admin, first)

        second = _start(client, admin, tpl["id"])
        _complete(client, admin, second, seragam="fail", lantai="fail")
        body = _submit(client, admin, second).json()
        seragam = next(f for f in body["findings"] if f["title"] == "Seragam lengkap")
        lantai = next(f for f in body["findings"] if f["title"] == "Lantai dapur bersih")
        assert seragam["isRepeat"] is True
        assert lantai["isRepeat"] is False
        issue = client.get(f"/api/issues/{seragam['issueId']}", headers=admin).json()
        assert issue["title"].startswith("[Berulang]")
        assert issue["priority"] == "high"

    def test_repeat_is_per_outlet(self, client, admin):
        tpl = _template(client, admin)
        a = _start(client, admin, tpl["id"], outlet="Jakarta")
        _complete(client, admin, a, seragam="fail")
        _submit(client, admin, a)
        from tests.conftest import TEST_OUTLETS
        other = next(n for n, _ in TEST_OUTLETS if n != "Jakarta")
        b = _start(client, admin, tpl["id"], outlet=other)
        _complete(client, admin, b, seragam="fail")
        seragam = next(f for f in _submit(client, admin, b).json()["findings"] if f["title"] == "Seragam lengkap")
        assert seragam["isRepeat"] is False

    def test_submit_is_audited(self, client, admin):
        audit = _start(client, admin, _template(client, admin)["id"])
        _complete(client, admin, audit)
        _submit(client, admin, audit)
        logs = client.get(f"/api/audit-logs?table_name=qa_audit_sessions&record_id={audit['id']}", headers=admin).json()
        assert any(l["action"] == "submit" for l in logs)


# ---------------------------------------------------------------------------
# Outlet scoping + scores
# ---------------------------------------------------------------------------

class TestScopingAndScores:
    def _other_outlet(self):
        from tests.conftest import TEST_OUTLETS
        return next(n for n, _ in TEST_OUTLETS if n != "Jakarta")

    def _scoped_manager(self, client, db, outlet_name):
        from app.models.outlet import Outlet
        outlet = db.query(Outlet).filter(Outlet.name == outlet_name).one()
        return seed_user_headers(db, f"mgr_{outlet_name.lower()}@test.test", "manager", outlets=[outlet])

    def test_cannot_audit_outlet_outside_scope(self, client, db, admin):
        tpl = _template(client, admin)
        mgr = self._scoped_manager(client, db, "Jakarta")
        res = client.post("/api/qa-audits", json={"templateId": tpl["id"], "outlet": self._other_outlet()}, headers=mgr)
        assert res.status_code == 403

    def test_other_outlets_audits_are_hidden(self, client, db, admin):
        tpl = _template(client, admin)
        audit = _start(client, admin, tpl["id"], outlet=self._other_outlet())
        mgr = self._scoped_manager(client, db, "Jakarta")
        assert client.get(f"/api/qa-audits/{audit['id']}", headers=mgr).status_code == 404
        assert all(a["id"] != audit["id"] for a in client.get("/api/qa-audits", headers=mgr).json())

    def test_scores_latest_and_trend(self, client, admin):
        tpl = _template(client, admin)
        a = _start(client, admin, tpl["id"], auditDate="2026-09-10")
        _complete(client, admin, a, seragam="fail")            # 83.3
        _submit(client, admin, a)
        b = _start(client, admin, tpl["id"], auditDate="2026-10-01")
        _complete(client, admin, b)                            # 100
        _submit(client, admin, b)
        _start(client, admin, tpl["id"])                       # draft — not counted

        res = client.get("/api/qa-audits/scores", headers=admin)
        assert res.status_code == 200, res.text
        body = res.json()
        jakarta = next(o for o in body["outlets"] if o["outlet"] == "Jakarta")
        assert jakarta["latestScore"] == 100.0
        assert jakarta["latestDate"] == "2026-10-01"
        assert jakarta["audits"] == 2
        assert jakarta["repeatFindings"] == 0
        assert body["trend"]["Jakarta"] == [
            {"month": "2026-09", "score": 83.3, "audits": 1},
            {"month": "2026-10", "score": 100.0, "audits": 1},
        ]

    def test_scores_respect_scope(self, client, db, admin):
        tpl = _template(client, admin)
        a = _start(client, admin, tpl["id"], outlet=self._other_outlet())
        _complete(client, admin, a)
        _submit(client, admin, a)
        mgr = self._scoped_manager(client, db, "Jakarta")
        body = client.get("/api/qa-audits/scores", headers=mgr).json()
        assert all(o["outlet"] != self._other_outlet() for o in body["outlets"])
