"""Spare parts & inventory (Tier 3)

Revision ID: 018
Revises: 017
Create Date: 2026-07-06

Adds:
  - table `parts` (inventory master: sku, stock, reorder level, unit cost IDR)
  - table `work_order_parts` (parts consumed by a WO — decrements stock and
    feeds work_orders.parts_cost)

All money is INTEGER (IDR), consistent with migration 014.
"""

from alembic import op
from sqlalchemy import text

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS parts (
            id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            sku            VARCHAR(60)   NOT NULL UNIQUE,
            name           VARCHAR(300)  NOT NULL,
            category       VARCHAR(100)  NOT NULL DEFAULT 'General',
            unit           VARCHAR(20)   NOT NULL DEFAULT 'pcs',
            unit_cost      INTEGER       NOT NULL DEFAULT 0,   -- IDR
            stock_qty      INTEGER       NOT NULL DEFAULT 0,
            reorder_level  INTEGER       NOT NULL DEFAULT 0,
            outlet         VARCHAR(200),                        -- null = shared
            is_active      BOOLEAN       NOT NULL DEFAULT true,
            created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            deleted_at     TIMESTAMPTZ
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS work_order_parts (
            id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            work_order_id  UUID          NOT NULL
                REFERENCES work_orders(id) ON DELETE CASCADE,
            part_id        UUID          REFERENCES parts(id) ON DELETE SET NULL,
            part_name      VARCHAR(300)  NOT NULL,            -- denormalized snapshot
            quantity       INTEGER       NOT NULL,
            unit_cost      INTEGER       NOT NULL DEFAULT 0,  -- IDR snapshot at consumption
            line_cost      INTEGER       NOT NULL DEFAULT 0,  -- quantity * unit_cost
            created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_wo_parts_wo ON work_order_parts(work_order_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_parts_sku ON parts(sku)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS work_order_parts"))
    conn.execute(text("DROP TABLE IF EXISTS parts"))
