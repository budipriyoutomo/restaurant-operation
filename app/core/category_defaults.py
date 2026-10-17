"""Issue category rules (Todo-Ops-Checklist P1 — categories are master data).

Issue categories live in the `categories` table, one set per company. What a
category *does* is decided by its `module`, never by its name, so an admin can
rename "Maintenance" to "Perbaikan" (or add "HVAC" in the maintenance module)
without switching off corrective work orders:

  maintenance     corrective Work Order on create; Maintenance page
  it_support      IT Support page
  compliance      QA & Compliance page (audit findings, Food Safety)
  guest_service   guest case on create; Guest Service page
  asset_purchase  Asset Purchase page
  general         nothing special

The system categories below exist in every company (migration 044,
ensure_system_categories). They can be renamed and their defaults edited, but
not deleted, deactivated, or moved to another module.
"""

from typing import Dict, Optional, TypedDict

CATEGORY_MODULES = ("maintenance", "it_support", "compliance", "guest_service", "asset_purchase", "general")


class SystemCategory(TypedDict):
    name: str
    description: str
    module: str
    task: bool                    # default "generate Task" toggle
    approval: bool                # default "send to Approval Center" toggle
    approval_type: Optional[str]  # ApprovalType of the generated request; None → procurement
    icon: str                     # lucide icon name (kebab-case)


SYSTEM_CATEGORIES: Dict[str, SystemCategory] = {
    "maintenance":    {"name": "Maintenance",    "description": "Perbaikan peralatan dan fasilitas",  "module": "maintenance",
                       "task": True, "approval": False, "approval_type": "maintenance",    "icon": "wrench"},
    "it_support":     {"name": "IT Support",     "description": "Perangkat, jaringan, dan POS",       "module": "it_support",
                       "task": True, "approval": False, "approval_type": None,             "icon": "monitor"},
    "compliance":     {"name": "Compliance",     "description": "Temuan audit dan kepatuhan",         "module": "compliance",
                       "task": True, "approval": False, "approval_type": None,             "icon": "shield-check"},
    "training":       {"name": "Training",       "description": "Pelatihan staff",                    "module": "general",
                       "task": True, "approval": True,  "approval_type": "training",       "icon": "graduation-cap"},
    "procurement":    {"name": "Procurement",    "description": "Pengadaan barang dan bahan",         "module": "general",
                       "task": True, "approval": True,  "approval_type": "procurement",    "icon": "shopping-cart"},
    "marketing":      {"name": "Marketing",      "description": "Promosi dan kampanye",               "module": "general",
                       "task": True, "approval": True,  "approval_type": "marketing",      "icon": "megaphone"},
    "asset_purchase": {"name": "Asset Purchase", "description": "Pembelian aset baru",                "module": "asset_purchase",
                       "task": True, "approval": True,  "approval_type": "asset-purchase", "icon": "package"},
    "guest_service":  {"name": "Guest Service",  "description": "Keluhan dan pengalaman tamu",        "module": "guest_service",
                       "task": True, "approval": False, "approval_type": None,             "icon": "message-square-warning"},
    "other":          {"name": "Other",          "description": "Lain-lain",                          "module": "general",
                       "task": True, "approval": False, "approval_type": None,             "icon": "circle-help"},
    "food_safety":    {"name": "Food Safety",    "description": "Keamanan pangan dan log suhu",       "module": "compliance",
                       "task": True, "approval": False, "approval_type": None,             "icon": "thermometer"},
}

CATEGORY_SYSTEM_KEYS = tuple(SYSTEM_CATEGORIES)

_KEY_BY_NAME = {d["name"].lower(): key for key, d in SYSTEM_CATEGORIES.items()}

# Fallback ApprovalType when a category has none (the pre-P1 behaviour).
DEFAULT_APPROVAL_TYPE = "procurement"


def system_key_for_name(name: Optional[str]) -> Optional[str]:
    """The system key whose default name this is (case-insensitive), else None.
    Used to adopt existing master-data rows and to backfill issues."""
    if not name:
        return None
    return _KEY_BY_NAME.get(name.strip().lower())


def allows_work_order(module: Optional[str]) -> bool:
    return module == "maintenance"


def approval_type_for(approval_type: Optional[str]) -> str:
    return approval_type or DEFAULT_APPROVAL_TYPE


# categories.type (operations | maintenance) predates module. The API still
# returns it, derived, and accepts it on create when module is not given.
def legacy_type(module: Optional[str]) -> str:
    return "maintenance" if module == "maintenance" else "operations"


def module_from_legacy_type(type_: Optional[str]) -> str:
    return "maintenance" if type_ == "maintenance" else "general"
