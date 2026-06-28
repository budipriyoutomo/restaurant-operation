"""Notifications table

Revision ID: 007
Revises: 006
Create Date: 2026-06-28
"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notifications (
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title       TEXT         NOT NULL,
            message     TEXT         NOT NULL,
            type        VARCHAR(20)  NOT NULL DEFAULT 'info',  -- info | warning | critical | success
            entity_type VARCHAR(50),   -- issues | tasks | approvals | etc.
            entity_id   UUID,
            read_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_notifications_user_id ON notifications(user_id)")
    op.execute("CREATE INDEX idx_notifications_read_at ON notifications(user_id, read_at) WHERE read_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notifications_read_at")
    op.execute("DROP INDEX IF EXISTS idx_notifications_user_id")
    op.execute("DROP TABLE IF EXISTS notifications")
