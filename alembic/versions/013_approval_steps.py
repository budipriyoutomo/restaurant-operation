"""Approval multi-step — approval_steps table + current_step_order

Revision ID: 013
Revises: 012
Create Date: 2026-06-28

Gotcha #7: ALTER TYPE ... ADD VALUE ('maintenance' ke approval_type) harus
dijalankan di luar transaction block.  Pola sama dengan migration 012.

Keputusan arsitektur:
- UNIQUE(approval_requests.issue_id) DIPERTAHANKAN.
- Multi-step hidup di tabel anak approval_steps.
- approver_role disimpan sebagai enum baru 'approver_role' agar nilai terkunci
  di DB (staff | manager | admin) — konsisten dengan role VARCHAR users.
"""

from alembic import op
from sqlalchemy import text

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Add 'maintenance' to approval_type enum  (outside transaction)
    # ------------------------------------------------------------------
    conn.execute(text("COMMIT"))
    conn.execute(text(
        "ALTER TYPE approval_type ADD VALUE IF NOT EXISTS 'maintenance'"
    ))
    # New implicit transaction begins with the next statement.

    # ------------------------------------------------------------------
    # 2. New enum types for approval steps
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TYPE approval_step_status AS ENUM (
            'pending', 'approved', 'rejected', 'skipped'
        )
    """))
    conn.execute(text("""
        CREATE TYPE approver_role AS ENUM (
            'staff', 'manager', 'admin'
        )
    """))

    # ------------------------------------------------------------------
    # 3. approval_steps — child rows of approval_requests
    #    UNIQUE(approval_request_id, step_order) enforces one row per step.
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS approval_steps (
            id                  UUID                  PRIMARY KEY DEFAULT gen_random_uuid(),
            approval_request_id UUID                  NOT NULL
                REFERENCES approval_requests(id) ON DELETE CASCADE,
            step_order          INTEGER               NOT NULL,
            approver_role       approver_role         NOT NULL,
            approver_user_id    UUID
                REFERENCES users(id) ON DELETE SET NULL,
            status              approval_step_status  NOT NULL DEFAULT 'pending',
            decided_by          UUID
                REFERENCES users(id) ON DELETE SET NULL,
            decided_at          TIMESTAMPTZ,
            comment             TEXT,
            created_at          TIMESTAMPTZ           NOT NULL DEFAULT NOW(),
            UNIQUE (approval_request_id, step_order)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_approval_steps_request_order "
        "ON approval_steps(approval_request_id, step_order)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_approval_steps_role_status "
        "ON approval_steps(approver_role, status)"
    ))

    # ------------------------------------------------------------------
    # 4. current_step_order on approval_requests
    #    Tracks which step is active; starts at 1 (first step).
    # ------------------------------------------------------------------
    conn.execute(text("""
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS current_step_order INTEGER NOT NULL DEFAULT 1
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(
        "ALTER TABLE approval_requests DROP COLUMN IF EXISTS current_step_order"
    ))
    conn.execute(text("DROP TABLE IF EXISTS approval_steps"))
    conn.execute(text("DROP TYPE IF EXISTS approver_role"))
    conn.execute(text("DROP TYPE IF EXISTS approval_step_status"))

    # Remove 'maintenance' from approval_type via recreate
    conn.execute(text("COMMIT"))
    conn.execute(text(
        "UPDATE approval_requests SET type = 'procurement' WHERE type = 'maintenance'"
    ))
    conn.execute(text(
        "ALTER TYPE approval_type RENAME TO approval_type_old"
    ))
    conn.execute(text("""
        CREATE TYPE approval_type AS ENUM (
            'procurement', 'marketing', 'training', 'asset-purchase'
        )
    """))
    conn.execute(text("""
        ALTER TABLE approval_requests
            ALTER COLUMN type TYPE approval_type
                USING type::text::approval_type
    """))
    conn.execute(text("DROP TYPE approval_type_old"))
