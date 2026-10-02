"""Email delivery for notifications (Todo-Next §4).

Callers never send directly. notification_service queues a message on the DB
session with queue_email(); the message goes out only after that session
commits. A rollback (or closing the session without a commit) drops it. So:

  - an email never announces something that did not happen, and
  - an SMTP failure can never undo the user's action (sending happens after
    the commit, on a background thread for the smtp backend).

Backends (settings.email_backend):
  smtp      send via SMTP on a small background thread pool
  console   log the message instead of sending it (development)
  memory    append to OUTBOX synchronously (tests)
  disabled  drop the message (default when SMTP_HOST is empty)
"""

from __future__ import annotations

import logging
import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage as _MimeMessage
from typing import List

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_emails"

# Messages delivered by the "memory" backend. Tests read and clear this.
OUTBOX: List["OutgoingEmail"] = []

_executor: ThreadPoolExecutor | None = None


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


def queue_email(db: Session, to: str, subject: str, body: str) -> None:
    """Queue an email on the session. It is sent only if the session commits."""
    if settings.email_backend == "disabled" or not to:
        return
    # Tie the message to a live transaction: with none begun yet, a rollback()
    # would end nothing, the drop hook below would not run, and the next commit
    # would send mail for work that was rolled back.
    if db.get_transaction() is None:
        db.begin()
    db.info.setdefault(_PENDING_KEY, []).append(OutgoingEmail(to=to, subject=subject, body=body))


@event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if pending:
        dispatch(pending)


@event.listens_for(Session, "after_transaction_end")
def _drop_unsent(session: Session, transaction) -> None:
    # Fires after after_commit too (pending already popped there). For a
    # rollback, or a close() without commit, this drops what was queued.
    # Only the outermost transaction: a SAVEPOINT ending is not the end of
    # the unit of work.
    if transaction.parent is None:
        session.info.pop(_PENDING_KEY, None)


def dispatch(messages: List[OutgoingEmail]) -> None:
    backend = settings.email_backend
    if backend == "memory":
        OUTBOX.extend(messages)
    elif backend == "console":
        for m in messages:
            logger.info("email (console backend) to=%s subject=%s\n%s", m.to, m.subject, m.body)
    elif backend == "smtp":
        global _executor
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="email")
        for m in messages:
            _executor.submit(_send_smtp, m)
    # disabled (or unknown): drop silently — in-app notifications still exist.


def _send_smtp(message: OutgoingEmail) -> None:
    mime = _MimeMessage()
    mime["From"] = settings.SMTP_FROM
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime.set_content(message.body)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls()
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(mime)
    except Exception:                              # noqa: BLE001 — never crash the worker
        logger.exception("email to %s failed (subject=%r)", message.to, message.subject)
