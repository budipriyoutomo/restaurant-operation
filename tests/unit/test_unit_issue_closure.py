"""Unit tests — can_close_issue (Todo-Pilot §1).

Purely in-memory. An Issue may close only when every derived record is terminal:
  Task: resolved | closed | cancelled
  Work Order: completed | cancelled
  Approval: approved | rejected
"""

import itertools
from types import SimpleNamespace as NS

import pytest

from app.services.issue_closure_service import (
    APPROVAL_TERMINAL,
    TASK_TERMINAL,
    WORK_ORDER_TERMINAL,
    can_close_issue,
)

TASK_STATUSES = ["open", "assigned", "in-progress", "waiting", "resolved", "closed", "cancelled"]
WO_STATUSES = ["scheduled", "in-progress", "on-hold", "completed", "cancelled"]
APPROVAL_STATUSES = [None, "pending", "approved", "rejected"]


def _issue(task=None, wo=None, approval=None):
    return NS(
        tasks=[NS(id="t1", number="TSK-1", status=task)] if task else [],
        work_orders=[NS(id="w1", number="WO-1", status=wo)] if wo else [],
        approval=NS(id="a1", number="APR-1", status=approval) if approval else None,
    )


@pytest.mark.parametrize(
    "task,wo,approval",
    list(itertools.product([None, *TASK_STATUSES], [None, *WO_STATUSES], APPROVAL_STATUSES)),
)
def test_every_status_combination(task, wo, approval):
    result = can_close_issue(_issue(task, wo, approval))
    expected_blockers = []
    if task and task not in TASK_TERMINAL:
        expected_blockers.append("TSK-1")
    if wo and wo not in WORK_ORDER_TERMINAL:
        expected_blockers.append("WO-1")
    if approval and approval not in APPROVAL_TERMINAL:
        expected_blockers.append("APR-1")
    assert [b.number for b in result.blockers] == expected_blockers
    assert result.can_close == (not expected_blockers)


def test_issue_without_children_can_close():
    assert can_close_issue(_issue()).can_close


def test_message_names_the_work_order():
    result = can_close_issue(_issue(task="resolved", wo="in-progress"))
    assert not result.can_close
    assert "WO-1 (in-progress)" in result.message()
    assert result.blockers[0].as_dict() == {"type": "work_order", "id": "w1", "number": "WO-1", "status": "in-progress"}


def test_terminal_sets_match_spec():
    assert TASK_TERMINAL == {"resolved", "closed", "cancelled"}
    assert WORK_ORDER_TERMINAL == {"completed", "cancelled"}
    assert APPROVAL_TERMINAL == {"approved", "rejected"}
