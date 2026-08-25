"""Asset QR tokens (Tier 5.2)

Revision ID: 026
Revises: 025
Create Date: 2026-07-06

Adds `assets.qr_token`: an opaque, unique token printed on a sticker stuck to
the physical asset. Scanning it resolves to the asset (and its active work
order) so a technician lands straight on the right screen in the field.

Opaque on purpose — the token is NOT the asset UUID, so a sticker never leaks an
internal id, and tokens can be rotated if a sticker is compromised. Existing
assets are backfilled with a random token.
"""

from alembic import op
from sqlalchemy import text

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE assets ADD COLUMN IF NOT EXISTS qr_token VARCHAR(40)"))
    # Backfill existing rows with a random token. gen_random_uuid() is built-in
    # (no pgcrypto needed); stripping the dashes gives a 32-char hex token.
    conn.execute(text("""
        UPDATE assets
           SET qr_token = replace(gen_random_uuid()::text, '-', '')
         WHERE qr_token IS NULL
    """))
    conn.execute(text("ALTER TABLE assets ALTER COLUMN qr_token SET NOT NULL"))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_qr_token ON assets(qr_token)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS uq_assets_qr_token"))
    conn.execute(text("ALTER TABLE assets DROP COLUMN IF EXISTS qr_token"))
