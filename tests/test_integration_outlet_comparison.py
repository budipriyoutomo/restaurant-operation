"""Integration — GM multi-outlet view (Todo-Pilot §25 / UX-12).

One screen compares outlets on what a GM acts on: open issues & SLA, QA
score, guests waiting, budget and repair downtime. Outlets with exceptions
come first; each exception is flagged so the screen can lead with it. A
daily/weekly digest of the same numbers goes out to those who opt in.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from app.models.notification import Notification
from app.models.outlet import Outlet
from app.models.qa_audit import QAAuditSession, QAAuditTemplate
from tests.conftest import seed_user_headers


@pytest.fixture()
def co(client, db, test_company):
    """A fresh company with two outlets: Dago and Riau."""
    from app.services.role_service import ensure_default_roles
    from app.services.category_service import ensure_system_categories
    with bypass_tenant(db):
        company = Company(name="PT Banding", slug=f"banding-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        ensure_system_categories(db)
        dago = Outlet(name="Dago", code="DGO", status="operational")
        riau = Outlet(name="Riau", code="RIA", status="operational")
        db.add_all([dago, riau])
        db.flush()
        admin = seed_user_headers(db, f"gm-{company.slug}@x.test", "admin", name="GM")
        manager = seed_user_headers(db, f"mgr-{company.slug}@x.test", "manager", outlets=[riau])
        staff = seed_user_headers(db, f"staff-{company.slug}@x.test", "staff", outlets=[riau])
        # This company's staff role cannot open the dashboard.
        from app.models.role import Role
        role = db.query(Role).filter(Role.key == "staff").one()
        role.permissions = {"issues": "view"}
        db.flush()
    return {"company": company, "dago": dago, "riau": riau, "admin": admin, "manager": manager, "staff": staff}


def _issue(client, headers, outlet, **over):
    body = {"title": "x", "description": "x", "outlet": outlet, "category": "Other", "priority": "medium",
            "assignee": "Unassigned", "generateTask": False, **over}
    res = client.post("/api/issues", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _rows(client, headers, **params):
    res = client.get("/api/analytics/outlets", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return {r["outlet"]: r for r in res.json()["outlets"]}, res.json()


def test_counts_issues_and_sla_per_outlet_and_puts_exceptions_first(client, co):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _issue(client, co["admin"], "Dago", dueDate=yesterday)
    _issue(client, co["admin"], "Dago")
    _issue(client, co["admin"], "Riau")

    rows, body = _rows(client, co["admin"])

    assert rows["Dago"]["openIssues"] == 2 and rows["Dago"]["slaBreached"] == 1
    assert rows["Riau"]["openIssues"] == 1 and rows["Riau"]["slaBreached"] == 0
    assert "sla" in rows["Dago"]["flags"] and rows["Riau"]["flags"] == []
    assert [r["outlet"] for r in body["outlets"]] == ["Dago", "Riau"]


def test_qa_guest_and_downtime(client, db, co):
    with tenant(db, co["company"].id):
        tpl = QAAuditTemplate(name="Harian")
        db.add(tpl)
        db.flush()
        db.add(QAAuditSession(number="AUD-T-1", template_id=tpl.id, template_name="Harian", outlet="Riau",
                              outlet_id=co["riau"].id, audit_date=date.today(), status="submitted", score=70,
                              submitted_at=datetime.now(timezone.utc)))
        db.flush()
    _issue(client, co["admin"], "Riau", category="Guest Service", title="Pesanan lama")

    issue = _issue(client, co["admin"], "Dago", category="Maintenance", generateWorkOrder=True, estimatedCost=100_000)
    for target in ("in-progress", "completed"):
        assert client.patch(f"/api/work-orders/{issue['workOrderId']}/transition", json={"targetStatus": target},
                            headers=co["admin"]).status_code == 200
    from app.models.asset import WorkOrder
    with tenant(db, co["company"].id):
        wo = db.query(WorkOrder).filter(WorkOrder.id == issue["workOrderId"]).one()
        wo.downtime_start = wo.downtime_end - timedelta(hours=10)
        db.flush()

    rows, _ = _rows(client, co["admin"])

    assert rows["Riau"]["qaScore"] == 70.0 and "qa" in rows["Riau"]["flags"]
    assert rows["Riau"]["guestWaiting"] == 1 and "guest" in rows["Riau"]["flags"]
    assert rows["Dago"]["qaScore"] is None
    assert rows["Dago"]["downtimeHours"] == pytest.approx(10, abs=0.1) and "downtime" in rows["Dago"]["flags"]


def test_budget_is_shown_when_set(client, db, co):
    from app.models.budget import Budget
    with tenant(db, co["company"].id):
        db.add(Budget(outlet_id=co["dago"].id, outlet="Dago", period=date.today().strftime("%Y-%m"), amount=5_000_000))
        db.flush()
    rows, _ = _rows(client, co["admin"])
    assert rows["Dago"]["budgetPct"] == 0
    assert rows["Riau"]["budgetPct"] is None


def test_a_manager_compares_only_their_outlets(client, co):
    _issue(client, co["admin"], "Dago")
    rows, _ = _rows(client, co["manager"])
    assert list(rows) == ["Riau"]


def test_a_role_without_the_dashboard_cannot_see_it(client, co):
    assert client.get("/api/analytics/outlets", headers=co["staff"]).status_code == 403


def test_window_is_bounded(client, co):
    assert client.get("/api/analytics/outlets?days=0", headers=co["admin"]).status_code == 422
    assert client.get("/api/analytics/outlets?days=366", headers=co["admin"]).status_code == 422


class TestDigest:
    def _opt_in(self, client, headers, **prefs):
        assert client.patch("/api/auth/me/preferences", json={"preferences": prefs}, headers=headers).status_code == 200

    def test_goes_once_a_day_to_those_who_asked(self, client, db, co):
        from app.services.outlet_comparison_service import send_digests
        now = datetime(2026, 10, 14, 7, 30, tzinfo=timezone(timedelta(hours=7)))
        # Overdue relative to the digest's `now`, not the real clock.
        _issue(client, co["admin"], "Dago", dueDate=(now.date() - timedelta(days=2)).isoformat())
        self._opt_in(client, co["admin"], digestDaily=True)

        with tenant(db, co["company"].id):
            first = send_digests(db, now=now)
            again = send_digests(db, now=now + timedelta(hours=1))
            notes = db.query(Notification).filter(Notification.title.ilike("%ringkasan%")).all()

        assert [r["period"] for r in first] == ["daily"]
        assert again == []                                     # hourly runner: no duplicates
        assert len(notes) == 1
        assert "Dago" in notes[0].message and "SLA" in notes[0].message

    def test_nothing_before_the_hour_and_weekly_only_on_monday(self, client, db, co):
        from app.services.outlet_comparison_service import send_digests
        self._opt_in(client, co["admin"], digestWeekly=True)
        tz = timezone(timedelta(hours=7))
        with tenant(db, co["company"].id):
            assert send_digests(db, now=datetime(2026, 10, 12, 6, 0, tzinfo=tz)) == []   # Monday, too early
            assert send_digests(db, now=datetime(2026, 10, 14, 9, 0, tzinfo=tz)) == []   # Wednesday
            sent = send_digests(db, now=datetime(2026, 10, 19, 8, 0, tzinfo=tz))         # next Monday
        assert [r["period"] for r in sent] == ["weekly"]

    def test_users_without_the_dashboard_get_none(self, client, db, co):
        from app.services.outlet_comparison_service import send_digests
        self._opt_in(client, co["staff"], digestDaily=True)
        with tenant(db, co["company"].id):
            assert send_digests(db, now=datetime(2026, 10, 14, 8, 0, tzinfo=timezone(timedelta(hours=7)))) == []


def test_runner_goes_through_every_company(client, db, co):
    from scripts.run_outlet_digest import run
    client.patch("/api/auth/me/preferences", json={"preferences": {"digestDaily": True}}, headers=co["admin"])
    sent = run(db, now=datetime(2026, 10, 14, 8, 0, tzinfo=timezone(timedelta(hours=7))))
    assert {"user": f"gm-{co['company'].slug}@x.test", "period": "daily"} in sent
