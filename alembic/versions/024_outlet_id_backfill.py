"""Multi-outlet — outlet_id FK + backfill (Tier 4.1)

Revision ID: 024
Revises: 023
Create Date: 2026-07-06

Adds a real `outlet_id` FK next to the existing denormalised `outlet` string on
every outlet-bearing table, backfills it by matching on outlet name, and then
adds the FK constraint.

Two deliberate decisions, both driven by auditing the actual data first:

1. `outlet_id` is NULLABLE everywhere — including tables where the `outlet`
   string is NOT NULL. The data contains the sentinel value 'All Outlets'
   (issues, approval_requests), which means "not scoped to one outlet"; several
   tables (vendors, parts, approval_policies, campaigns) already use NULL for
   the same "shared" meaning. Forcing NOT NULL would have no correct value to
   put there.

   => Scoping rule for Tier 4.2: a row is visible when
      `outlet_id IS NULL` (shared/all outlets) OR `outlet_id` ∈ the user's outlets.

2. The `outlet` string column is KEPT. The whole codebase reads it for display
   and it is denormalised on purpose; dropping it would be a large breaking
   change for no benefit at this step.

Rows whose outlet name has no match in `outlets` are left NULL and REPORTED in
the migration output rather than silently dropped.
"""

from alembic import op
from sqlalchemy import text

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

# Tables carrying a denormalised `outlet` name.
TABLES = [
    "issues", "tasks", "assets", "work_orders", "approval_requests",
    "approval_policies", "parts", "pm_schedules", "vendors",
    "training_programs", "campaigns",
]

# Known sentinel meaning "not tied to a single outlet" — maps to NULL, not an error.
ALL_OUTLETS_SENTINEL = "All Outlets"


def upgrade() -> None:
    conn = op.get_bind()

    for t in TABLES:
        conn.execute(text(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS outlet_id UUID"))

        # Backfill by exact name match against the outlet master.
        conn.execute(text(f"""
            UPDATE {t} AS x
               SET outlet_id = o.id
              FROM outlets o
             WHERE o.name = x.outlet
               AND x.outlet_id IS NULL
        """))

        # Report anything still unmatched so a dirty dataset is visible, not silent.
        unmatched = conn.execute(text(f"""
            SELECT x.outlet, count(*) AS n
              FROM {t} x
             WHERE x.outlet IS NOT NULL
               AND x.outlet_id IS NULL
             GROUP BY x.outlet
             ORDER BY n DESC
        """)).fetchall()
        for outlet_name, n in unmatched:
            if outlet_name == ALL_OUTLETS_SENTINEL:
                print(f"  [024] {t}: {n} row(s) '{outlet_name}' -> outlet_id NULL (expected sentinel)")
            else:
                print(f"  [024] {t}: WARNING {n} row(s) with unknown outlet {outlet_name!r} -> outlet_id NULL")

        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS idx_{t}_outlet_id ON {t}(outlet_id)"
        ))
        # FK added after backfill. ON DELETE RESTRICT: an outlet still holding
        # records must not be deletable out from under them.
        conn.execute(text(f"""
            DO $$ BEGIN
                ALTER TABLE {t}
                    ADD CONSTRAINT fk_{t}_outlet
                    FOREIGN KEY (outlet_id) REFERENCES outlets(id) ON DELETE RESTRICT;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))


def downgrade() -> None:
    conn = op.get_bind()
    for t in TABLES:
        conn.execute(text(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS fk_{t}_outlet"))
        conn.execute(text(f"DROP INDEX IF EXISTS idx_{t}_outlet_id"))
        conn.execute(text(f"ALTER TABLE {t} DROP COLUMN IF EXISTS outlet_id"))
