"""e2e tests — Work Order full lifecycle.

Flow tested:
  Create asset → create WO → transition scheduled→in-progress (asset=maintenance,
  downtime_start set) → add checklist → toggle done → update cost →
  transition in-progress→completed (asset=operational, downtime_end set, total_cost).

Also tests:
  - illegal transition → 409
  - on-hold transitions
"""

import pytest
from tests.conftest import register_and_login


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mgr(client):
    return register_and_login(client, "mgr_wo@test.test", "Pass1234!", "manager")


@pytest.fixture()
def asset_id(client, mgr):
    res = client.post("/api/assets", json={
        "name": "Freezer Dago",
        "category": "Refrigeration",
        "outlet": "Dago",
        "status": "operational",
    }, headers=mgr)
    assert res.status_code == 201
    return res.json()["id"]


@pytest.fixture()
def wo_id(client, mgr, asset_id):
    res = client.post("/api/work-orders", json={
        "assetId": asset_id,
        "type": "corrective",
        "title": "Freezer mati total",
        "description": "Tidak ada pendinginan sama sekali",
        "priority": "critical",
        "assignee": "Budi Teknisi",
        "estimatedCost": 500000,
    }, headers=mgr)
    assert res.status_code == 201
    return res.json()["id"]


# ---------------------------------------------------------------------------
# 1. Create & basic fields
# ---------------------------------------------------------------------------

class TestCreateWorkOrder:
    def test_number_format(self, client, mgr, asset_id):
        res = client.post("/api/work-orders", json={
            "assetId": asset_id, "title": "Test WO", "description": "",
            "priority": "medium", "assignee": "X",
        }, headers=mgr)
        assert res.status_code == 201
        assert res.json()["number"].startswith("WO-")

    def test_initial_status_scheduled(self, client, mgr, asset_id):
        res = client.post("/api/work-orders", json={
            "assetId": asset_id, "title": "T", "description": "",
            "priority": "low", "assignee": "Y",
        }, headers=mgr)
        assert res.json()["status"] == "scheduled"

    def test_cost_fields_default_zero(self, client, wo_id, mgr):
        res = client.get(f"/api/work-orders/{wo_id}", headers=mgr)
        data = res.json()
        assert data["laborCost"] == 0
        assert data["partsCost"] == 0
        assert data["totalCost"] == 0
        assert data["requiresApproval"] is False


# ---------------------------------------------------------------------------
# 2. Transition — state machine enforcement
# ---------------------------------------------------------------------------

class TestTransition:
    def test_scheduled_to_in_progress(self, client, mgr, wo_id, asset_id):
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "in-progress"}, headers=mgr)
        assert res.status_code == 200
        assert res.json()["status"] == "in-progress"

    def test_in_progress_sets_downtime_start(self, client, mgr, wo_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "in-progress"}, headers=mgr)
        res = client.get(f"/api/work-orders/{wo_id}", headers=mgr)
        assert res.json()["downtimeStart"] is not None

    def test_asset_status_becomes_maintenance(self, client, mgr, wo_id, asset_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "in-progress"}, headers=mgr)
        asset = client.get(f"/api/assets/{asset_id}", headers=mgr).json()
        assert asset["status"] == "maintenance"

    def test_illegal_transition_returns_409(self, client, mgr, wo_id):
        # scheduled → completed is illegal
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "completed"}, headers=mgr)
        assert res.status_code == 409

    def test_unknown_status_returns_422(self, client, mgr, wo_id):
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "flying"}, headers=mgr)
        assert res.status_code == 422

    def test_on_hold_then_in_progress(self, client, mgr, wo_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "on-hold"}, headers=mgr)
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "in-progress"}, headers=mgr)
        assert res.status_code == 200
        assert res.json()["status"] == "in-progress"


# ---------------------------------------------------------------------------
# 3. Checklist
# ---------------------------------------------------------------------------

class TestChecklist:
    def _create_item(self, client, mgr, wo_id, title="Periksa freon", order=0):
        return client.post(f"/api/work-orders/{wo_id}/checklist",
                           json={"title": title, "orderIndex": order},
                           headers=mgr)

    def test_add_item_returns_201(self, client, mgr, wo_id):
        res = self._create_item(client, mgr, wo_id)
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Periksa freon"
        assert data["isDone"] is False

    def test_toggle_done(self, client, mgr, wo_id):
        item_id = self._create_item(client, mgr, wo_id).json()["id"]
        res = client.patch(f"/api/work-orders/{wo_id}/checklist/{item_id}",
                           json={"isDone": True}, headers=mgr)
        assert res.status_code == 200
        data = res.json()
        assert data["isDone"] is True
        assert data["doneAt"] is not None

    def test_toggle_undone(self, client, mgr, wo_id):
        item_id = self._create_item(client, mgr, wo_id).json()["id"]
        client.patch(f"/api/work-orders/{wo_id}/checklist/{item_id}",
                     json={"isDone": True}, headers=mgr)
        res = client.patch(f"/api/work-orders/{wo_id}/checklist/{item_id}",
                           json={"isDone": False}, headers=mgr)
        assert res.json()["isDone"] is False
        assert res.json()["doneAt"] is None

    def test_checklist_items_appear_in_detail(self, client, mgr, wo_id):
        self._create_item(client, mgr, wo_id, "Step 1", order=0)
        self._create_item(client, mgr, wo_id, "Step 2", order=1)
        detail = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert len(detail["checklistItems"]) == 2
        assert detail["checklistItems"][0]["orderIndex"] == 0


