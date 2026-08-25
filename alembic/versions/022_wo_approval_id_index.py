"""Index work_orders.approval_id

Revision ID: 022
Revises: 021
Create Date: 2026-07-06

`_sync_linked_work_order` looks the work order up by approval_id every time an
approval step is decided, but the FK had no supporting index — that lookup was a
sequential scan over work_orders.

Only this FK gets an index: the other unindexed FKs (decided_by, uploaded_by,
done_by, recorded_by, approver_user_id, assignee_user_id, part_id) are
attribution columns that are never used as query filters, so indexing them would
cost write throughput for no read benefit.
"""

from alembic import op
from sqlalchemy import text

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_work_orders_approval "
        "ON work_orders(approval_id) WHERE approval_id IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS idx_work_orders_approval"))
