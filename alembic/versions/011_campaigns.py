"""Marketing campaigns table

Revision ID: 011
Revises: 010
Create Date: 2026-06-28
"""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE campaign_status AS ENUM ('draft', 'active', 'completed', 'cancelled')
    """)
    op.execute("""
        CREATE TYPE campaign_type AS ENUM (
            'promotion', 'event', 'social-media', 'email', 'other'
        )
    """)
    op.execute("""
        CREATE TABLE campaigns (
            id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            title       VARCHAR(300)  NOT NULL,
            type        campaign_type NOT NULL DEFAULT 'other',
            description TEXT,
            outlet      VARCHAR(200),
            budget      VARCHAR(100),
            start_date  DATE,
            end_date    DATE,
            status      campaign_status NOT NULL DEFAULT 'draft',
            pic         VARCHAR(200),
            created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_campaigns_status ON campaigns(status)")
    op.execute("CREATE INDEX idx_campaigns_outlet ON campaigns(outlet)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_campaigns_outlet")
    op.execute("DROP INDEX IF EXISTS idx_campaigns_status")
    op.execute("DROP TABLE IF EXISTS campaigns")
    op.execute("DROP TYPE IF EXISTS campaign_type")
    op.execute("DROP TYPE IF EXISTS campaign_status")
