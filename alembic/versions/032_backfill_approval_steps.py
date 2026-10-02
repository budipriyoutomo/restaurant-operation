"""Backfill approval steps for step-less approval requests

Revision ID: 032
Revises: 031
Create Date: 2026-10-02

Issues created with "Send to Approval Center" inserted a bare ApprovalRequest
without any approval_steps, so deciding them failed ("No step with order 1").
The creation path now uses create_approval_with_steps; this gives the existing
step-less rows the default chain (manager -> admin) so they can be decided.
Steps of already-decided requests mirror the request's final status.
"""

from alembic import op
from sqlalchemy import text

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        INSERT INTO approval_steps (id, approval_request_id, step_order, approver_role, status)
        SELECT gen_random_uuid(), a.id, d.step_order, d.approver_role::approver_role,
               (CASE
                    WHEN a.status::text = 'pending' THEN 'pending'
                    WHEN a.status::text = 'rejected' AND d.step_order = 2 THEN 'skipped'
                    ELSE a.status::text
                END)::approval_step_status
        FROM approval_requests a
        CROSS JOIN (VALUES (1, 'manager'), (2, 'admin')) AS d(step_order, approver_role)
        WHERE NOT EXISTS (
            SELECT 1 FROM approval_steps s WHERE s.approval_request_id = a.id
        )
    """))
    conn.execute(text("""
        UPDATE approval_requests SET current_step_order = 1
        WHERE status = 'pending' AND current_step_order <> 1
          AND NOT EXISTS (
              SELECT 1 FROM approval_steps s
              WHERE s.approval_request_id = approval_requests.id
                AND s.step_order = approval_requests.current_step_order
          )
    """))


def downgrade() -> None:
    # Backfilled rows are indistinguishable from real ones; nothing to undo.
    pass
