"""Weekly and monthly ops checklist schedules (Todo-Ops-Checklist Fase 4)

Revision ID: 050
Revises: 049
Create Date: 2026-10-16

ops_checklist_schedules
  + frequency     daily (default, as before) | weekly (its one weekday in
                  days_of_week) | monthly (day_of_month; a shorter month falls on
                  its last day)
  + day_of_month  1–31, monthly only
Deep cleaning and thermometer calibration open days before they are due; the
window limits per frequency are enforced by the API (ops_checklist_rules).
"""

from alembic import op
from sqlalchemy import text

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE ops_checklist_schedules
            ADD COLUMN frequency VARCHAR(10) NOT NULL DEFAULT 'daily',
            ADD COLUMN day_of_month INTEGER,
            ADD CONSTRAINT ops_checklist_schedules_frequency_check CHECK (frequency IN ('daily', 'weekly', 'monthly')),
            ADD CONSTRAINT ops_checklist_schedules_day_of_month_check CHECK (
                (frequency = 'monthly' AND day_of_month BETWEEN 1 AND 31)
                OR (frequency <> 'monthly' AND day_of_month IS NULL))
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE ops_checklist_schedules
            DROP CONSTRAINT ops_checklist_schedules_day_of_month_check,
            DROP CONSTRAINT ops_checklist_schedules_frequency_check,
            DROP COLUMN day_of_month, DROP COLUMN frequency
    """))
