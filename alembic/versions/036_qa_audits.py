"""QA audit checklist: templates, sessions, findings, photos (Todo-Pilot §7)

Revision ID: 036
Revises: 035
Create Date: 2026-10-03

Tables are prefixed qa_audit_ to stay apart from audit_logs (system audit trail).
Findings snapshot the template item (title, weight, flags) so editing a template
never rewrites past audits. The `qa` module becomes manageable (run audits,
edit templates); the built-in manager role gets qa:manage, like other modules
it already manages.
"""

from alembic import op
from sqlalchemy import text

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE qa_audit_templates (
            id          UUID PRIMARY KEY,
            name        VARCHAR(200) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("""
        CREATE TABLE qa_audit_template_items (
            id             UUID PRIMARY KEY,
            template_id    UUID NOT NULL REFERENCES qa_audit_templates(id) ON DELETE CASCADE,
            title          VARCHAR(500) NOT NULL,
            category       VARCHAR(100) NOT NULL DEFAULT '',
            weight         INTEGER NOT NULL DEFAULT 1 CHECK (weight BETWEEN 1 AND 10),
            requires_photo BOOLEAN NOT NULL DEFAULT false,
            is_critical    BOOLEAN NOT NULL DEFAULT false,
            order_index    INTEGER NOT NULL DEFAULT 0,
            is_active      BOOLEAN NOT NULL DEFAULT true
        )
    """))
    conn.execute(text("CREATE INDEX ix_qa_audit_template_items_template ON qa_audit_template_items (template_id)"))
    conn.execute(text("""
        CREATE TABLE qa_audit_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """))
    conn.execute(text("""
        CREATE TABLE qa_audit_sessions (
            id            UUID PRIMARY KEY,
            number        VARCHAR(30) NOT NULL UNIQUE,
            template_id   UUID NOT NULL REFERENCES qa_audit_templates(id) ON DELETE RESTRICT,
            template_name VARCHAR(200) NOT NULL,
            outlet        VARCHAR(200) NOT NULL,
            outlet_id     UUID NULL REFERENCES outlets(id) ON DELETE RESTRICT,
            auditor_id    UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            auditor_name  VARCHAR(200) NOT NULL DEFAULT '',
            audit_date    DATE NOT NULL,
            status        VARCHAR(20) NOT NULL DEFAULT 'in_progress'
                          CHECK (status IN ('in_progress', 'submitted')),
            score         NUMERIC(5, 1) NULL,
            submitted_at  TIMESTAMPTZ NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX ix_qa_audit_sessions_outlet_template ON qa_audit_sessions (outlet_id, template_id, submitted_at)"))
    conn.execute(text("""
        CREATE TABLE qa_audit_findings (
            id               UUID PRIMARY KEY,
            session_id       UUID NOT NULL REFERENCES qa_audit_sessions(id) ON DELETE CASCADE,
            template_item_id UUID NULL REFERENCES qa_audit_template_items(id) ON DELETE SET NULL,
            title            VARCHAR(500) NOT NULL,
            category         VARCHAR(100) NOT NULL DEFAULT '',
            weight           INTEGER NOT NULL DEFAULT 1,
            requires_photo   BOOLEAN NOT NULL DEFAULT false,
            is_critical      BOOLEAN NOT NULL DEFAULT false,
            order_index      INTEGER NOT NULL DEFAULT 0,
            result           VARCHAR(10) NULL CHECK (result IN ('pass', 'fail', 'na')),
            notes            TEXT NOT NULL DEFAULT '',
            is_repeat        BOOLEAN NOT NULL DEFAULT false,
            issue_id         UUID NULL REFERENCES issues(id) ON DELETE SET NULL
        )
    """))
    conn.execute(text("CREATE INDEX ix_qa_audit_findings_session ON qa_audit_findings (session_id)"))
    conn.execute(text("""
        CREATE TABLE qa_audit_photos (
            id            UUID PRIMARY KEY,
            finding_id    UUID NOT NULL REFERENCES qa_audit_findings(id) ON DELETE CASCADE,
            storage_key   VARCHAR(300) NOT NULL,
            thumbnail_key VARCHAR(300) NULL,
            mime_type     VARCHAR(100) NOT NULL,
            size_bytes    INTEGER NOT NULL,
            uploaded_by   UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX ix_qa_audit_photos_finding ON qa_audit_photos (finding_id)"))
    # Managers already manage issues/cmms/…; running outlet audits fits the same tier.
    conn.execute(text("""
        UPDATE roles SET permissions = permissions || '{"qa": "manage"}'::jsonb
        WHERE key = 'manager'
    """))


def downgrade() -> None:
    conn = op.get_bind()
    for t in ("qa_audit_photos", "qa_audit_findings", "qa_audit_sessions",
              "qa_audit_number_sequences", "qa_audit_template_items", "qa_audit_templates"):
        conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
    conn.execute(text("""
        UPDATE roles SET permissions = permissions || '{"qa": "view"}'::jsonb
        WHERE key = 'manager' AND permissions->>'qa' = 'manage'
    """))
