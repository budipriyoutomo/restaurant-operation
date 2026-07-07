"""Approval Policy engine — approval_policies table

Revision ID: 016
Revises: 015
Create Date: 2026-07-06

Adds `approval_policies`: rules that resolve, per approval_type + amount range
(+ optional outlet), the chain of approval steps to generate for a new
ApprovalRequest. When no policy matches, the service falls back to the default
2-step chain (manager → admin).

Note: the `maintenance` value of the `approval_type` enum already exists
(added in migration 013), so no ALTER TYPE ... ADD VALUE is needed here.
Amounts are INTEGER (IDR), consistent with migration 014.
"""

from alembic import op
from sqlalchemy import text

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS approval_policies (
            id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            approval_type  approval_type NOT NULL,
            min_amount     INTEGER,                         -- inclusive lower bound (IDR), null = unbounded
            max_amount     INTEGER,                         -- inclusive upper bound (IDR), null = unbounded
            steps          JSONB         NOT NULL DEFAULT '[]'::jsonb,  -- list[{order:int, role:str}]
            outlet         VARCHAR(200),                    -- null = applies to all outlets
            is_active      BOOLEAN       NOT NULL DEFAULT true,
            created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            deleted_at     TIMESTAMPTZ
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_approval_policies_type_active "
        "ON approval_policies(approval_type, is_active)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS idx_approval_policies_type_active"))
    conn.execute(text("DROP TABLE IF EXISTS approval_policies"))
