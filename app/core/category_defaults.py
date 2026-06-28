"""Port of CATEGORY_DEFAULTS and CATEGORY_TO_APPROVAL_TYPE from lib/types.ts.

These are used by issue_service to set default toggle values and
determine which ApprovalType to assign when generating an Approval.
"""

from typing import Dict, Optional

# Default suggestion rules: should a new Issue with this category
# auto-generate a Task / an Approval? Mirrors the frontend CATEGORY_DEFAULTS.
CATEGORY_DEFAULTS: Dict[str, Dict[str, bool]] = {
    "Maintenance":    {"task": True,  "approval": False},
    "IT Support":     {"task": True,  "approval": False},
    "Compliance":     {"task": True,  "approval": False},
    "Training":       {"task": True,  "approval": True},
    "Procurement":    {"task": True,  "approval": True},
    "Marketing":      {"task": True,  "approval": True},
    "Asset Purchase": {"task": True,  "approval": True},
    "Guest Service":  {"task": True,  "approval": False},
    "Other":          {"task": True,  "approval": False},
}

# Maps an Issue category to the ApprovalType stored on the generated ApprovalRequest.
# Mirrors the frontend CATEGORY_TO_APPROVAL_TYPE.
CATEGORY_TO_APPROVAL_TYPE: Dict[str, Optional[str]] = {
    "Procurement":    "procurement",
    "Marketing":      "marketing",
    "Training":       "training",
    "Asset Purchase": "asset-purchase",
}


def get_approval_type(category: str) -> str:
    """Return the approval type for a given category, defaulting to 'procurement'."""
    return CATEGORY_TO_APPROVAL_TYPE.get(category, "procurement")
