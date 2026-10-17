"""Manager's daily sign-off of ops logs (Todo-Ops-Checklist Fase 4)

Revision ID: 051
Revises: 050
Create Date: 2026-10-16

ops_daily_signoffs: one per outlet per business day — "I checked today's logs".
summary keeps the counts the manager saw; digest (SHA-256 over the day's runs
and temperature readings) shows later whether the data still matches.
"""

from alembic import op
from sqlalchemy import text

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE ops_daily_signoffs (
            id              UUID PRIMARY KEY,
            company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            outlet          VARCHAR(200) NOT NULL,
            outlet_id       UUID NOT NULL REFERENCES outlets(id) ON DELETE RESTRICT,
            business_date   DATE NOT NULL,
            signed_by       UUID REFERENCES users(id) ON DELETE SET NULL,
            signed_by_name  VARCHAR(200) NOT NULL,
            signed_at       TIMESTAMPTZ NOT NULL,
            note            TEXT NOT NULL DEFAULT '',
            summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
            digest          VARCHAR(64) NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_ops_daily_signoffs_outlet_date UNIQUE (outlet_id, business_date)
        )
    """))
    conn.execute(text("CREATE INDEX ix_ops_daily_signoffs_company_id ON ops_daily_signoffs (company_id)"))


def downgrade() -> None:
    op.execute(text("DROP TABLE ops_daily_signoffs"))
