#!/usr/bin/env python3
"""WhatsApp retry runner — sends outbox messages that are due (Todo-Pilot §4).

New messages are normally sent right after the commit that created them; this
runner picks up whatever that missed (WuzAPI down, API restarted mid-send) and
retries with backoff until WHATSAPP_MAX_ATTEMPTS, then marks the row failed.

Run from the backend/ directory:
    python -m scripts.run_whatsapp_retry

Deployed as the `whatsapp-retry` compose service (1-minute loop) or the systemd
timer in deploy/restaurantops-whatsapp.timer. Safe to run concurrently with the
API: rows are claimed with SELECT … FOR UPDATE SKIP LOCKED.

Exit codes: 0 = ok (including "nothing due"), 1 = failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.config import settings
from app.database import SessionLocal
from app.services.whatsapp_service import process_due


def run(db) -> dict:
    """Process due messages on the given session (testable without SessionLocal)."""
    return process_due(db)


def main() -> int:
    if settings.whatsapp_backend == "disabled":
        print("WhatsApp retry: disabled (WUZAPI_URL not set)")
        return 0
    db = SessionLocal()
    try:
        counts = run(db)
        if any(counts.values()):
            print(f"WhatsApp retry: sent={counts['sent']} retry={counts['retry']} failed={counts['failed']}")
        return 0
    except Exception as exc:                      # noqa: BLE001 — surface to systemd/docker logs
        print(f"WhatsApp retry FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
