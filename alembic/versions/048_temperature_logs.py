"""Food safety temperature log (Todo-Ops-Checklist Fase 2)

Revision ID: 048
Revises: 047
Create Date: 2026-10-15

  assets                         + temp_monitored, temp_min, temp_max (°C, inclusive; min < max)
  ops_checklist_template_items   + asset_id (a temperature item measuring one monitored asset)
  ops_checklist_run_items        + asset_id, asset_name (snapshot)
  temperature_logs               one row per reading; limits snapshotted, so changing an
                                 asset's limits never rewrites old readings. Not
                                 meter_readings: those are cumulative integers for meter PM.
"""

from alembic import op
from sqlalchemy import text

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE assets
            ADD COLUMN temp_monitored BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN temp_min NUMERIC(6,1),
            ADD COLUMN temp_max NUMERIC(6,1),
            ADD CONSTRAINT assets_temp_range_check CHECK (temp_min IS NULL OR temp_max IS NULL OR temp_min < temp_max)
    """))
    conn.execute(text("ALTER TABLE ops_checklist_template_items ADD COLUMN asset_id UUID REFERENCES assets(id) ON DELETE SET NULL"))
    conn.execute(text("""
        ALTER TABLE ops_checklist_run_items
            ADD COLUMN asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
            ADD COLUMN asset_name VARCHAR(300)
    """))
    conn.execute(text("""
        CREATE TABLE temperature_logs (
            id                UUID PRIMARY KEY,
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            asset_id          UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            outlet            VARCHAR(200) NOT NULL,
            outlet_id         UUID REFERENCES outlets(id) ON DELETE RESTRICT,
            value             NUMERIC(6,1) NOT NULL,
            min_value         NUMERIC(6,1),
            max_value         NUMERIC(6,1),
            in_range          BOOLEAN NOT NULL,
            corrective_action TEXT,
            source            VARCHAR(20) NOT NULL,
            run_item_id       UUID UNIQUE REFERENCES ops_checklist_run_items(id) ON DELETE SET NULL,
            issue_id          UUID REFERENCES issues(id) ON DELETE SET NULL,
            recorded_at       TIMESTAMPTZ NOT NULL,
            received_at       TIMESTAMPTZ NOT NULL,
            recorded_by       UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT temperature_logs_source_check CHECK (source IN ('checklist', 'manual'))
        )
    """))
    conn.execute(text("CREATE INDEX ix_temperature_logs_company_id ON temperature_logs (company_id)"))
    conn.execute(text("CREATE INDEX ix_temperature_logs_asset_id ON temperature_logs (asset_id)"))
    conn.execute(text("CREATE INDEX ix_temperature_logs_issue_id ON temperature_logs (issue_id)"))
    conn.execute(text("CREATE INDEX ix_temperature_logs_asset_recorded ON temperature_logs (asset_id, recorded_at)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE temperature_logs"))
    conn.execute(text("ALTER TABLE ops_checklist_run_items DROP COLUMN asset_name, DROP COLUMN asset_id"))
    conn.execute(text("ALTER TABLE ops_checklist_template_items DROP COLUMN asset_id"))
    conn.execute(text("""
        ALTER TABLE assets DROP CONSTRAINT assets_temp_range_check,
            DROP COLUMN temp_max, DROP COLUMN temp_min, DROP COLUMN temp_monitored
    """))
