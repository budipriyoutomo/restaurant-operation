"""Initial schema — Issue Core

Creates all PostgreSQL enum types and tables for Issues, Tasks, and ApprovalRequests,
plus the three number-sequence tables that back the ISS-/TSK-/APR- number generation.

Revision ID: 001
Revises:
Create Date: 2026-06-20
"""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # PostgreSQL enum types — values identical to frontend TypeScript types
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TYPE issue_category AS ENUM (
            'Maintenance', 'IT Support', 'Compliance', 'Training',
            'Procurement', 'Marketing', 'Asset Purchase', 'Guest Service', 'Other'
        )
    """)
    op.execute("""
        CREATE TYPE priority AS ENUM ('critical', 'high', 'medium', 'low')
    """)
    op.execute("""
        CREATE TYPE issue_status AS ENUM (
            'open', 'assigned', 'in-progress', 'waiting', 'resolved', 'closed'
        )
    """)
    op.execute("""
        CREATE TYPE task_status AS ENUM (
            'open', 'assigned', 'in-progress', 'waiting', 'resolved', 'closed'
        )
    """)
    op.execute("""
        CREATE TYPE approval_type AS ENUM (
            'procurement', 'marketing', 'training', 'asset-purchase'
        )
    """)
    op.execute("""
        CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected')
    """)

    # ------------------------------------------------------------------
    # Sequence tables — atomic per-year counters for ISS-/TSK-/APR- numbers
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE issue_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE task_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE approval_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ------------------------------------------------------------------
    # issues — single source of truth
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE issues (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            number      VARCHAR(30) NOT NULL UNIQUE,
            title       VARCHAR(500) NOT NULL,
            description TEXT,
            outlet      VARCHAR(200) NOT NULL,
            category    issue_category NOT NULL,
            priority    priority       NOT NULL DEFAULT 'medium',
            status      issue_status   NOT NULL DEFAULT 'open',
            assignee    VARCHAR(200),
            due_date    DATE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_issues_status   ON issues(status)")
    op.execute("CREATE INDEX idx_issues_category ON issues(category)")
    op.execute("CREATE INDEX idx_issues_outlet   ON issues(outlet)")

    # ------------------------------------------------------------------
    # tasks — always derived from an Issue, never created standalone (FR-9)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE tasks (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            issue_id     UUID        NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            issue_number VARCHAR(30) NOT NULL,
            number       VARCHAR(30) NOT NULL UNIQUE,
            title        VARCHAR(500) NOT NULL,
            description  TEXT,
            status       task_status NOT NULL DEFAULT 'open',
            priority     priority    NOT NULL DEFAULT 'medium',
            assignee     VARCHAR(200),
            due_date     DATE,
            outlet       VARCHAR(200),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_tasks_issue_id ON tasks(issue_id)")
    op.execute("CREATE INDEX idx_tasks_status   ON tasks(status)")

    # ------------------------------------------------------------------
    # approval_requests — max one per Issue in MVP (enforced by UNIQUE on issue_id)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE approval_requests (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            issue_id      UUID        NOT NULL UNIQUE REFERENCES issues(id) ON DELETE CASCADE,
            issue_number  VARCHAR(30) NOT NULL,
            number        VARCHAR(30) NOT NULL UNIQUE,
            title         VARCHAR(500) NOT NULL,
            type          approval_type   NOT NULL,
            description   TEXT,
            requester     VARCHAR(200),
            outlet        VARCHAR(200),
            requested_date DATE,
            amount        VARCHAR(100),
            status        approval_status NOT NULL DEFAULT 'pending',
            decided_at    TIMESTAMPTZ,
            decided_by    VARCHAR(200),
            decision_note TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_approvals_status ON approval_requests(status)")
    op.execute("CREATE INDEX idx_approvals_type   ON approval_requests(type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS issues CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_number_sequences CASCADE")
    op.execute("DROP TABLE IF EXISTS task_number_sequences CASCADE")
    op.execute("DROP TABLE IF EXISTS issue_number_sequences CASCADE")

    op.execute("DROP TYPE IF EXISTS approval_status")
    op.execute("DROP TYPE IF EXISTS approval_type")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS issue_status")
    op.execute("DROP TYPE IF EXISTS priority")
    op.execute("DROP TYPE IF EXISTS issue_category")
