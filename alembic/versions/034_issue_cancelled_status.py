"""Issue/Task status `cancelled` (Todo-Pilot §2)

Revision ID: 034
Revises: 033
Create Date: 2026-10-02

A Manager can cancel an Issue (POST /api/issues/{id}/cancel); its open Tasks are
cancelled with it. Both enums gain the value — the frontend shares one type for
them (TaskStatus = IssueStatus).

Same gotcha as 012: ALTER TYPE ... ADD VALUE must not run inside the
Alembic-managed transaction, so we COMMIT first.
"""

from alembic import op
from sqlalchemy import text

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    conn.execute(text("ALTER TYPE issue_status ADD VALUE IF NOT EXISTS 'cancelled'"))
    conn.execute(text("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'cancelled'"))


def downgrade() -> None:
    # Postgres cannot drop an enum value. Fold the rows back into `closed`;
    # the value itself stays in the type (harmless).
    conn = op.get_bind()
    conn.execute(text("UPDATE issues SET status = 'closed' WHERE status = 'cancelled'"))
    conn.execute(text("UPDATE tasks  SET status = 'closed' WHERE status = 'cancelled'"))
