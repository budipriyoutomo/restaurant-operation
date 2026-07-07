"""Asset purchase cost — enables repair-vs-replace analytics

Revision ID: 017
Revises: 016
Create Date: 2026-07-06

Adds `assets.purchase_cost` (INTEGER IDR, nullable). Used by CMMS analytics
(Tier 3) to flag repair-vs-replace when accumulated repair cost exceeds a
configured ratio of the asset's purchase value. Additive & non-breaking.
"""

from alembic import op
from sqlalchemy import text

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS purchase_cost INTEGER"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE assets DROP COLUMN IF EXISTS purchase_cost"))
