"""Multi-outlet — user↔outlet membership (Tier 4.1)

Revision ID: 023
Revises: 022
Create Date: 2026-07-06

Adds `user_outlets`: which outlets a user is allowed to see.

Many-to-many (not a single `users.outlet_id`) because area/roving managers
genuinely cover several outlets — modelling it as one column would force
duplicate accounts.

Purely additive: nothing reads this table until Tier 4.2 turns on query
scoping, so applying this migration changes no existing behaviour.
"""

from alembic import op
from sqlalchemy import text

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_outlets (
            user_id    UUID        NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            outlet_id  UUID        NOT NULL REFERENCES outlets(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, outlet_id)
        )
    """))
    # PK already covers (user_id, ...) lookups; this one serves the reverse
    # question "who can see outlet X?" (used by outlet-scoped notification fan-out).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_user_outlets_outlet ON user_outlets(outlet_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS user_outlets"))
