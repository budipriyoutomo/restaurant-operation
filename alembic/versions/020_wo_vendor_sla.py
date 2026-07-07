"""Vendor / external maintenance (Tier 3)

Revision ID: 020
Revises: 019
Create Date: 2026-07-06

Adds vendor assignment + SLA tracking to work_orders:
  - vendor_id (FK vendors), vendor_name (denormalized)
  - sla_due (date), sla_met (bool, set on completion)
"""

from alembic import op
from sqlalchemy import text

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE work_orders
            ADD COLUMN IF NOT EXISTS vendor_id   UUID
                REFERENCES vendors(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS vendor_name VARCHAR(200),
            ADD COLUMN IF NOT EXISTS sla_due     DATE,
            ADD COLUMN IF NOT EXISTS sla_met     BOOLEAN
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_work_orders_vendor ON work_orders(vendor_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS idx_work_orders_vendor"))
    conn.execute(text("""
        ALTER TABLE work_orders
            DROP COLUMN IF EXISTS sla_met,
            DROP COLUMN IF EXISTS sla_due,
            DROP COLUMN IF EXISTS vendor_name,
            DROP COLUMN IF EXISTS vendor_id
    """))
