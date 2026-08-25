#!/usr/bin/env python3
"""Preventive-maintenance generator runner — the scheduled entry point.

Creates preventive Work Orders for every PM schedule that is due (calendar) or
whose meter has advanced past its interval (meter-based).

Run from the backend/ directory:
    python -m scripts.run_pm_generator

Deployed as a daily systemd timer (see deploy/restaurantops-pm.timer). Safe to
run as often as you like: the generator is idempotent — a schedule that already
produced a WO for the current period is skipped, so a double run (or a retry
after a failure) never duplicates work orders.

Exit codes: 0 = ok (including "nothing was due"), 1 = failure.
"""

import os
import sys

# Ensure the backend/ directory is on the path when running as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.services.pm_schedule_service import generate_due_preventive_work_orders


def run(db) -> list:
    """Generate due preventive WOs on the given session. Returns the new WOs.

    Split out from main() so the scheduled behaviour is testable against the
    test database without opening a real application session.
    """
    return generate_due_preventive_work_orders(db)


def main() -> int:
    db = SessionLocal()
    try:
        created = run(db)
        if created:
            print(f"PM generator: {len(created)} preventive work order(s) created")
            for wo in created:
                print(f"  {wo.number}  {wo.title}  (asset: {wo.asset_name})")
        else:
            print("PM generator: nothing due")
        return 0
    except Exception as exc:                      # noqa: BLE001 — surface to systemd
        print(f"PM generator FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
