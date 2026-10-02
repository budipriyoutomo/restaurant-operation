"""Per-outlet work-order approval threshold (Todo-Next §2.2)

Revision ID: 033
Revises: 032
Create Date: 2026-10-02

Corrective work orders with an estimated cost above a threshold need approval.
The threshold was a hard-coded constant (Rp 1 juta). Each outlet may now set its
own; NULL keeps the global default (settings.APPROVAL_THRESHOLD_DEFAULT), so
existing outlets behave exactly as before.
"""

from alembic import op
from sqlalchemy import text

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE outlets
            ADD COLUMN IF NOT EXISTS approval_threshold BIGINT NULL
    """))
    conn.execute(text("""
        ALTER TABLE outlets
            ADD CONSTRAINT ck_outlets_approval_threshold_nonneg
            CHECK (approval_threshold IS NULL OR approval_threshold >= 0)
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE outlets DROP CONSTRAINT IF EXISTS ck_outlets_approval_threshold_nonneg"))
    conn.execute(text("ALTER TABLE outlets DROP COLUMN IF EXISTS approval_threshold"))
