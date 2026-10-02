#!/usr/bin/env python3
"""Compare the enum value sets in Python with frontend/lib/types.ts.

The backend and frontend spell the same enums separately; nothing tied them
together, so a value added on one side only (the missing `on-hold` work-order
status) shipped unnoticed. This check parses every string-literal union in
types.ts and requires, for each one:

  - a mapping in PAIRS to one or more Python sources, all with exactly the
    same values — or an explicit entry in TS_ONLY (frontend-only types);
  - no mapped type silently disappearing from types.ts.

Python sources are "module:attr[.attr]" refs to an Enum class, an SQLAlchemy
Enum column, a tuple/set constant, a typing.Literal, or a Pydantic field
annotated with a Literal.

Run from backend/ (no database needed):
    python -m scripts.check_enum_parity [path/to/types.ts]
Exit code 1 when the sides disagree — suitable for CI. The same check runs in
the test suite (tests/unit/test_unit_enum_parity.py).
"""

import enum
import importlib
import os
import re
import sys
import types
import typing
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "types.ts"

# TS type → Python sources that must hold exactly the same values.
PAIRS: Dict[str, List[str]] = {
    # Issue core
    "Priority":             ["app.models.enums:PriorityEnum", "app.schemas.guest_case:CreateGuestCaseRequest.priority"],
    "IssueStatus":          ["app.models.enums:IssueStatusEnum"],
    "TaskStatus":           ["app.models.enums:TaskStatusEnum"],
    "IssueCategory":        ["app.models.enums:IssueCategoryEnum"],
    "ApprovalType":         ["app.models.enums:ApprovalTypeEnum"],
    "ApprovalStatus":       ["app.models.enums:ApprovalStatusEnum"],
    "ApprovalStepStatus":   ["app.models.enums:ApprovalStepStatusEnum"],
    "ApproverRole":         ["app.models.enums:ApproverRoleEnum"],
    "ApprovalTier":         ["app.models.enums:ApproverRoleEnum", "app.permissions:APPROVAL_TIERS"],
    "AccessLevel":          ["app.permissions:LEVELS"],
    "NotificationType":     ["app.services.notification_service:NOTIFICATION_TYPES"],
    # Outlets / categories
    "OutletStatus":         ["app.models.enums:OutletStatusEnum"],
    "CategoryType":         ["app.models.enums:CategoryTypeEnum"],
    # CMMS
    "AssetStatus":          ["app.models.enums:AssetStatusEnum"],
    "WorkOrderType":        ["app.models.enums:WorkOrderTypeEnum"],
    "WorkOrderStatus":      ["app.models.enums:WorkOrderStatusEnum"],
    "PMIntervalType":       ["app.models.enums:PMIntervalTypeEnum"],
    "PMTriggerType":        ["app.models.enums:PMTriggerTypeEnum"],
    # Procurement
    "PurchaseRequestStatus": ["app.services.procurement_service:PR_STATUSES"],
    "PurchaseOrderStatus":   ["app.services.procurement_service:PO_STATUSES"],
    # Training / marketing
    "TrainingProgramStatus": ["app.models.training_program:TrainingProgram.status",
                              "app.routers.training_programs:VALID_STATUSES"],
    "EnrollmentStatus":      ["app.services.training_service:ENROLLMENT_STATUSES"],
    "CampaignStatus":        ["app.models.campaign:Campaign.status", "app.routers.campaigns:VALID_STATUSES"],
    "CampaignType":          ["app.models.campaign:Campaign.type", "app.routers.campaigns:VALID_TYPES"],
    # QA / guest service
    "QAAuditResult":         ["app.services.qa_audit_service:RESULTS",
                              "app.schemas.qa_audit:UpdateFindingRequest.result"],
    "GuestChannel":          ["app.services.guest_service:CHANNELS", "app.schemas.guest_case:Channel"],
    "CompensationType":      ["app.services.guest_service:COMPENSATION_TYPES",
                              "app.schemas.guest_case:CompensationType"],
}

# String-literal unions that exist only in the frontend (none today).
TS_ONLY: Set[str] = set()


# ---------------------------------------------------------------------------
# types.ts
# ---------------------------------------------------------------------------

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_DECL = re.compile(r"^export\s+type\s+(\w+)\s*=(.*)$")


