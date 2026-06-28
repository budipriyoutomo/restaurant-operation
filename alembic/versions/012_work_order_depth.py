"""Work Order depth — cost, downtime, checklist, attachments

Revision ID: 012
Revises: 011
Create Date: 2026-06-28

Gotcha #7: ALTER TYPE ... ADD VALUE cannot run inside a transaction block in
PostgreSQL (before PG16 the restriction still applies for enum values used in
live DDL).  We break out of the Alembic-managed transaction with an explicit
COMMIT, run the ADD VALUE, then let the rest of the DDL proceed in a fresh
implicit transaction.
"""

from alembic import op
from sqlalchemy import text

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Add 'on-hold' to work_order_status enum
    #    Must run outside an open transaction (Postgres constraint).
    # ------------------------------------------------------------------
    conn.execute(text("COMMIT"))
    conn.execute(text(
        "ALTER TYPE work_order_status ADD VALUE IF NOT EXISTS 'on-hold'"
    ))
    # The next statement implicitly starts a new transaction.

    # ------------------------------------------------------------------
    # 2. New columns on work_orders
    # ------------------------------------------------------------------
    conn.execute(text("""
        ALTER TABLE work_orders
            ADD COLUMN IF NOT EXISTS downtime_start    TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS downtime_end      TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS labor_hours       NUMERIC(8, 2),
            ADD COLUMN IF NOT EXISTS labor_cost        NUMERIC(14, 2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS parts_cost        NUMERIC(14, 2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS estimated_cost    NUMERIC(14, 2),
            ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS approval_id       UUID
                REFERENCES approval_requests(id) ON DELETE SET NULL
    """))

    # ------------------------------------------------------------------
    # 3. work_order_checklist_items
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS work_order_checklist_items (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            work_order_id UUID        NOT NULL
                REFERENCES work_orders(id) ON DELETE CASCADE,
            title         VARCHAR(500) NOT NULL,
            is_done       BOOLEAN     NOT NULL DEFAULT false,
            done_by       UUID        REFERENCES users(id) ON DELETE SET NULL,
            done_at       TIMESTAMPTZ,
            order_index   INTEGER     NOT NULL DEFAULT 0
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_wo_checklist_wo_order "
        "ON work_order_checklist_items(work_order_id, order_index)"
    ))

    # ------------------------------------------------------------------
    # 4. work_order_attachments
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS work_order_attachments (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            work_order_id UUID        NOT NULL
                REFERENCES work_orders(id) ON DELETE CASCADE,
            file_url      TEXT        NOT NULL,
            caption       VARCHAR(500),
            uploaded_by   UUID        NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_wo_attachments_wo_id "
        "ON work_order_attachments(work_order_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Drop child tables first (FK dependencies)
    conn.execute(text("DROP TABLE IF EXISTS work_order_attachments"))
    conn.execute(text("DROP TABLE IF EXISTS work_order_checklist_items"))

    # Drop new columns on work_orders
    conn.execute(text("""
        ALTER TABLE work_orders
            DROP COLUMN IF EXISTS approval_id,
            DROP COLUMN IF EXISTS requires_approval,
            DROP COLUMN IF EXISTS estimated_cost,
            DROP COLUMN IF EXISTS parts_cost,
            DROP COLUMN IF EXISTS labor_cost,
            DROP COLUMN IF EXISTS labor_hours,
            DROP COLUMN IF EXISTS downtime_end,
            DROP COLUMN IF EXISTS downtime_start
    """))

    # Removing an enum value in Postgres requires recreating the type.
    # Any 'on-hold' rows are converted to 'scheduled' before the swap.
    conn.execute(text("COMMIT"))
    conn.execute(text(
        "UPDATE work_orders SET status = 'scheduled' WHERE status = 'on-hold'"
    ))
    conn.execute(text("""
        ALTER TYPE work_order_status RENAME TO work_order_status_old
    """))
    conn.execute(text("""
        CREATE TYPE work_order_status AS ENUM (
            'scheduled', 'in-progress', 'completed', 'cancelled'
        )
    """))
    conn.execute(text("""
        ALTER TABLE work_orders
            ALTER COLUMN status TYPE work_order_status
                USING status::text::work_order_status
    """))
    conn.execute(text("DROP TYPE work_order_status_old"))
