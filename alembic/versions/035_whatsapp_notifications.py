"""WhatsApp notifications: user number + outbox (Todo-Pilot §4)

Revision ID: 035
Revises: 034
Create Date: 2026-10-02

- users.whatsapp_number: normalised digits with country code (6281234567890),
  NULL = no WhatsApp. Opt-in switches live in users.preferences (wa*).
- whatsapp_outbox: one row per message. Written in the same transaction as the
  business change (a rollback drops it), sent after commit, retried by
  scripts/run_whatsapp_retry. Also the source for dedup and rate limiting.
"""

from alembic import op
from sqlalchemy import text

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_number VARCHAR(20) NULL"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_outbox (
            id              UUID PRIMARY KEY,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            phone           VARCHAR(20) NOT NULL,
            event           VARCHAR(50) NOT NULL,
            entity_type     VARCHAR(50) NULL,
            entity_id       UUID NULL,
            body            TEXT NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempts        INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT NULL,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at         TIMESTAMPTZ NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_whatsapp_outbox_status
                CHECK (status IN ('pending', 'sent', 'failed', 'skipped'))
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_outbox_due ON whatsapp_outbox (status, next_attempt_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_outbox_user_created ON whatsapp_outbox (user_id, created_at)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS whatsapp_outbox"))
    conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS whatsapp_number"))
