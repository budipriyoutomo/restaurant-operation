"""Soft-delete: add deleted_at to outlets, categories, pics

Revision ID: 003
Revises: 002
Create Date: 2026-06-20
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE outlets    ADD COLUMN deleted_at TIMESTAMPTZ DEFAULT NULL")
    op.execute("ALTER TABLE categories ADD COLUMN deleted_at TIMESTAMPTZ DEFAULT NULL")
    op.execute("ALTER TABLE pics       ADD COLUMN deleted_at TIMESTAMPTZ DEFAULT NULL")

    # Indexes to make soft-delete queries fast
    op.execute("CREATE INDEX idx_outlets_deleted_at    ON outlets(deleted_at)    WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX idx_categories_deleted_at ON categories(deleted_at) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX idx_pics_deleted_at       ON pics(deleted_at)       WHERE deleted_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pics_deleted_at")
    op.execute("DROP INDEX IF EXISTS idx_categories_deleted_at")
    op.execute("DROP INDEX IF EXISTS idx_outlets_deleted_at")
    op.execute("ALTER TABLE pics       DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE outlets    DROP COLUMN IF EXISTS deleted_at")
