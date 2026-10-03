"""Tenant leak sweep — every route, not a hand-picked list (Todo-Pilot §11).

Company Alpha gets data in every module through the API. Then, as Beta's
admin (full permissions in Beta), the sweep calls:

  - every GET route without path parameters      → no Alpha id in any 2xx body
  - every GET/PATCH/DELETE route with one path
    parameter, once per Alpha row id              → never a 2xx that returns or
                                                     changes Alpha data

Routes are discovered from the app's OpenAPI schema, so a new endpoint is
swept automatically.
Mirrors tests/test_integration_outlet_scoping.py one level up.
"""

import re
import uuid
from datetime import date

import pytest

from app.core.tenancy import TenantScoped, bypass_tenant, tenant
from app.database import Base
from app.main import app
from app.models.company import Company
from tests.conftest import seed_user_headers

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SKIP_PREFIXES = ("/api/platform", "/api/auth/me")


def _post(client, headers, path, body):
    res = client.post(path, json=body, headers=headers)
    assert res.status_code in (200, 201), f"{path}: {res.status_code} {res.text}"
    return res.json()


@pytest.fixture()
def alpha_data(client, db, test_company):
    """A spread of Alpha records across modules, created the way users create them."""
    h = seed_user_headers(db, "admin@alpha.test", "admin", name="Alpha Admin")
    seed_user_headers(db, "manager@alpha.test", "manager", name="Alpha Manager")
    asset = _post(client, h, "/api/assets", {"name": "Freezer", "category": "Refrigeration", "outlet": "Jakarta",
                                              "status": "operational"})
    _post(client, h, "/api/issues", {"title": "Freezer mati", "description": "x", "category": "Maintenance",
                                     "priority": "high", "outlet": "Jakarta", "generateTask": True,
                                     "generateWorkOrder": True, "assetId": asset["id"], "estimatedCost": 3_000_000})
    _post(client, h, "/api/issues", {"title": "Promo", "description": "x", "category": "Marketing", "priority": "low",
                                     "outlet": "Jakarta", "generateApproval": True, "approvalAmount": 2_000_000})
    vendor = _post(client, h, "/api/vendors", {"name": "PT Suku Cadang", "category": "Parts"})
    _post(client, h, "/api/parts", {"sku": "FLT-1", "name": "Filter", "category": "HVAC", "unitCost": 50_000,
                                    "stockQty": 1, "reorderLevel": 2, "vendorId": vendor["id"]})
    _post(client, h, "/api/campaigns", {"title": "Promo Ramadan", "type": "promotion", "outlet": "Jakarta",
                                        "budget": 1_000_000})
    program = _post(client, h, "/api/training-programs", {"title": "Food Safety", "outlet": "Jakarta",
                                                          "target_role": "staff",
                                                          "scheduled_date": date.today().isoformat()})
    template = _post(client, h, "/api/qa-audit-templates", {"name": "Audit Dapur", "items": [
        {"title": "Suhu chiller", "category": "Food Safety", "weight": 3, "isCritical": True}]})
    _post(client, h, "/api/qa-audits", {"templateId": template["id"], "outlet": "Jakarta"})
    _post(client, h, "/api/guest-cases", {"title": "Makanan dingin", "description": "x", "outlet": "Jakarta",
                                          "priority": "high", "guestName": "Ibu Sari", "channel": "google-review"})
    _post(client, h, "/api/pm-schedules", {"assetId": asset["id"], "name": "Servis bulanan", "triggerType": "calendar",
                                           "intervalType": "months", "intervalValue": 1, "checklist": ["Cek"],
                                           "nextDueDate": date.today().isoformat(),
                                           "isActive": True})
    _post(client, h, "/api/approval-policies", {"approvalType": "maintenance", "minAmount": 5_000_001,
                                                "steps": [{"order": 1, "role": "admin"}], "isActive": True})
    _post(client, h, "/api/categories", {"name": "Alpha Cat", "description": "x", "type": "operations"})
    assert program["id"]

    ids = set()
    with bypass_tenant(db):
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            if issubclass(cls, TenantScoped) and "id" in cls.__table__.c:
                ids |= {str(r[0]) for r in db.query(cls.id).filter(cls.company_id == test_company.id)}
    return ids


@pytest.fixture()
def beta_admin(client, db, alpha_data):
    from app.models.outlet import Outlet
    from app.services.role_service import ensure_default_roles
    with bypass_tenant(db):
        company = Company(name="PT Beta", slug=f"beta-{uuid.uuid4().hex[:6]}")
        db.add(company)
        db.flush()
    with tenant(db, company.id):
        ensure_default_roles(db)
        db.add(Outlet(name="Beta Dago", code="BDG", status="operational"))
        db.flush()
        return seed_user_headers(db, "admin@beta.test", "admin", name="Beta Admin")


def _routes(method):
    """(path, path-parameter names) for every route with `method`, from the
    OpenAPI schema — it lists every endpoint, including included routers."""
    for path, ops in app.openapi()["paths"].items():
        op = ops.get(method.lower())
        if op is None or not path.startswith("/api") or path.startswith(SKIP_PREFIXES):
            continue
        yield path, [p["name"] for p in op.get("parameters", []) if p["in"] == "path"]


def _leaked(body: str, alpha_ids: set) -> set:
    return set(UUID_RE.findall(body)) & alpha_ids


def test_alpha_has_data_in_many_tables(alpha_data):
    assert len(alpha_data) >= 25


def test_no_list_endpoint_returns_another_companys_rows(client, alpha_data, beta_admin):
    checked, leaks = 0, {}
    for path, params in _routes("GET"):
        if params:
            continue
        res = client.get(path, headers=beta_admin)
        checked += 1
        if res.status_code < 300 and (found := _leaked(res.text, alpha_data)):
            leaks[path] = sorted(found)[:3]
    assert leaks == {}, f"cross-company data in: {leaks}"
    assert checked >= 30


def test_no_single_record_endpoint_reaches_another_companys_rows(client, alpha_data, beta_admin):
    checked, leaks = 0, {}
    for method in ("GET", "PATCH", "DELETE"):
        for path, params in _routes(method):
            if len(params) != 1:
                continue
            name = params[0]
            for alpha_id in alpha_data:
                url = path.replace("{" + name + "}", alpha_id)
                res = client.request(method, url, headers=beta_admin, json={} if method == "PATCH" else None)
                checked += 1
                if res.status_code < 300 and (method != "GET" or _leaked(res.text, alpha_data)):
                    leaks.setdefault(f"{method} {path}", res.status_code)
    assert leaks == {}, f"cross-company access via: {leaks}"
    assert checked >= 500
