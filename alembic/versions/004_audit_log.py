"""Audit log table

Records every create/update/delete/status_change event across the system.

Revision ID: 004
Revises: 003
Create Date: 2026-06-20
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_logs (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            table_name   VARCHAR(50)  NOT NULL,
            record_id    VARCHAR(50)  NOT NULL,
            action       VARCHAR(30)  NOT NULL,
            old_value    JSONB        DEFAULT NULL,
            new_value    JSONB        DEFAULT NULL,
            performed_by VARCHAR(200) NOT NULL DEFAULT 'system',
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_audit_logs_table_record ON audit_logs(table_name, record_id)")
    op.execute("CREATE INDEX idx_audit_logs_created_at   ON audit_logs(created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_logs_created_at")
    op.execute("DROP INDEX IF EXISTS idx_audit_logs_table_record")
    op.execute("DROP TABLE IF EXISTS audit_logs")
