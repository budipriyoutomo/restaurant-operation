"""Comments on an Issue (Todo-Ops-Checklist P3)

Revision ID: 046
Revises: 045
Create Date: 2026-10-15

kind = user (written in the drawer) | system (written by a service, e.g. a
daily checklist item failing again on an Issue that is still open).
"""

from alembic import op
from sqlalchemy import text

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE issue_comments (
            id             UUID PRIMARY KEY,
            company_id     UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            issue_id       UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            author_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            author_name    VARCHAR(200) NOT NULL,
            body           TEXT NOT NULL,
            kind           VARCHAR(10) NOT NULL DEFAULT 'user',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT issue_comments_kind_check CHECK (kind IN ('user', 'system'))
        )
    """))
    op.execute(text("CREATE INDEX ix_issue_comments_company_id ON issue_comments (company_id)"))
    op.execute(text("CREATE INDEX ix_issue_comments_issue_id ON issue_comments (issue_id)"))


def downgrade() -> None:
    op.execute(text("DROP TABLE issue_comments"))
