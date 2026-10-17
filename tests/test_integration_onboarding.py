"""Integration — onboarding a new company (Todo-Pilot §24 / UX-11).

The first admin of a fresh company sees what is left to set up, in order:
outlets → users & roles → categories & PICs → assets & QR → preventive
maintenance → approval policy. Each step is done when the data exists — not
ticked by hand. Optional demo data lets a salesperson show a working company.
"""

import uuid

import pytest

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from tests.conftest import seed_user_headers

STEPS = ["outlets", "users", "masterData", "assets", "pm", "opsChecklist", "approvalPolicy"]


@pytest.fixture()
def fresh(client, db, test_company):
    """A company exactly as the platform creates it: roles and one admin, nothing else."""
    from app.services.role_service import ensure_default_roles
    from app.services.category_service import ensure_system_categories
    with bypass_tenant(db):
        company = Company(name="PT Baru", slug=f"baru-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        ensure_system_categories(db)
        admin = seed_user_headers(db, f"admin-{company.slug}@baru.test", "admin", name="Admin Baru")
    return {"company": company, "admin": admin}


def _status(client, headers):
    res = client.get("/api/onboarding", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_a_new_company_has_every_step_left(client, fresh):
    body = _status(client, fresh["admin"])
    assert [s["key"] for s in body["steps"]] == STEPS
    assert not any(s["done"] for s in body["steps"])
    assert body["complete"] is False
    assert body["demoAvailable"] is True


def test_steps_follow_the_data(client, fresh):
    admin = fresh["admin"]
    assert client.post("/api/outlets", json={"name": "Dago", "code": "DGO"}, headers=admin).status_code == 201
    assert client.post("/api/categories", json={"name": "Dapur", "type": "operations"}, headers=admin).status_code == 201
    assert client.post("/api/pics", json={"name": "Pak Joko", "email": "joko@baru.test", "phone": "0812", "department": "Teknik", "categories": []}, headers=admin).status_code == 201
    asset = client.post("/api/assets", json={"name": "Freezer", "category": "Refrigeration", "outlet": "Dago"}, headers=admin)
    assert asset.status_code == 201, asset.text

    done = {s["key"]: s["done"] for s in _status(client, admin)["steps"]}
    assert done == {"outlets": True, "users": False, "masterData": True, "assets": True, "pm": False, "opsChecklist": False, "approvalPolicy": False}


def test_an_outlet_threshold_counts_as_an_approval_policy(client, fresh):
    admin = fresh["admin"]
    outlet = client.post("/api/outlets", json={"name": "Dago", "code": "DGO"}, headers=admin).json()
    assert client.patch(f"/api/outlets/{outlet['id']}", json={"approvalThreshold": 2_000_000}, headers=admin).status_code == 200
    done = {s["key"]: s["done"] for s in _status(client, admin)["steps"]}
    assert done["approvalPolicy"] is True


def test_only_for_people_who_set_the_company_up(client, db, fresh):
    with tenant(db, fresh["company"].id):
        staff = seed_user_headers(db, f"staff-{fresh['company'].slug}@baru.test", "staff", outlets=[])
    assert client.get("/api/onboarding", headers=staff).status_code == 403
    assert client.post("/api/onboarding/demo-data", headers=staff).status_code == 403


def test_demo_data_gives_a_working_company_once(client, fresh):
    admin = fresh["admin"]
    res = client.post("/api/onboarding/demo-data", headers=admin)
    assert res.status_code == 201, res.text

    issues = client.get("/api/issues", headers=admin).json()
    assert len(issues) >= 3 and all(i["title"].startswith("[Demo]") for i in issues)
    assert {i["category"] for i in issues} >= {"Maintenance", "Procurement", "Guest Service"}
    assert client.get("/api/approvals", headers=admin).json()      # something to approve
    assets = client.get("/api/assets", headers=admin).json()
    assert assets and all(a["name"].startswith("[Demo]") for a in assets)

    body = _status(client, admin)
    assert body["demoAvailable"] is False
    assert client.post("/api/onboarding/demo-data", headers=admin).status_code == 409


def test_demo_data_never_touches_a_company_already_in_use(client, fresh):
    admin = fresh["admin"]
    client.post("/api/outlets", json={"name": "Dago", "code": "DGO"}, headers=admin)
    client.post("/api/issues", json={"title": "Real", "description": "x", "outlet": "Dago", "category": "Other",
                                      "priority": "low", "generateTask": False}, headers=admin)
    assert _status(client, admin)["demoAvailable"] is False
    assert client.post("/api/onboarding/demo-data", headers=admin).status_code == 409


def test_other_companies_do_not_count(client, fresh, alpha_admin_with_data):
    # The suite's default company has outlets and issues; the fresh one sees none of it.
    body = _status(client, fresh["admin"])
    assert not any(s["done"] for s in body["steps"])


@pytest.fixture()
def alpha_admin_with_data(client, db, test_company):
    headers = seed_user_headers(db, "admin_onb_alpha@test.test", "admin")
    client.post("/api/issues", json={"title": "Alpha", "description": "x", "outlet": "Jakarta", "category": "Other",
                                     "priority": "low", "generateTask": False}, headers=headers)
    return headers


def test_the_admin_can_hide_the_checklist(client, fresh):
    admin = fresh["admin"]
    assert _status(client, admin)["dismissed"] is False
    client.patch("/api/auth/me/preferences", json={"preferences": {"onboardingDismissed": True}}, headers=admin)
    assert _status(client, admin)["dismissed"] is True
