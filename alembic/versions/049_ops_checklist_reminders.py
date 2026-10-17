"""Daily ops checklist reminders (Todo-Ops-Checklist Fase 3)

Revision ID: 049
Revises: 048
Create Date: 2026-10-15

ops_checklist_runs.due_soon_notified_at — the "due in 30 minutes" reminder went
out; the 15-minute job sends it once per run. Missed-run notices need no column:
a run turns missed (and is announced) once.
"""

from alembic import op
from sqlalchemy import text

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE ops_checklist_runs ADD COLUMN due_soon_notified_at TIMESTAMPTZ"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE ops_checklist_runs DROP COLUMN due_soon_notified_at"))
