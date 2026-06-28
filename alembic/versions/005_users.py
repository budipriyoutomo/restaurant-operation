"""Users table for authentication

Revision ID: 005
Revises: 004
Create Date: 2026-06-20
"""

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            email         VARCHAR(200) NOT NULL UNIQUE,
            name          VARCHAR(200) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role          VARCHAR(50)  NOT NULL DEFAULT 'staff',
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_users_email ON users(email)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_email")
    op.execute("DROP TABLE IF EXISTS users")
