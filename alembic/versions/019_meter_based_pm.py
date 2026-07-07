"""Meter-based PM (Tier 3) — meter_readings + usage-triggered schedules

Revision ID: 019
Revises: 018
Create Date: 2026-07-06

Adds:
  - enum `pm_trigger_type` (calendar | meter)
  - pm_schedules: trigger_type, meter_interval, last_meter_value; next_due_date
    becomes nullable (meter schedules don't use it)
  - table `meter_readings` (asset usage log, e.g. running hours)
"""

from alembic import op
from sqlalchemy import text

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE pm_trigger_type AS ENUM ('calendar', 'meter');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))

    conn.execute(text("""
        ALTER TABLE pm_schedules
            ADD COLUMN IF NOT EXISTS trigger_type     pm_trigger_type NOT NULL DEFAULT 'calendar',
            ADD COLUMN IF NOT EXISTS meter_interval   INTEGER,
            ADD COLUMN IF NOT EXISTS last_meter_value INTEGER
    """))
    # Meter schedules don't set next_due_date.
    conn.execute(text("ALTER TABLE pm_schedules ALTER COLUMN next_due_date DROP NOT NULL"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS meter_readings (
            id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_id     UUID          NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            value        INTEGER       NOT NULL,        -- cumulative meter (e.g. running hours)
            note         VARCHAR(300),
            recorded_by  UUID          REFERENCES users(id) ON DELETE SET NULL,
            recorded_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_meter_readings_asset ON meter_readings(asset_id, recorded_at DESC)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS meter_readings"))
    conn.execute(text("""
        ALTER TABLE pm_schedules
            DROP COLUMN IF EXISTS last_meter_value,
            DROP COLUMN IF EXISTS meter_interval,
            DROP COLUMN IF EXISTS trigger_type
    """))
    conn.execute(text("ALTER TABLE pm_schedules ALTER COLUMN next_due_date SET NOT NULL"))
    conn.execute(text("DROP TYPE IF EXISTS pm_trigger_type"))
