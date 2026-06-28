"""Vendors table for Procurement module

Revision ID: 009
Revises: 008
Create Date: 2026-06-28
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE vendors (
            id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name         VARCHAR(300) NOT NULL,
            category     VARCHAR(100) NOT NULL DEFAULT 'General',
            contact_name VARCHAR(200),
            contact_phone VARCHAR(50),
            contact_email VARCHAR(200),
            address      TEXT,
            outlet       VARCHAR(200),
            is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
            notes        TEXT,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_vendors_is_active ON vendors(is_active)")
    op.execute("CREATE INDEX idx_vendors_category ON vendors(category)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_vendors_category")
    op.execute("DROP INDEX IF EXISTS idx_vendors_is_active")
    op.execute("DROP TABLE IF EXISTS vendors")
