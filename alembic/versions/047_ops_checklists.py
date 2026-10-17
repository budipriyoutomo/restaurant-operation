"""Daily ops checklist (Todo-Ops-Checklist Fase 1)

Revision ID: 047
Revises: 046
Create Date: 2026-10-15

  outlets.timezone              IANA zone of the outlet's wall clock (default Asia/Jakarta)
  ops_checklist_templates       + items (snapshotted into runs; deactivated, never deleted)
  ops_checklist_schedules       template × outlet × weekdays × due time
  ops_checklist_runs            one per (schedule, shift date) — UNIQUE, the generator's idempotency key
  ops_checklist_run_items       snapshot + answer + issue_id (dedup) + escalated_issue
  ops_checklist_photos
  ops_checklist_number_sequences OPS-2026-00001 per company per year
  roles                         module `ops`: staff view (fill in), manager manage (admin = all)

Status/type columns use CHECK constraints, not Postgres enums (no ADD VALUE gotcha).
"""

from alembic import op
from sqlalchemy import text

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None

_TABLES = (
    "ops_checklist_photos", "ops_checklist_run_items", "ops_checklist_runs", "ops_checklist_number_sequences",
    "ops_checklist_schedules", "ops_checklist_template_items", "ops_checklist_templates",
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE outlets ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Jakarta'"))

    conn.execute(text("""
        CREATE TABLE ops_checklist_templates (
            id          UUID PRIMARY KEY,
            company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            name        VARCHAR(200) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            shift       VARCHAR(20) NOT NULL DEFAULT 'other',
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ops_checklist_templates_shift_check CHECK (shift IN ('opening', 'midday', 'closing', 'other'))
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_template_items (
            id                UUID PRIMARY KEY,
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            template_id       UUID NOT NULL REFERENCES ops_checklist_templates(id) ON DELETE CASCADE,
            title             VARCHAR(500) NOT NULL,
            section           VARCHAR(100) NOT NULL DEFAULT '',
            item_type         VARCHAR(20) NOT NULL DEFAULT 'check',
            min_value         NUMERIC(6,1),
            max_value         NUMERIC(6,1),
            unit              VARCHAR(20) NOT NULL DEFAULT '',
            requires_photo    BOOLEAN NOT NULL DEFAULT false,
            is_critical       BOOLEAN NOT NULL DEFAULT false,
            issue_category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
            order_index       INTEGER NOT NULL DEFAULT 0,
            is_active         BOOLEAN NOT NULL DEFAULT true,
            CONSTRAINT ops_checklist_template_items_type_check CHECK (item_type IN ('check', 'number', 'temperature')),
            CONSTRAINT ops_checklist_template_items_range_check
                CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_schedules (
            id                  UUID PRIMARY KEY,
            company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            template_id         UUID NOT NULL REFERENCES ops_checklist_templates(id) ON DELETE RESTRICT,
            outlet              VARCHAR(200) NOT NULL,
            outlet_id           UUID NOT NULL REFERENCES outlets(id) ON DELETE RESTRICT,
            days_of_week        INTEGER[] NOT NULL DEFAULT '{}',
            due_time            TIME NOT NULL,
            open_minutes_before INTEGER NOT NULL DEFAULT 60,
            grace_minutes       INTEGER NOT NULL DEFAULT 120,
            is_active           BOOLEAN NOT NULL DEFAULT true,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at          TIMESTAMPTZ
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_number_sequences (
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            year       INTEGER NOT NULL,
            last_seq   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (company_id, year)
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_runs (
            id                UUID PRIMARY KEY,
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            number            VARCHAR(30) NOT NULL,
            schedule_id       UUID NOT NULL REFERENCES ops_checklist_schedules(id) ON DELETE RESTRICT,
            template_id       UUID NOT NULL REFERENCES ops_checklist_templates(id) ON DELETE RESTRICT,
            template_name     VARCHAR(200) NOT NULL,
            shift             VARCHAR(20) NOT NULL DEFAULT 'other',
            outlet            VARCHAR(200) NOT NULL,
            outlet_id         UUID NOT NULL REFERENCES outlets(id) ON DELETE RESTRICT,
            run_date          DATE NOT NULL,
            opens_at          TIMESTAMPTZ NOT NULL,
            due_at            TIMESTAMPTZ NOT NULL,
            closes_at         TIMESTAMPTZ NOT NULL,
            status            VARCHAR(20) NOT NULL DEFAULT 'pending',
            is_late           BOOLEAN NOT NULL DEFAULT false,
            submitted_by      UUID REFERENCES users(id) ON DELETE SET NULL,
            submitted_by_name VARCHAR(200),
            submitted_at      TIMESTAMPTZ,
            score             NUMERIC(5,1),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_ops_checklist_runs_schedule_date UNIQUE (schedule_id, run_date),
            CONSTRAINT uq_ops_checklist_runs_company_number UNIQUE (company_id, number),
            CONSTRAINT ops_checklist_runs_status_check CHECK (status IN ('pending', 'in_progress', 'submitted', 'missed'))
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_run_items (
            id                UUID PRIMARY KEY,
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            run_id            UUID NOT NULL REFERENCES ops_checklist_runs(id) ON DELETE CASCADE,
            template_item_id  UUID REFERENCES ops_checklist_template_items(id) ON DELETE SET NULL,
            title             VARCHAR(500) NOT NULL,
            section           VARCHAR(100) NOT NULL DEFAULT '',
            item_type         VARCHAR(20) NOT NULL DEFAULT 'check',
            min_value         NUMERIC(6,1),
            max_value         NUMERIC(6,1),
            unit              VARCHAR(20) NOT NULL DEFAULT '',
            requires_photo    BOOLEAN NOT NULL DEFAULT false,
            is_critical       BOOLEAN NOT NULL DEFAULT false,
            issue_category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
            order_index       INTEGER NOT NULL DEFAULT 0,
            result            VARCHAR(10),
            value             NUMERIC(6,1),
            notes             TEXT NOT NULL DEFAULT '',
            corrective_action TEXT,
            recorded_at       TIMESTAMPTZ,
            received_at       TIMESTAMPTZ,
            recorded_by       UUID REFERENCES users(id) ON DELETE SET NULL,
            issue_id          UUID REFERENCES issues(id) ON DELETE SET NULL,
            escalated_issue   BOOLEAN NOT NULL DEFAULT false,
            CONSTRAINT ops_checklist_run_items_result_check CHECK (result IS NULL OR result IN ('pass', 'fail', 'na')),
            CONSTRAINT ops_checklist_run_items_type_check CHECK (item_type IN ('check', 'number', 'temperature'))
        )
    """))
    conn.execute(text("""
        CREATE TABLE ops_checklist_photos (
            id            UUID PRIMARY KEY,
            company_id    UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            run_item_id   UUID NOT NULL REFERENCES ops_checklist_run_items(id) ON DELETE CASCADE,
            storage_key   VARCHAR(300) NOT NULL,
            thumbnail_key VARCHAR(300),
            mime_type     VARCHAR(100) NOT NULL,
            size_bytes    INTEGER NOT NULL,
            uploaded_by   UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    for table in _TABLES:
        conn.execute(text(f"CREATE INDEX ix_{table}_company_id ON {table} (company_id)"))
    for name, table, cols in (
        ("ix_ops_checklist_template_items_template_id", "ops_checklist_template_items", "template_id"),
        ("ix_ops_checklist_schedules_template_id", "ops_checklist_schedules", "template_id"),
        ("ix_ops_checklist_schedules_outlet_id", "ops_checklist_schedules", "outlet_id"),
        ("ix_ops_checklist_runs_outlet_date", "ops_checklist_runs", "outlet_id, run_date"),
        ("ix_ops_checklist_runs_status_closes", "ops_checklist_runs", "status, closes_at"),
        ("ix_ops_checklist_run_items_run_id", "ops_checklist_run_items", "run_id"),
        ("ix_ops_checklist_run_items_template_item_id", "ops_checklist_run_items", "template_item_id"),
        ("ix_ops_checklist_run_items_issue_id", "ops_checklist_run_items", "issue_id"),
        ("ix_ops_checklist_photos_run_item_id", "ops_checklist_photos", "run_item_id"),
    ):
        conn.execute(text(f"CREATE INDEX {name} ON {table} ({cols})"))

    # Filling in a checklist is everyday staff work; managers run templates/schedules.
    conn.execute(text("""UPDATE roles SET permissions = permissions || '{"ops": "view"}'::jsonb WHERE key = 'staff'"""))
    conn.execute(text("""UPDATE roles SET permissions = permissions || '{"ops": "manage"}'::jsonb WHERE key = 'manager'"""))


def downgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES:
        conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    conn.execute(text("ALTER TABLE outlets DROP COLUMN timezone"))
    conn.execute(text("UPDATE roles SET permissions = permissions - 'ops'"))
