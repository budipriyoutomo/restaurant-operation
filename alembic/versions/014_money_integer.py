"""Money columns → INTEGER + currency VARCHAR(3)

Revision ID: 014
Revises: 013
Create Date: 2026-06-29

Changes:
- approval_requests.amount  : VARCHAR(100) → INTEGER (non-numeric values → NULL)
- approval_requests.currency: new VARCHAR(3) NOT NULL DEFAULT 'IDR'
- work_orders.estimated_cost: NUMERIC(14,2) → INTEGER (ROUND)
- work_orders.labor_cost    : NUMERIC(14,2) → INTEGER (ROUND)
- work_orders.parts_cost    : NUMERIC(14,2) → INTEGER (ROUND)
- work_orders.currency      : new VARCHAR(3) NOT NULL DEFAULT 'IDR'
"""

from alembic import op
from sqlalchemy import text

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # approval_requests.amount: VARCHAR → INTEGER
    # Non-numeric strings (e.g. "RM 12,000") become NULL.
    # ------------------------------------------------------------------
    conn.execute(text("""
        ALTER TABLE approval_requests
            ALTER COLUMN amount TYPE INTEGER
            USING CASE
                WHEN amount ~ '^[0-9]+$' THEN amount::INTEGER
                ELSE NULL
            END
    """))
    conn.execute(text("""
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'IDR'
    """))

    # ------------------------------------------------------------------
    # work_orders cost columns: NUMERIC(14,2) → INTEGER
    # Rounds fractional values — existing data is dev seed, no loss.
    # ------------------------------------------------------------------
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN estimated_cost TYPE INTEGER
            USING ROUND(estimated_cost)::INTEGER
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN labor_cost TYPE INTEGER
            USING ROUND(labor_cost)::INTEGER
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN parts_cost TYPE INTEGER
            USING ROUND(parts_cost)::INTEGER
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'IDR'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE work_orders DROP COLUMN IF EXISTS currency"))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN parts_cost TYPE NUMERIC(14,2) USING parts_cost::NUMERIC
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN labor_cost TYPE NUMERIC(14,2) USING labor_cost::NUMERIC
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN estimated_cost TYPE NUMERIC(14,2) USING estimated_cost::NUMERIC
    """))

    conn.execute(text("ALTER TABLE approval_requests DROP COLUMN IF EXISTS currency"))
    conn.execute(text("""
        ALTER TABLE approval_requests
            ALTER COLUMN amount TYPE VARCHAR(100) USING amount::VARCHAR
    """))
