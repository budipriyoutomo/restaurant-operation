"""Photos on an Issue (Todo-Pilot §20 / UX-7)

Revision ID: 043
Revises: 042
Create Date: 2026-10-14

Staff attach a photo when they report a problem. Same storage pipeline as
work-order photos (storage_service: validated, EXIF stripped, thumbnailed).
Tenant-scoped like every business table (migration 040).
"""

from alembic import op
from sqlalchemy import text

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE issue_photos (
            id            UUID PRIMARY KEY,
            company_id    UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            issue_id      UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            caption       VARCHAR(500),
            uploaded_by   UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            storage_key   VARCHAR(300) NOT NULL,
            thumbnail_key VARCHAR(300),
            mime_type     VARCHAR(100),
            size_bytes    INTEGER,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text("CREATE INDEX ix_issue_photos_company_id ON issue_photos (company_id)"))
    op.execute(text("CREATE INDEX ix_issue_photos_issue_id ON issue_photos (issue_id)"))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS issue_photos"))
