"""Assignee routing: rules category × outlet → user, PICs linked to users (Todo-Ops-Checklist P2)

Revision ID: 045
Revises: 044
Create Date: 2026-10-15

An Issue created without an assignee goes to the (category, outlet) rule →
the (category, all outlets) rule → a PIC of the category who has a user
account → Unassigned. See app/services/assignment_rules.py.

  pics                    + user_id (one PIC per user)
  issue_assignment_rules  category_id, outlet_id (NULL = every outlet),
                          assignee_user_id; one rule per (category, outlet)
"""

from alembic import op
from sqlalchemy import text

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE pics ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL"))
    op.execute(text("""CREATE UNIQUE INDEX uq_pics_company_user ON pics (company_id, user_id)
                       WHERE user_id IS NOT NULL AND deleted_at IS NULL"""))

    op.execute(text("""
        CREATE TABLE issue_assignment_rules (
            id               UUID PRIMARY KEY,
            company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            category_id      UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            outlet_id        UUID REFERENCES outlets(id) ON DELETE CASCADE,
            assignee_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_issue_assignment_rules_scope
                UNIQUE NULLS NOT DISTINCT (company_id, category_id, outlet_id)
        )
    """))
    op.execute(text("CREATE INDEX ix_issue_assignment_rules_company_id ON issue_assignment_rules (company_id)"))
    op.execute(text("CREATE INDEX ix_issue_assignment_rules_category_id ON issue_assignment_rules (category_id)"))


def downgrade() -> None:
    op.execute(text("DROP TABLE issue_assignment_rules"))
    op.execute(text("DROP INDEX IF EXISTS uq_pics_company_user"))
    op.execute(text("ALTER TABLE pics DROP COLUMN user_id"))
