"""Idempotency keys for offline-safe retries (Tier 5.3-A)

Revision ID: 027
Revises: 026
Create Date: 2026-07-06

A technician on a flaky field connection retries a mutation whose first attempt
may already have succeeded. Endpoints that aren't naturally idempotent
(consuming a part decrements stock; uploading a photo; adding a checklist item)
would double-apply.

The client sends an `Idempotency-Key` (a UUID minted once per user action). The
first request stores its response under that key; a retry returns the stored
response instead of running the mutation again.

Scoped by user so one user's key can never replay another user's response.
"""

from alembic import op
from sqlalchemy import text

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key           VARCHAR(80)  NOT NULL,
            user_id       UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            method        VARCHAR(10)  NOT NULL,
            path          VARCHAR(300) NOT NULL,
            status_code   INTEGER      NOT NULL,
            response_body TEXT         NOT NULL,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (key, user_id)
        )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS idempotency_keys"))
