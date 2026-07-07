"""Preventive Maintenance scheduling — pm_schedules + WO idempotency link

Revision ID: 015
Revises: 014
Create Date: 2026-07-06

Adds:
  - enum `pm_interval_type` (days | weeks | months)
  - table `pm_schedules` (recurring preventive maintenance definitions)
  - columns on `work_orders`: `pm_schedule_id`, `pm_period_key`
  - UNIQUE(pm_schedule_id, pm_period_key) — guarantees the generator can never
    create two preventive WOs for the same schedule + due period (idempotency,
    even under concurrent runs).

All DDL is additive & non-breaking. `assignee_role` reuses the existing
`approver_role` enum (staff | manager | admin).
"""

from alembic import op
from sqlalchemy import text

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. pm_interval_type enum
    # ------------------------------------------------------------------
    conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE pm_interval_type AS ENUM ('days', 'weeks', 'months');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))

    # ------------------------------------------------------------------
    # 2. pm_schedules
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pm_schedules (
            id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_id           UUID          NOT NULL
                REFERENCES assets(id) ON DELETE CASCADE,
            name               VARCHAR(300)  NOT NULL,
            interval_type      pm_interval_type NOT NULL DEFAULT 'days',
            interval_value     INTEGER       NOT NULL DEFAULT 30,
            checklist          JSONB         NOT NULL DEFAULT '[]'::jsonb,
            assignee_role      approver_role,
            assignee_user_id   UUID          REFERENCES users(id) ON DELETE SET NULL,
            assignee_name      VARCHAR(200),
            lead_time_days     INTEGER       NOT NULL DEFAULT 0,
            next_due_date      DATE          NOT NULL,
            last_generated_at  TIMESTAMPTZ,
            is_active          BOOLEAN       NOT NULL DEFAULT true,
            outlet             VARCHAR(200)  NOT NULL,
            created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            deleted_at         TIMESTAMPTZ
        )
    """))
    # Composite index used by the scheduler query (active + due).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pm_schedules_active_due "
        "ON pm_schedules(is_active, next_due_date)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pm_schedules_asset "
        "ON pm_schedules(asset_id)"
    ))

    # ------------------------------------------------------------------
    # 3. work_orders link + idempotency key
    # ------------------------------------------------------------------
    conn.execute(text("""
        ALTER TABLE work_orders
            ADD COLUMN IF NOT EXISTS pm_schedule_id UUID
                REFERENCES pm_schedules(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS pm_period_key  VARCHAR(20)
    """))
    # One preventive WO per schedule per due period. NULLs are ignored by the
    # unique index, so ordinary (non-PM) work orders are unaffected.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_pm_schedule_period "
        "ON work_orders(pm_schedule_id, pm_period_key) "
        "WHERE pm_schedule_id IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("DROP INDEX IF EXISTS uq_wo_pm_schedule_period"))
    conn.execute(text("""
        ALTER TABLE work_orders
            DROP COLUMN IF EXISTS pm_period_key,
            DROP COLUMN IF EXISTS pm_schedule_id
    """))
    conn.execute(text("DROP TABLE IF EXISTS pm_schedules"))
    conn.execute(text("DROP TYPE IF EXISTS pm_interval_type"))
