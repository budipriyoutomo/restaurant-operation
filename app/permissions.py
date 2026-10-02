"""Module registry for role-based access (dynamic roles).

A role maps each module key to an access level:

    none   : module hidden, its endpoints return 403
    view   : read + the day-to-day actions every operator does (report an issue,
             tick a checklist item, update own task)
    manage : create / edit / delete / decide

Module keys match the frontend page ids, so the sidebar can ask "can I view
page X?" without a second mapping. A few keys (vendors, budgets) are not pages
of their own — they are panels inside the procurement page that historically
had their own write rules.

This file is the single source of truth for which modules exist. Adding a
module = add it here, guard its endpoints with require_permission(), and give
it a page id on the frontend.
"""

from __future__ import annotations

from typing import Dict, List, TypedDict

NONE = "none"
VIEW = "view"
MANAGE = "manage"

LEVELS = (NONE, VIEW, MANAGE)
_RANK = {NONE: 0, VIEW: 1, MANAGE: 2}

# Approval tier a role acts as inside approval workflows / notifications. These
# are the values of the `approver_role` Postgres enum, so they stay fixed even
# though role keys are dynamic.
APPROVAL_TIERS = ("staff", "manager", "admin")

# Roles shipped by migration 031. They cannot be deleted; `admin` additionally
# cannot be edited (it is the lock-out safety net).
SYSTEM_ROLES = ("staff", "manager", "admin")
SUPERUSER_ROLE = "admin"


class ModuleDef(TypedDict):
    key: str
    label: str
    group: str
    # False when the module has no write actions of its own (dashboard,
    # analytics, issue-category views) — the UI then offers only none/view.
    manageable: bool


MODULES: List[ModuleDef] = [
    {"key": "dashboard",     "label": "Executive Dashboard", "group": "Operations", "manageable": False},
    {"key": "issues",        "label": "Issues",              "group": "Operations", "manageable": True},
    {"key": "tasks",         "label": "Tasks",               "group": "Operations", "manageable": False},
    {"key": "approvals",     "label": "Approvals",           "group": "Operations", "manageable": True},
    {"key": "maintenance",   "label": "Maintenance",         "group": "Modules",    "manageable": False},
    {"key": "qa",            "label": "QA & Compliance",     "group": "Modules",    "manageable": True},   # manage = run audits, edit templates
    {"key": "guest-service", "label": "Guest Service",       "group": "Modules",    "manageable": True},   # manage = record compensation
    {"key": "it-support",    "label": "IT Support",          "group": "Modules",    "manageable": False},
    {"key": "procurement",   "label": "Procurement",         "group": "Modules",    "manageable": True},
    {"key": "vendors",       "label": "Vendors",             "group": "Modules",    "manageable": True},
    {"key": "budgets",       "label": "Budgets",             "group": "Modules",    "manageable": True},
    {"key": "training",      "label": "Training",            "group": "Modules",    "manageable": True},
    {"key": "marketing",     "label": "Marketing",           "group": "Modules",    "manageable": True},
    {"key": "assets",        "label": "Asset Purchase & Registry", "group": "Modules", "manageable": True},  # page = Asset Purchase; manage = add assets in CMMS
    {"key": "cmms",          "label": "CMMS",                "group": "Modules",    "manageable": True},
    {"key": "analytics",     "label": "Analytics",           "group": "Insights",   "manageable": False},
    {"key": "reports",       "label": "Reports & Audit Log", "group": "Insights",   "manageable": False},
    {"key": "master-data",   "label": "Master Data",         "group": "System",     "manageable": True},
    {"key": "users",         "label": "Users & Roles",       "group": "System",     "manageable": True},
    {"key": "settings",      "label": "Settings",            "group": "System",     "manageable": True},
]

MODULE_KEYS = [m["key"] for m in MODULES]
_MANAGEABLE = {m["key"]: m["manageable"] for m in MODULES}


def rank(level: str) -> int:
    return _RANK.get(level, 0)


def max_level(module: str) -> str:
    return MANAGE if _MANAGEABLE.get(module, False) else VIEW


def normalize(perms: Dict[str, str] | None) -> Dict[str, str]:
    """Full module→level map: unknown modules dropped, missing ones = none,
    levels clamped to what the module supports."""
    perms = perms or {}
    out: Dict[str, str] = {}
    for key in MODULE_KEYS:
        level = perms.get(key, NONE)
        if level not in _RANK:
            level = NONE
        if rank(level) > rank(max_level(key)):
            level = max_level(key)
        out[key] = level
    return out


def full_access() -> Dict[str, str]:
    return {key: max_level(key) for key in MODULE_KEYS}


# Defaults reproduce the pre-031 hardcoded behaviour of staff/manager/admin.
_STAFF_VIEW = [
    "dashboard", "issues", "tasks", "approvals",
    "maintenance", "qa", "guest-service", "it-support",
    "procurement", "vendors", "budgets", "training", "marketing", "assets", "cmms",
]
_MANAGER_MANAGE = [
    "issues", "approvals", "procurement", "vendors",
    "training", "marketing", "assets", "cmms", "qa", "guest-service",
]

DEFAULT_PERMISSIONS: Dict[str, Dict[str, str]] = {
    "staff": normalize({k: VIEW for k in _STAFF_VIEW}),
    "manager": normalize({
        **{k: VIEW for k in _STAFF_VIEW},
        **{k: MANAGE for k in _MANAGER_MANAGE},
        "analytics": VIEW,
        "reports": VIEW,
    }),
    "admin": full_access(),
}
