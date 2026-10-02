"""WhatsApp delivery for notifications via WuzAPI (Todo-Pilot §4).

Callers never send directly. notification_service's WhatsAppChannel calls
queue_whatsapp(), which writes a row to whatsapp_outbox in the caller's
transaction:

  - a rollback drops the row, so WhatsApp never announces what did not happen;
  - after commit the row is sent on a background thread, so a slow or broken
    WuzAPI can never fail (or slow down) the user's request;
  - a failed send is retried with backoff by scripts/run_whatsapp_retry
    (compose service `whatsapp-retry`), then marked failed.

The outbox is also the memory for anti-spam rules: one message per
(recipient, event, record) inside WHATSAPP_DEDUP_MINUTES, and at most
WHATSAPP_MAX_PER_HOUR messages per recipient (extra ones are kept as `skipped`).

Backends (settings.whatsapp_backend):
  wuzapi    POST {WUZAPI_URL}/chat/send/text with the Token header
  console   log the message instead of sending it (development)
  memory    append to OUTBOX when process_due runs (tests drain explicitly)
  disabled  queue nothing (default when WUZAPI_URL is empty)
"""

from __future__ import annotations

import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import WhatsAppMessage

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_whatsapp_ids"

# Events that may go out on WhatsApp, mapped to the users.preferences key that
# switches each one. WhatsApp is opt-in: nothing is sent unless the user saved
# a number AND turned on `waEnabled`; the per-event keys then default to on.
WA_EVENT_PREFS = {
    "approval_pending":     "waApprovalPending",
    "approval_escalated":   "waApprovalEscalated",
    "approval_decided":     "waApprovalDecided",
    "work_order_assigned":  "waWorkOrderAssigned",
    "issue_ready_to_close": "waIssueReadyToClose",
}
WA_MASTER_PREF = "waEnabled"

# Retry delays after attempt 1, 2, 3 … (the last one repeats).
BACKOFF_SECONDS = (60, 300, 1800)

# Messages delivered by the "memory" backend. Tests read and clear this.
OUTBOX: List["SentWhatsApp"] = []

_executor: ThreadPoolExecutor | None = None


@dataclass(frozen=True)
class SentWhatsApp:
    phone: str
    body: str


