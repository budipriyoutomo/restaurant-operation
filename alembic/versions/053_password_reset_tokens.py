"""Forgot password by email

Revision ID: 053
Revises: 052
Create Date: 2026-10-17

password_reset_tokens   one-time links (only the SHA-256 is stored), valid for
                        PASSWORD_RESET_EXPIRE_MINUTES; global, not company-scoped
"""

from alembic import op
from sqlalchemy import text

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE password_reset_tokens (
            id          UUID PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(64) NOT NULL UNIQUE,
            expires_at  TIMESTAMPTZ NOT NULL,
            used_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id)"))


def downgrade() -> None:
    op.get_bind().execute(text("DROP TABLE password_reset_tokens"))
