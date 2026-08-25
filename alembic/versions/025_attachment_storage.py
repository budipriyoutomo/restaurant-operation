"""Real file uploads for work-order attachments (Tier 5.1)

Revision ID: 025
Revises: 024
Create Date: 2026-07-06

An attachment used to be a bare external `file_url`. This adds columns for
files stored by the app itself:
  - storage_key   : backend-relative key (NULL for legacy URL-only rows)
  - thumbnail_key : small preview for slow field connections
  - mime_type, size_bytes

`file_url` becomes nullable: uploaded files are served via a route by id, not by
a caller-supplied URL. Existing URL rows keep working (storage_key NULL).
"""

from alembic import op
from sqlalchemy import text

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE work_order_attachments
            ADD COLUMN IF NOT EXISTS storage_key   VARCHAR(300),
            ADD COLUMN IF NOT EXISTS thumbnail_key VARCHAR(300),
            ADD COLUMN IF NOT EXISTS mime_type     VARCHAR(100),
            ADD COLUMN IF NOT EXISTS size_bytes    INTEGER
    """))
    # Legacy rows have a URL; uploaded rows have a storage_key instead.
    conn.execute(text("ALTER TABLE work_order_attachments ALTER COLUMN file_url DROP NOT NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    # Restore NOT NULL only if no rows would violate it.
    conn.execute(text("UPDATE work_order_attachments SET file_url = '' WHERE file_url IS NULL"))
    conn.execute(text("ALTER TABLE work_order_attachments ALTER COLUMN file_url SET NOT NULL"))
    conn.execute(text("""
        ALTER TABLE work_order_attachments
            DROP COLUMN IF EXISTS size_bytes,
            DROP COLUMN IF EXISTS mime_type,
            DROP COLUMN IF EXISTS thumbnail_key,
            DROP COLUMN IF EXISTS storage_key
    """))
