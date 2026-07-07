"""Approval delegation + auto-escalation (Tier 3)

Revision ID: 021
Revises: 020
Create Date: 2026-07-06

Adds to approval_requests:
  - current_step_since (timestamptz) — when the active step became active; reset
    on each advance. Used to detect stale/stuck steps.
  - escalated (bool) — flagged True by the escalation scan when a step has been
    pending longer than the threshold.

Delegation reuses the existing approval_steps.approver_user_id / approver_role
columns (no schema change needed).
"""

from alembic import op
from sqlalchemy import text

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS current_step_since TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ADD COLUMN IF NOT EXISTS escalated          BOOLEAN     NOT NULL DEFAULT false
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE approval_requests
            DROP COLUMN IF EXISTS escalated,
            DROP COLUMN IF EXISTS current_step_since
    """))
