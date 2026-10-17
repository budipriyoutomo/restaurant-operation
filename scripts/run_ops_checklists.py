#!/usr/bin/env python3
"""Daily ops checklist runner — the scheduled entry point (Todo-Ops-Checklist Fase 1 §1.6).

For every active company:
  1. generate_due_runs — today's run (outlet-local shift date) for every active
     schedule that is due today and whose window has not closed yet;
  2. remind_due_soon   — runs nobody started, due within 30 minutes: remind staff once;
  3. mark_missed_runs  — runs still open after their window closed are locked and
                         the outlet's managers are told (once).

Run from the backend/ directory:
    python -m scripts.run_ops_checklists

Deployed as a 15-minute loop (compose service ops-checklist-scheduler) or a
systemd timer (deploy/restaurantops-ops.timer). Safe to run as often as you
like: one run per (schedule, shift date) is a UNIQUE constraint, and marking
missed runs is idempotent.

Exit codes: 0 = ok (including "nothing to do"), 1 = failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.tenancy import for_each_company
from app.database import SessionLocal
from app.services.ops_checklist_service import generate_due_runs, mark_missed_runs, remind_due_soon


def run(db) -> tuple:
    """(created runs, missed runs) across every company. Split out for tests."""
    created = for_each_company(db, lambda: generate_due_runs(db))
    for_each_company(db, lambda: remind_due_soon(db))
    missed = for_each_company(db, lambda: mark_missed_runs(db))
    return created, missed


def main() -> int:
    db = SessionLocal()
    try:
        created, missed = run(db)
        print(f"Ops checklists: {len(created)} run(s) created, {len(missed)} marked missed")
        for r in created:
            print(f"  + {r.number}  {r.template_name}  {r.outlet}  {r.run_date}")
        for r in missed:
            print(f"  ! {r.number}  {r.template_name}  {r.outlet}  {r.run_date} missed")
        return 0
    except Exception as exc:                      # noqa: BLE001 — surface to the scheduler
        print(f"Ops checklists FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