class WhatsAppSendError(RuntimeError):
    """WuzAPI refused or could not be reached."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """'0812-3456-7890' / '+62 812 3456 7890' / '812…' → '6281234567890'.

    Empty → None. Raises ValueError for anything that is not a plausible
    number. Without a '+', a number is taken as Indonesian.
    """
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"[\s\-().]", "", s.lstrip("+"))
    if not digits.isdigit():
        raise ValueError("WhatsApp number may only contain digits, spaces, dashes and a leading +")
    if not has_plus:
        if digits.startswith("0"):
            digits = "62" + digits[1:]
        elif digits.startswith("8"):
            digits = "62" + digits
    if not 10 <= len(digits) <= 15 or digits.startswith("0"):
        raise ValueError("WhatsApp number must have 10–15 digits including the country code")
    return digits


def wants_whatsapp(preferences: Optional[dict], number: Optional[str], event_key: Optional[str]) -> bool:
    """Pure: does this user want this event on WhatsApp?"""
    if not number or event_key not in WA_EVENT_PREFS:
        return False
    prefs = preferences or {}
    if not prefs.get(WA_MASTER_PREF, False):
        return False
    return bool(prefs.get(WA_EVENT_PREFS[event_key], True))


def backoff_delay(attempts: int) -> timedelta:
    """Pure: wait before the next try, after `attempts` failed tries (≥ 1)."""
    idx = min(max(attempts, 1), len(BACKOFF_SECONDS)) - 1
    return timedelta(seconds=BACKOFF_SECONDS[idx])


def format_body(title: str, message: str) -> str:
    lines = [f"*{title}*", message]
    if settings.APP_URL:
        lines += ["", settings.APP_URL.rstrip("/")]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Queueing (inside the caller's transaction)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue_whatsapp(
    db: Session,
    user,
    event_key: str,
    body: str,
    entity_type: Optional[str] = None,
    entity_id=None,
    *,
    respect_preferences: bool = True,
) -> Optional[WhatsAppMessage]:
    """Add an outbox row for `user` if they want it; sent only if the session commits.

    Returns the row (pending or skipped), or None when nothing was queued.
    """
    if settings.whatsapp_backend == "disabled" or not user.whatsapp_number:
        return None
    if respect_preferences and not wants_whatsapp(user.preferences, user.whatsapp_number, event_key):
        return None

    now = _now()
    # Tie the row to a live transaction (same reason as email_service).
    if db.get_transaction() is None:
        db.begin()

    if respect_preferences:
        dup = db.query(WhatsAppMessage.id).filter(
            WhatsAppMessage.user_id == user.id,
            WhatsAppMessage.event == event_key,
            WhatsAppMessage.created_at >= now - timedelta(minutes=settings.WHATSAPP_DEDUP_MINUTES),
            (WhatsAppMessage.entity_id == entity_id) if entity_id is not None else (WhatsAppMessage.body == body),
        ).first()
        if dup is not None:
            return None

    sent_last_hour = db.query(WhatsAppMessage).filter(
        WhatsAppMessage.user_id == user.id,
        WhatsAppMessage.status != "skipped",
        WhatsAppMessage.created_at >= now - timedelta(hours=1),
    ).count()
    over_limit = sent_last_hour >= settings.WHATSAPP_MAX_PER_HOUR

    row = WhatsAppMessage(
        id=uuid.uuid4(),
        user_id=user.id,
        phone=user.whatsapp_number,
        event=event_key,
        entity_type=entity_type,
        entity_id=entity_id,
        body=body,
        status="skipped" if over_limit else "pending",
        last_error="rate_limited" if over_limit else None,
        next_attempt_at=now,
        created_at=now,
    )
    db.add(row)
    db.flush()   # later dedup/limit checks in this transaction must see it
    if over_limit:
        logger.warning("whatsapp to user %s skipped: hourly limit reached", user.id)
    else:
        db.info.setdefault(_PENDING_KEY, []).append(row.id)
    return row


@event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    ids = session.info.pop(_PENDING_KEY, None)
    if not ids or settings.whatsapp_backend not in ("wuzapi", "console"):
        return   # memory: tests drain with process_due(); disabled: nothing queued
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="whatsapp")
    _executor.submit(_deliver_in_new_session, list(ids))


@event.listens_for(Session, "after_transaction_end")
def _drop_unsent(session: Session, transaction) -> None:
    if transaction.parent is None:
        session.info.pop(_PENDING_KEY, None)


def _deliver_in_new_session(ids) -> None:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        process_due(db, ids=ids)
    except Exception:                              # noqa: BLE001 — never crash the worker
        logger.exception("whatsapp delivery thread failed")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _send(phone: str, body: str) -> None:
    """Hand one message to the configured backend. Raises on failure."""
    backend = settings.whatsapp_backend
    if backend == "memory":
        OUTBOX.append(SentWhatsApp(phone=phone, body=body))
    elif backend == "console":
        logger.info("whatsapp (console backend) to=%s\n%s", phone, body)
    elif backend == "wuzapi":
        try:
            res = httpx.post(
                f"{settings.WUZAPI_URL.rstrip('/')}/chat/send/text",
                headers={"Token": settings.WUZAPI_TOKEN},
                json={"Phone": phone, "Body": body},
                timeout=settings.WUZAPI_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise WhatsAppSendError(f"WuzAPI unreachable: {exc}") from exc
        if res.status_code >= 300:
            raise WhatsAppSendError(f"WuzAPI HTTP {res.status_code}: {res.text[:300]}")
        try:
            ok = res.json().get("success", True)
        except ValueError:
            ok = True
        if not ok:
            raise WhatsAppSendError(f"WuzAPI refused: {res.text[:300]}")
    else:
        raise WhatsAppSendError(f"WhatsApp backend {backend!r} cannot send")


def process_due(db: Session, ids=None, limit: int = 100, now: Optional[datetime] = None) -> dict:
    """Send pending messages whose next_attempt_at has come (optionally only `ids`).

    Rows are locked with SKIP LOCKED, so the after-commit thread and the retry
    runner never send the same row twice. Commits once per row.
    Returns counts: {"sent", "retry", "failed"}.
    """
    now = now or _now()
    counts = {"sent": 0, "retry": 0, "failed": 0}
    query = db.query(WhatsAppMessage).filter(
        WhatsAppMessage.status == "pending",
        WhatsAppMessage.next_attempt_at <= now,
    )
    if ids is not None:
        query = query.filter(WhatsAppMessage.id.in_(list(ids)))
    candidate_ids = [r.id for r in query.order_by(WhatsAppMessage.created_at).limit(limit).all()]
    db.commit()

    for row_id in candidate_ids:
        row = (
            db.query(WhatsAppMessage)
            .filter(WhatsAppMessage.id == row_id, WhatsAppMessage.status == "pending")
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            db.commit()
            continue   # taken by another worker, or already done
        row.attempts += 1
        try:
            _send(row.phone, row.body)
        except Exception as exc:                   # noqa: BLE001 — record and move on
            row.last_error = str(exc)[:500]
            if row.attempts >= settings.WHATSAPP_MAX_ATTEMPTS:
                row.status = "failed"
                counts["failed"] += 1
                logger.error("whatsapp %s to %s failed for good: %s", row.id, row.phone, exc)
            else:
                row.next_attempt_at = now + backoff_delay(row.attempts)
                counts["retry"] += 1
                logger.warning("whatsapp %s to %s failed (attempt %s): %s", row.id, row.phone, row.attempts, exc)
        else:
            row.status = "sent"
            row.sent_at = _now()
            row.last_error = None
            counts["sent"] += 1
        db.commit()
    return counts


def send_test(db: Session, user) -> Optional[WhatsAppMessage]:
    """Queue a test message to the user's own number (Settings → Send test).
    Ignores preferences and dedup; still subject to the hourly limit."""
    return queue_whatsapp(
        db, user, "test",
        format_body("Tes RestaurantOps", "Notifikasi WhatsApp Anda sudah tersambung."),
        respect_preferences=False,
    )
