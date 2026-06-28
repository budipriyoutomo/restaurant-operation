"""Integration tests: Outlet/Category/PIC CRUD with soft-delete and audit log."""

import pytest

_OUTLET = {"name": "Test Outlet KL", "code": "TESTKL", "status": "operational"}
_CATEGORY = {"name": "Test Category", "description": "For testing", "type": "operations"}
_PIC = {"name": "Ahmad Test", "email": "ahmad.test@restaurantops.test",
        "phone": "+60123456789", "department": "Operations", "categories": []}


# ── Outlets ───────────────────────────────────────────────────────────────────

class TestOutlets:
    def test_create_outlet(self, client):
        res = client.post("/api/outlets", json=_OUTLET)
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == _OUTLET["name"]
        assert data["code"] == "TESTKL"

    def test_duplicate_code_returns_409(self, client):
        client.post("/api/outlets", json=_OUTLET)
        res = client.post("/api/outlets", json=_OUTLET)
        assert res.status_code == 409

    def test_list_outlets(self, client):
        client.post("/api/outlets", json=_OUTLET)
        res = client.get("/api/outlets")
        assert res.status_code == 200
        assert any(o["code"] == "TESTKL" for o in res.json())

    def test_get_outlet_by_id(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        res = client.get(f"/api/outlets/{outlet_id}")
        assert res.status_code == 200
        assert res.json()["id"] == outlet_id

    def test_update_outlet(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        res = client.patch(f"/api/outlets/{outlet_id}", json={"status": "warning"})
        assert res.status_code == 200
        assert res.json()["status"] == "warning"

    def test_soft_delete_outlet(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        res = client.delete(f"/api/outlets/{outlet_id}")
        assert res.status_code == 204

    def test_soft_deleted_outlet_not_in_list(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        client.delete(f"/api/outlets/{outlet_id}")
        res = client.get("/api/outlets")
        assert not any(o["id"] == outlet_id for o in res.json())

    def test_soft_deleted_outlet_returns_404_on_get(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        client.delete(f"/api/outlets/{outlet_id}")
        res = client.get(f"/api/outlets/{outlet_id}")
        assert res.status_code == 404

    def test_create_outlet_creates_audit_log(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        logs = client.get(f"/api/audit-logs?table_name=outlets&record_id={outlet_id}").json()
        assert any(log["action"] == "create" for log in logs)

    def test_delete_outlet_creates_audit_log(self, client):
        outlet_id = client.post("/api/outlets", json=_OUTLET).json()["id"]
        client.delete(f"/api/outlets/{outlet_id}")
        logs = client.get(f"/api/audit-logs?table_name=outlets&record_id={outlet_id}").json()
        assert any(log["action"] == "delete" for log in logs)


# ── Categories ────────────────────────────────────────────────────────────────

class TestCategories:
    def test_create_category(self, client):
        res = client.post("/api/categories", json=_CATEGORY)
        assert res.status_code == 201
        assert res.json()["name"] == _CATEGORY["name"]

    def test_soft_delete_category(self, client):
        cat_id = client.post("/api/categories", json=_CATEGORY).json()["id"]
        res = client.delete(f"/api/categories/{cat_id}")
        assert res.status_code == 204

    def test_soft_deleted_category_not_in_list(self, client):
        cat_id = client.post("/api/categories", json=_CATEGORY).json()["id"]
        client.delete(f"/api/categories/{cat_id}")
        ids = [c["id"] for c in client.get("/api/categories").json()]
        assert cat_id not in ids

    def test_update_category(self, client):
        cat_id = client.post("/api/categories", json=_CATEGORY).json()["id"]
        res = client.patch(f"/api/categories/{cat_id}", json={"name": "Renamed Category"})
        assert res.json()["name"] == "Renamed Category"


# ── PICs ──────────────────────────────────────────────────────────────────────

class TestPICs:
    def test_create_pic(self, client):
        res = client.post("/api/pics", json=_PIC)
        assert res.status_code == 201
        assert res.json()["email"] == _PIC["email"]

    def test_duplicate_email_returns_409(self, client):
        client.post("/api/pics", json=_PIC)
        res = client.post("/api/pics", json=_PIC)
        assert res.status_code == 409

    def test_soft_delete_pic(self, client):
        pic_id = client.post("/api/pics", json=_PIC).json()["id"]
        res = client.delete(f"/api/pics/{pic_id}")
        assert res.status_code == 204

    def test_soft_deleted_pic_not_in_list(self, client):
        pic_id = client.post("/api/pics", json=_PIC).json()["id"]
        client.delete(f"/api/pics/{pic_id}")
        ids = [p["id"] for p in client.get("/api/pics").json()]
        assert pic_id not in ids

    def test_pic_with_category(self, client):
        cat_id = client.post("/api/categories", json=_CATEGORY).json()["id"]
        pic_with_cat = {**_PIC, "categories": [cat_id]}
        res = client.post("/api/pics", json=pic_with_cat)
        assert res.status_code == 201
        assert cat_id in res.json()["categories"]
