"""Maintenance budgets per outlet per period (Tier 6.3)

Revision ID: 029
Revises: 028
Create Date: 2026-07-06

A monthly maintenance budget per outlet. Spend is derived (completed work-order
cost + purchase-order value in that month), so the table only holds the plan.
"""

from alembic import op
from sqlalchemy import text

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS budgets (
            id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            outlet_id  UUID          NOT NULL REFERENCES outlets(id) ON DELETE CASCADE,
            outlet     VARCHAR(200),                       -- denormalized name
            period     VARCHAR(7)    NOT NULL,             -- 'YYYY-MM'
            amount     INTEGER       NOT NULL DEFAULT 0,   -- IDR
            created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            UNIQUE (outlet_id, period)
        )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS budgets"))
