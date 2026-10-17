"""Pure category rules (Todo-Ops-Checklist P1 — dynamic issue categories).

Behaviour hangs off a category's `module` (and, for the built-in rows, its
`system_key`), never off its name — an admin may rename "Maintenance" to
"Perbaikan" without switching off corrective work orders.
"""

import pytest

from app.core.category_defaults import (
    CATEGORY_MODULES, CATEGORY_SYSTEM_KEYS, SYSTEM_CATEGORIES,
    allows_work_order, approval_type_for, legacy_type, module_from_legacy_type, system_key_for_name,
)
from app.models.enums import ApprovalTypeEnum


class TestSystemCategories:
    def test_every_system_key_has_a_definition(self):
        assert set(SYSTEM_CATEGORIES) == set(CATEGORY_SYSTEM_KEYS)

    def test_food_safety_is_a_system_category_in_the_compliance_module(self):
        assert SYSTEM_CATEGORIES["food_safety"]["module"] == "compliance"

    def test_the_nine_legacy_enum_names_are_all_system_categories(self):
        names = {d["name"] for d in SYSTEM_CATEGORIES.values()}
        assert {"Maintenance", "IT Support", "Compliance", "Training", "Procurement", "Marketing",
                "Asset Purchase", "Guest Service", "Other"} <= names

    @pytest.mark.parametrize("key", CATEGORY_SYSTEM_KEYS)
    def test_definitions_are_valid(self, key):
        d = SYSTEM_CATEGORIES[key]
        assert d["module"] in CATEGORY_MODULES
        assert d["approval_type"] is None or d["approval_type"] in {e.value for e in ApprovalTypeEnum}
        assert isinstance(d["task"], bool) and isinstance(d["approval"], bool)

    def test_defaults_match_the_old_category_defaults(self):
        # Pre-P1 CATEGORY_DEFAULTS / CATEGORY_TO_APPROVAL_TYPE, so existing flows do not change.
        assert (SYSTEM_CATEGORIES["maintenance"]["task"], SYSTEM_CATEGORIES["maintenance"]["approval"]) == (True, False)
        assert (SYSTEM_CATEGORIES["training"]["approval"], SYSTEM_CATEGORIES["training"]["approval_type"]) == (True, "training")
        assert (SYSTEM_CATEGORIES["asset_purchase"]["approval"], SYSTEM_CATEGORIES["asset_purchase"]["approval_type"]) == (True, "asset-purchase")
        assert (SYSTEM_CATEGORIES["guest_service"]["approval"], SYSTEM_CATEGORIES["guest_service"]["module"]) == (False, "guest_service")
        assert SYSTEM_CATEGORIES["maintenance"]["approval_type"] == "maintenance"


class TestSystemKeyForName:
    def test_matches_case_insensitively(self):
        assert system_key_for_name("maintenance") == "maintenance"
        assert system_key_for_name("  Guest Service ") == "guest_service"

    def test_unknown_name_is_none(self):
        assert system_key_for_name("HVAC") is None
        assert system_key_for_name("") is None
        assert system_key_for_name(None) is None


class TestModuleRules:
    def test_only_the_maintenance_module_creates_work_orders(self):
        assert allows_work_order("maintenance") is True
        for m in CATEGORY_MODULES:
            if m != "maintenance":
                assert allows_work_order(m) is False

    def test_approval_type_falls_back_to_procurement(self):
        assert approval_type_for(None) == "procurement"
        assert approval_type_for("") == "procurement"
        assert approval_type_for("marketing") == "marketing"


class TestLegacyType:
    """categories.type (operations | maintenance) was replaced by module; the API
    keeps returning and accepting it so older clients keep working."""

    def test_type_from_module(self):
        assert legacy_type("maintenance") == "maintenance"
        assert legacy_type("general") == "operations"
        assert legacy_type("compliance") == "operations"

    def test_module_from_type(self):
        assert module_from_legacy_type("maintenance") == "maintenance"
        assert module_from_legacy_type("operations") == "general"
        assert module_from_legacy_type(None) == "general"