def _declarations(src: str):
    """(name, body) per `export type`; the body continues on following `| …` lines."""
    lines = [l for l in src.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = _DECL.match(line)
        if not m:
            continue
        body = [m.group(2)]
        for nxt in lines[i + 1:]:
            if not nxt.strip().startswith("|"):
                break
            body.append(nxt)
        yield m.group(1), " ".join(body)


_LITERAL = re.compile(r"""^\s*(?:'([^']*)'|"([^"]*)")\s*$""")


def parse_ts_unions(src: str) -> Dict[str, Set[str]]:
    """Every `export type X = 'a' | 'b'` (and `export type Y = X` aliases) → {name: values}.
    Unions mixing literals with other types are not enums and are skipped."""
    src = _COMMENTS.sub("", src)
    unions: Dict[str, Set[str]] = {}
    aliases: Dict[str, str] = {}
    for name, body in _declarations(src):
        body = body.strip()
        if re.fullmatch(r"\w+", body):
            aliases[name] = body
            continue
        parts = [p for p in body.split("|") if p.strip()]
        values = [_LITERAL.match(p) for p in parts]
        if parts and all(values):
            unions[name] = {m.group(1) if m.group(1) is not None else m.group(2) for m in values}
    for name, target in aliases.items():
        if target in unions:
            unions[name] = set(unions[target])
    return unions


# ---------------------------------------------------------------------------
# Python sources
# ---------------------------------------------------------------------------

def resolve(ref: str):
    """'pkg.module:Attr.attr' → object. Pydantic fields resolve to their annotation."""
    module_name, _, path = ref.partition(":")
    obj = importlib.import_module(module_name)
    for name in path.split("."):
        fields = getattr(obj, "model_fields", None)
        if isinstance(obj, type) and fields and name in fields:
            obj = fields[name].annotation
        else:
            obj = getattr(obj, name)
    return obj


def values_of(obj) -> Set[str]:
    """Value set of an Enum class, SQLAlchemy Enum column, collection or Literal."""
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return {m.value for m in obj}
    if isinstance(obj, (tuple, list, set, frozenset)):
        return set(obj)
    origin = typing.get_origin(obj)
    if origin is typing.Literal:
        return set(typing.get_args(obj))
    if origin in (typing.Union, types.UnionType):            # Optional[Literal[...]]
        args = [a for a in typing.get_args(obj) if a is not type(None)]
        if len(args) == 1:
            return values_of(args[0])
    col_type = getattr(getattr(obj, "property", None), "columns", [None])[0]
    enums = getattr(getattr(col_type, "type", None), "enums", None)
    if enums is not None:
        return set(enums)
    raise TypeError(f"cannot read enum values from {obj!r}")


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def check(ts_unions: Dict[str, Set[str]], pairs: Dict[str, Iterable[str]], ts_only: Set[str],
          resolver: Callable[[str], object]) -> List[str]:
    problems: List[str] = []
    for name in sorted(ts_unions):
        if name not in pairs and name not in ts_only:
            problems.append(f"types.ts: {name} has no Python mapping — add it to PAIRS or TS_ONLY")
    for name, refs in pairs.items():
        ts_values = ts_unions.get(name)
        if ts_values is None:
            problems.append(f"types.ts: {name} not found (renamed or removed?)")
            continue
        for ref in refs:
            try:
                py_values = values_of(resolver(ref))
            except Exception as exc:  # noqa: BLE001 — report, don't crash the whole check
                problems.append(f"{name} ↔ {ref}: cannot read Python values ({exc})")
                continue
            if py_values != ts_values:
                problems.append(f"{name} ↔ {ref}: only in TS: {sorted(ts_values - py_values)}, "
                                f"only in Python: {sorted(py_values - ts_values)}")
    return problems


def main(argv: List[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_TYPES_TS
    if not path.exists():
        print(f"types.ts not found at {path}")
        return 2
    problems = check(parse_ts_unions(path.read_text()), PAIRS, TS_ONLY, resolve)
    for p in problems:
        print("MISMATCH", p)
    print(f"enum parity check: {len(PAIRS)} enum(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
