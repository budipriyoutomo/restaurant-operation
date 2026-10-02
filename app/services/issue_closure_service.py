"""Issue closure — when may an Issue be resolved/closed, and what follows (Todo-Pilot §1).

An Issue is the root of the Issue → Task → Approval → Work Order cascade. It may
only move to `resolved`/`closed` once every record derived from it is terminal:

    Task          resolved | closed | cancelled
    Work Order    completed | cancelled
    Approval      approved | rejected

Pure functions (no DB):
  can_close_issue                  — which derived records still block closure
  derive_issue_status_from_tasks   — Task → Issue roll-up rule (Todo-Next §2.1)

DB functions:
  rollup_issue_from_tasks  — applies the roll-up inside the caller's transaction
  on_child_completed       — roll-up + "Issue siap ditutup" notice; called when a
                             Task / WO / Approval reaches a *successful* end state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.enums import IssueStatusEnum
from app.services.audit_service import write_audit
from app.services.notification_service import notify_issue_ready_to_close, notify_issue_status_changed


TASK_TERMINAL = frozenset({"resolved", "closed", "cancelled"})
WORK_ORDER_TERMINAL = frozenset({"completed", "cancelled"})
APPROVAL_TERMINAL = frozenset({"approved", "rejected"})

# Issue statuses that end the Issue's life. `resolved` can still be reopened.
ISSUE_CLOSING = frozenset({"resolved", "closed"})
ISSUE_FINAL = frozenset({"resolved", "closed", "cancelled"})

_ACTIVE_WO_STATUSES = {"scheduled", "in-progress", "on-hold"}


def _value(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


# ---------------------------------------------------------------------------
# Closure check (pure)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClosureBlocker:
    kind: str      # "task" | "work_order" | "approval"
    id: str
    number: str
    status: str

    def as_dict(self) -> dict:
        return {"type": self.kind, "id": self.id, "number": self.number, "status": self.status}


@dataclass(frozen=True)
class ClosureCheckResult:
    blockers: List[ClosureBlocker] = field(default_factory=list)

    @property
    def can_close(self) -> bool:
        return not self.blockers

    def message(self) -> str:
        if self.can_close:
            return "Issue can be closed."
        listed = ", ".join(f"{b.number} ({b.status})" for b in self.blockers)
        return f"Issue cannot be closed while derived records are still open: {listed}"


def can_close_issue(issue) -> ClosureCheckResult:
    """Return every derived record of `issue` that is not yet terminal.

    Duck-typed: needs .tasks, .work_orders (iterables) and .approval (or None),
    each item with .id, .number and .status.
    """
    blockers: List[ClosureBlocker] = []
    for t in issue.tasks or []:
        if _value(t.status) not in TASK_TERMINAL:
            blockers.append(ClosureBlocker("task", str(t.id), t.number, _value(t.status)))
    for wo in issue.work_orders or []:
        if _value(wo.status) not in WORK_ORDER_TERMINAL:
            blockers.append(ClosureBlocker("work_order", str(wo.id), wo.number, _value(wo.status)))
    approval = getattr(issue, "approval", None)
    if approval is not None and _value(approval.status) not in APPROVAL_TERMINAL:
        blockers.append(ClosureBlocker("approval", str(approval.id), approval.number, _value(approval.status)))
    return ClosureCheckResult(blockers)


def has_children(issue) -> bool:
    return bool(issue.tasks) or bool(issue.work_orders) or getattr(issue, "approval", None) is not None


# ---------------------------------------------------------------------------
# Task → Issue roll-up (Todo-Next §2.1)
# ---------------------------------------------------------------------------

def derive_issue_status_from_tasks(
    issue_status: str,
    task_statuses: List[str],
    approval_status: Optional[str],
    active_work_orders: int,
) -> Optional[str]:
    """Return the status the Issue should move to, or None to leave it alone.

    Pure function (no DB). The Issue auto-resolves only when it has at least one
    Task, every Task is terminal, no Approval is pending and no Work Order is
    still active. Forward-only: an already resolved/closed/cancelled Issue is
    never changed, so reopening a Task does not reopen its Issue.

    Auto-resolve is not auto-close: `closed` stays a Manager decision, and the
    Manager is told the Issue is ready (on_child_completed).
    """
    if issue_status in ISSUE_FINAL:
        return None
    if not task_statuses or any(s not in TASK_TERMINAL for s in task_statuses):
        return None
    if approval_status == "pending":
        return None
    if active_work_orders > 0:
        return None
    return IssueStatusEnum.resolved.value


def rollup_issue_from_tasks(db: Session, issue) -> bool:
    """Apply derive_issue_status_from_tasks to a loaded Issue.

    Writes the status change, its audit entry (performed_by="system") and the
    usual status-change notification into the caller's transaction — the caller
    commits. Returns True when the Issue status changed.
    """
    db.flush()
    db.refresh(issue)  # see the Task change the caller just made
    old_status = _value(issue.status)
    new_status = derive_issue_status_from_tasks(
        old_status,
        [_value(t.status) for t in issue.tasks],
        _value(issue.approval.status) if issue.approval else None,
        sum(1 for wo in issue.work_orders if _value(wo.status) in _ACTIVE_WO_STATUSES),
    )
    if new_status is None:
        return False

    issue.status = new_status
    write_audit(
        db,
        table_name="issues",
        record_id=str(issue.id),
        action="status_change",
        old_value={"status": old_status, "number": issue.number},
        new_value={"status": new_status, "number": issue.number, "reason": "all_tasks_done"},
    )
    notify_issue_status_changed(db, issue.number, issue.title, old_status, new_status, issue.id)
    return True


def on_child_completed(db: Session, issue) -> None:
    """A Task, Work Order or Approval of `issue` just reached a successful end.

    Runs the roll-up, then — if nothing derived is still open — tells the
    Issue's managers it is ready to close. One-way: the Issue is never closed
    here. Cancel/reject paths do not call this (a rejected approval must not
    look like finished work). Caller commits.
    """
    rollup_issue_from_tasks(db, issue)  # flushes + refreshes the issue
    if _value(issue.status) in ("closed", "cancelled"):
        return
    if has_children(issue) and can_close_issue(issue).can_close:
        notify_issue_ready_to_close(db, issue)