# ---------------------------------------------------------------------------
# 4. Cost update
# ---------------------------------------------------------------------------

class TestCostUpdate:
    def test_update_cost_fields(self, client, mgr, wo_id):
        res = client.patch(f"/api/work-orders/{wo_id}/cost",
                           json={"laborHours": 3.5, "laborCost": 350000, "partsCost": 200000},
                           headers=mgr)
        assert res.status_code == 200
        data = res.json()
        assert data["laborHours"] == 3.5
        assert data["laborCost"] == 350000
        assert data["partsCost"] == 200000
        assert data["totalCost"] == 550000

    def test_partial_cost_update(self, client, mgr, wo_id):
        client.patch(f"/api/work-orders/{wo_id}/cost",
                     json={"laborCost": 100000}, headers=mgr)
        res = client.patch(f"/api/work-orders/{wo_id}/cost",
                           json={"partsCost": 50000}, headers=mgr)
        data = res.json()
        assert data["laborCost"] == 100000
        assert data["partsCost"] == 50000
        assert data["totalCost"] == 150000


# ---------------------------------------------------------------------------
# 5. Complete — asset restored, downtime_end set
# ---------------------------------------------------------------------------

class TestComplete:
    def test_complete_sets_downtime_end(self, client, mgr, wo_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "in-progress"}, headers=mgr)
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "completed"}, headers=mgr)
        assert res.status_code == 200
        assert res.json()["downtimeEnd"] is not None

    def test_complete_restores_asset_operational(self, client, mgr, wo_id, asset_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "in-progress"}, headers=mgr)
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "completed"}, headers=mgr)
        asset = client.get(f"/api/assets/{asset_id}", headers=mgr).json()
        assert asset["status"] == "operational"

    def test_completed_terminal_transition_rejected(self, client, mgr, wo_id):
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "in-progress"}, headers=mgr)
        client.patch(f"/api/work-orders/{wo_id}/transition",
                     json={"targetStatus": "completed"}, headers=mgr)
        res = client.patch(f"/api/work-orders/{wo_id}/transition",
                           json={"targetStatus": "in-progress"}, headers=mgr)
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# 6. Attachments
# ---------------------------------------------------------------------------

class TestAttachments:
    def test_add_attachment(self, client, mgr, wo_id):
        res = client.post(f"/api/work-orders/{wo_id}/attachments",
                          json={"fileUrl": "https://storage.example.com/photo.jpg",
                                "caption": "Foto kerusakan"},
                          headers=mgr)
        assert res.status_code == 201
        data = res.json()
        assert data["fileUrl"] == "https://storage.example.com/photo.jpg"
        assert data["caption"] == "Foto kerusakan"

    def test_attachments_appear_in_detail(self, client, mgr, wo_id):
        client.post(f"/api/work-orders/{wo_id}/attachments",
                    json={"fileUrl": "https://storage.example.com/a.jpg"},
                    headers=mgr)
        detail = client.get(f"/api/work-orders/{wo_id}", headers=mgr).json()
        assert len(detail["attachments"]) == 1


# ---------------------------------------------------------------------------
# 7. Asset history & summary
# ---------------------------------------------------------------------------

class TestAssetHistoryAndSummary:
    def test_history_returns_work_orders(self, client, mgr, wo_id, asset_id):
        res = client.get(f"/api/assets/{asset_id}/history", headers=mgr)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        assert any(wo["id"] == wo_id for wo in data["items"])

    def test_history_pagination(self, client, mgr, asset_id):
        res = client.get(f"/api/assets/{asset_id}/history?page=1&page_size=1", headers=mgr)
        assert res.json()["pageSize"] == 1

    def test_summary_total_work_orders(self, client, mgr, wo_id, asset_id):
        res = client.get(f"/api/assets/{asset_id}/summary", headers=mgr)
        assert res.status_code == 200
        data = res.json()
        assert data["totalWorkOrders"] >= 1

    def test_summary_cost_aggregation(self, client, mgr, wo_id, asset_id):
        client.patch(f"/api/work-orders/{wo_id}/cost",
                     json={"laborCost": 200000, "partsCost": 100000}, headers=mgr)
        res = client.get(f"/api/assets/{asset_id}/summary", headers=mgr).json()
        assert res["totalCost"] == 300000
