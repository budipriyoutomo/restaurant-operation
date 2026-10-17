"""IoT temperature sensors (Todo-Ops-Checklist Fase 4)

Revision ID: 052
Revises: 051
Create Date: 2026-10-16

temperature_sensors   one device per monitored asset; authenticates with its own
                      key (only the SHA-256 is stored, shown once at creation)
temperature_logs      + sensor_id; source gains 'sensor'; a device retrying the
                      same reading is ignored (UNIQUE sensor_id, recorded_at)
"""

from alembic import op
from sqlalchemy import text

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE temperature_sensors (
            id            UUID PRIMARY KEY,
            company_id    UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            asset_id      UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            name          VARCHAR(100) NOT NULL,
            key_prefix    VARCHAR(12) NOT NULL,
            key_hash      VARCHAR(64) NOT NULL UNIQUE,
            is_active     BOOLEAN NOT NULL DEFAULT true,
            last_seen_at  TIMESTAMPTZ,
            last_value    NUMERIC(6,1),
            created_by    VARCHAR(200),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at    TIMESTAMPTZ
        )
    """))
    conn.execute(text("CREATE INDEX ix_temperature_sensors_company_id ON temperature_sensors (company_id)"))
    conn.execute(text("CREATE INDEX ix_temperature_sensors_asset_id ON temperature_sensors (asset_id)"))
    conn.execute(text("""
        ALTER TABLE temperature_logs
            ADD COLUMN sensor_id UUID REFERENCES temperature_sensors(id) ON DELETE SET NULL,
            DROP CONSTRAINT temperature_logs_source_check,
            ADD CONSTRAINT temperature_logs_source_check CHECK (source IN ('checklist', 'manual', 'sensor'))
    """))
    conn.execute(text("""
        CREATE UNIQUE INDEX uq_temperature_logs_sensor_recorded
            ON temperature_logs (sensor_id, recorded_at) WHERE sensor_id IS NOT NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DELETE FROM temperature_logs WHERE source = 'sensor'"))
    conn.execute(text("DROP INDEX uq_temperature_logs_sensor_recorded"))
    conn.execute(text("""
        ALTER TABLE temperature_logs
            DROP CONSTRAINT temperature_logs_source_check,
            ADD CONSTRAINT temperature_logs_source_check CHECK (source IN ('checklist', 'manual')),
            DROP COLUMN sensor_id
    """))
    conn.execute(text("DROP TABLE temperature_sensors"))
