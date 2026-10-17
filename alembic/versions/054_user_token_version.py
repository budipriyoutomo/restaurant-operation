"""Sign out every session when a password changes

Revision ID: 054
Revises: 053
Create Date: 2026-10-17

users.token_version   in every JWT ("ver"); bumped by a password change/reset,
                      so tokens issued before stop working
"""

from alembic import op
from sqlalchemy import text

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))


def downgrade() -> None:
    op.get_bind().execute(text("ALTER TABLE users DROP COLUMN token_version"))
