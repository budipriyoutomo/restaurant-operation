"""Home page per role (Todo-Pilot §19 / UX-6)

Revision ID: 042
Revises: 041
Create Date: 2026-10-14

roles.home_page: the page users of the role land on. NULL = "Pekerjaan Saya"
(My Work), the default for field roles. The admin role keeps the executive
dashboard it always landed on.
"""

from alembic import op
from sqlalchemy import text

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE roles ADD COLUMN home_page VARCHAR(40) NULL"))
    op.execute(text("UPDATE roles SET home_page = 'dashboard' WHERE key = 'admin'"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE roles DROP COLUMN IF EXISTS home_page"))
