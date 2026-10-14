#!/usr/bin/env python3
"""Outlet digest runner — daily/weekly GM summary (Todo-Pilot §25 / UX-12).

Sends each opted-in user (preferences `digestDaily` / `digestWeekly`, with
access to the dashboard) a summary of their outlets: open issues, SLA, QA,
guests waiting, budget, downtime — exceptions first. In-app always; email and
WhatsApp following the user's usual channel preferences.

Run from the backend/ directory:
    python -m scripts.run_outlet_digest

Deployed as the `outlet-digest` compose service (hourly loop). Safe to run as
often as you like: nothing goes out before DIGEST_HOUR (07:00, DIGEST_TZ,
default Asia/Jakarta), and each user gets at most one daily and one Monday
weekly digest per day.

Exit codes: 0 = ok, 1 = failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.tenancy import for_each_company
from app.database import SessionLocal
from app.services.outlet_comparison_service import send_digests


def run(db, now=None) -> list:
    """Every active company, each in its own context (Todo-Pilot §11)."""
    return for_each_company(db, lambda: send_digests(db, now=now))


def main() -> int:
    db = SessionLocal()
    try:
        sent = run(db)
        print(f"Outlet digest: {len(sent)} sent" if sent else "Outlet digest: nothing due")
        return 0
    except Exception as exc:                      # noqa: BLE001 — surface to docker logs
        print(f"Outlet digest FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
