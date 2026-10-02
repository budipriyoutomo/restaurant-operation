"""Unit tests — Task → Issue status roll-up (Todo-Next §2.1).

Purely in-memory: no database, no HTTP client.

Rule: when every Task of an Issue is resolved/closed, the Issue becomes
`resolved` automatically — unless something else still blocks it:
  - an Approval that is still pending
  - an active Work Order (scheduled / in-progress / on-hold)
An Issue that is already resolved/closed is never touched, and an Issue
without Tasks is never auto-resolved (nothing to roll up).
The roll-up only moves forward: reopening a Task does not reopen the Issue.
"""

from app.services.issue_service import derive_issue_status_from_tasks as derive


def test_all_tasks_resolved_resolves_issue():
    assert derive("in-progress", ["resolved", "resolved"], None, 0) == "resolved"


def test_closed_tasks_count_as_done():
    assert derive("assigned", ["resolved", "closed"], None, 0) == "resolved"


def test_some_tasks_open_keeps_issue():
    assert derive("in-progress", ["resolved", "in-progress"], None, 0) is None


def test_no_tasks_never_auto_resolves():
    assert derive("open", [], None, 0) is None


def test_pending_approval_blocks():
    assert derive("in-progress", ["resolved"], "pending", 0) is None


def test_decided_approval_does_not_block():
    assert derive("in-progress", ["resolved"], "approved", 0) == "resolved"
    assert derive("waiting", ["resolved"], "rejected", 0) == "resolved"


def test_active_work_order_blocks():
    assert derive("in-progress", ["resolved"], None, 1) is None


def test_already_resolved_or_closed_is_untouched():
    assert derive("resolved", ["resolved"], None, 0) is None
    assert derive("closed", ["resolved"], None, 0) is None


def test_reopened_task_does_not_reopen_issue():
    assert derive("resolved", ["in-progress"], None, 0) is None
