"""Assignees are users: assignee_user_id on issues, tasks, work_orders (Todo-Pilot §17)

Revision ID: 041
Revises: 040
Create Date: 2026-10-13

`assignee` stays (denormalised display name), next to a real FK. The work-order
notification used to find the technician by display name — a typo or two people
with the same name meant nobody was told.

Backfill: an existing name is linked only when exactly one active user of the
same company has it (case-insensitive, trimmed). Ambiguous or unknown names
stay unlinked; the UI flags them as "will not be notified".

Also fixed here: preventive work orders generated from a PM schedule never got
outlet_id, so they were "shared" and visible to every outlet. Backfilled from
their schedule.
"""

from alembic import op
from sqlalchemy import text

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None

TABLES = ("issues", "tasks", "work_orders")


def upgrade() -> None:
    for table in TABLES:
        op.execute(text(f"""
            ALTER TABLE {table}
              ADD COLUMN assignee_user_id UUID NULL
              REFERENCES users(id) ON DELETE SET NULL
        """))
        op.execute(text(f"CREATE INDEX ix_{table}_assignee_user_id ON {table} (assignee_user_id)"))
        op.execute(text(f"""
            UPDATE {table} AS r
               SET assignee_user_id = m.user_id
              FROM (
                    SELECT company_id, lower(btrim(name)) AS key, min(id::text)::uuid AS user_id
                      FROM users
                     WHERE is_active AND company_id IS NOT NULL
                  GROUP BY company_id, lower(btrim(name))
                    HAVING count(*) = 1
                   ) AS m
             WHERE m.company_id = r.company_id
               AND m.key = lower(btrim(r.assignee))
        """))


    op.execute(text("""
        UPDATE work_orders AS w
           SET outlet_id = s.outlet_id
          FROM pm_schedules AS s
         WHERE w.pm_schedule_id = s.id
           AND w.outlet_id IS NULL
           AND s.outlet_id IS NOT NULL
    """))


def downgrade() -> None:
    for table in TABLES:
        op.execute(text(f"DROP INDEX IF EXISTS ix_{table}_assignee_user_id"))
        op.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS assignee_user_id"))
