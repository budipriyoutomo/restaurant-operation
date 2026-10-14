"""Integration — paged lists for outlet connections (Todo-Pilot §22 / UX-9).

Every big list takes `limit` / `offset` and reports the full size in
`X-Total-Count`, so a phone on outlet Wi-Fi fetches one page at a time.
Without `limit` a list still returns everything (old clients keep working).
"""

import pytest

from app.models.outlet import Outlet
from tests.conftest import seed_user_headers

LISTS = ["/api/issues", "/api/tasks", "/api/approvals", "/api/work-orders", "/api/audit-logs"]


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_page@test.test", "admin")


@pytest.fixture()
def staff_bdg(client, db):
    bdg = db.query(Outlet).filter(Outlet.name == "Bandung").one()
    return seed_user_headers(db, "staff_bdg_page@test.test", "staff", outlets=[bdg])


def _issue(client, headers, title, **over):
    body = {
        "title": title, "description": "x", "category": "Maintenance", "priority": "medium",
        "outlet": "Jakarta", "assignee": "Unassigned", "generateTask": True, "generateWorkOrder": True,
        "estimatedCost": 100_000, **over,
    }
    res = client.post("/api/issues", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def three(client, admin):
    issues = [_issue(client, admin, f"Paged {n}") for n in range(3)]
    for i in issues:   # status changes write the audit log
        assert client.patch(f"/api/issues/{i['id']}", json={"priority": "high"}, headers=admin).status_code == 200
    for n in range(3):
        _issue(client, admin, f"Needs money {n}", category="Procurement", generateApproval=True, generateWorkOrder=False)
    return issues


@pytest.mark.parametrize("path", LISTS)
def test_limit_and_offset_walk_the_list_without_overlap(client, admin, three, path):
    full = client.get(path, headers=admin)
    total = int(full.headers["X-Total-Count"])
    assert total >= 3
    # audit-logs: newest 100 when no limit is given; other lists: everything
    assert len(full.json()) == (min(total, 100) if path == "/api/audit-logs" else total)

    first = client.get(f"{path}?limit=2", headers=admin)
    second = client.get(f"{path}?limit=2&offset=2", headers=admin)
    assert first.status_code == second.status_code == 200
    assert int(first.headers["X-Total-Count"]) == total
    assert len(first.json()) == 2
    ids = [r["id"] for r in first.json() + second.json()]
    assert len(ids) == len(set(ids))                       # pages do not overlap
    assert ids == [r["id"] for r in full.json()][: len(ids)]   # same order as the full list


@pytest.mark.parametrize("path", LISTS)
def test_limit_is_bounded(client, admin, path):
    assert client.get(f"{path}?limit=0", headers=admin).status_code == 422
    assert client.get(f"{path}?limit=501", headers=admin).status_code == 422
    assert client.get(f"{path}?offset=-1", headers=admin).status_code == 422


def test_total_count_is_readable_by_the_browser(client, admin):
    res = client.get("/api/issues?limit=1", headers={**admin, "Origin": "http://localhost:3000"})
    exposed = res.headers.get("access-control-expose-headers", "")
    assert "x-total-count" in exposed.lower()


def test_issue_search_and_priority_filter_on_the_server(client, admin, three):
    _issue(client, admin, "Freezer bocor parah", priority="critical")
    found = client.get("/api/issues?q=freezer%20BOCOR", headers=admin).json()
    assert [i["title"] for i in found] == ["Freezer bocor parah"]
    by_number = client.get(f"/api/issues?q={three[0]['number']}", headers=admin).json()
    assert [i["id"] for i in by_number] == [three[0]["id"]]
    critical = client.get("/api/issues?priority=critical", headers=admin).json()
    assert critical and all(i["priority"] == "critical" for i in critical)


def test_paging_respects_outlet_scope(client, admin, staff_bdg, three):
    res = client.get("/api/issues?limit=50", headers=staff_bdg)
    assert res.status_code == 200
    assert all(i["outlet"] != "Jakarta" for i in res.json())
    assert int(res.headers["X-Total-Count"]) == len(res.json())


class TestGetOneTask:
    def test_returns_the_task(self, client, admin, three):
        task_id = three[0]["taskIds"][0]
        res = client.get(f"/api/tasks/{task_id}", headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["id"] == task_id and res.json()["issueId"] == three[0]["id"]

    def test_other_outlet_or_unknown_is_404(self, client, admin, staff_bdg, three):
        assert client.get(f"/api/tasks/{three[0]['taskIds'][0]}", headers=staff_bdg).status_code == 404
        assert client.get("/api/tasks/00000000-0000-0000-0000-000000000999", headers=admin).status_code == 404
        assert client.get("/api/tasks/not-a-uuid", headers=admin).status_code == 404
