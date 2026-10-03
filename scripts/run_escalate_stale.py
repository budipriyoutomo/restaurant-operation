#!/usr/bin/env python3
"""Approval escalation runner — the scheduled entry point.

Flags pending approvals whose active step has waited longer than
ESCALATION_THRESHOLD_DAYS and notifies admins.

Run from the backend/ directory:
    python -m scripts.run_escalate_stale

Deployed as the `approval-escalator` compose service (hourly loop) or the
systemd timer in deploy/restaurantops-escalate.timer. Safe to run as often as
you like: an already-escalated request is skipped until it advances to its next
step, so repeated runs never send duplicate escalation notices.

Exit codes: 0 = ok (including "nothing stale"), 1 = failure.
"""

import os
import sys

# Ensure the backend/ directory is on the path when running as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.tenancy import for_each_company
from app.database import SessionLocal
from app.services.approval_service import ESCALATION_THRESHOLD_DAYS, escalate_stale_approvals


def run(db, threshold_days: int = ESCALATION_THRESHOLD_DAYS) -> list:
    """Escalate stale approvals on the given session. Returns the escalated rows.

    Split out from main() so the scheduled behaviour is testable against the
    test database without opening a real application session.
    """
    # Every active company, each in its own context (Todo-Pilot §11).
    return for_each_company(db, lambda: escalate_stale_approvals(db, threshold_days=threshold_days))


def main() -> int:
    db = SessionLocal()
    try:
        escalated = run(db)
        if escalated:
            print(f"Approval escalator: {len(escalated)} approval(s) escalated")
            for approval in escalated:
                print(f"  {approval.number}  {approval.title}")
        else:
            print("Approval escalator: nothing stale")
        return 0
    except Exception as exc:                      # noqa: BLE001 — surface to systemd/docker logs
        print(f"Approval escalator FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
