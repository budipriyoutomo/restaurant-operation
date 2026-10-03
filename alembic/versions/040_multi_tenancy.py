"""Multi-tenancy: companies + company_id on every business table (Todo-Pilot §11)

- `companies` table. All existing data moves to one company, "Default Company"
  (slug `default`) — rename it later from the platform admin page.
- `company_id` (NOT NULL, FK, indexed) on every business table, including child
  tables. users.company_id stays NULL-able: platform admins belong to no company.
  users.is_platform_admin flags the SaaS operator.
- Business keys become unique per company instead of globally: outlet code,
  PIC email, part SKU, and every document number (ISS/TSK/APR/WO/AST/PR/PO/GRN/AUD).
  users.email stays globally unique (one account = one company); assets.qr_token
  stays global (it is an unguessable token).
- Number sequences are counted per (company, year).
- roles: surrogate `id` primary key, `key` unique per company. users point at
  (company_id, role); role_outlets at role_id.

The ORM enforces the company filter (app/core/tenancy.py); this migration only
reshapes the schema and backfills.

Revision ID: 040
Revises: 039
"""

import uuid

from alembic import op
from sqlalchemy import text

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

DEFAULT_COMPANY_NAME = "Default Company"
DEFAULT_COMPANY_SLUG = "default"

SEQUENCES = {
    "issue_number_sequences": ["year"],
    "task_number_sequences": ["year"],
    "approval_number_sequences": ["year"],
    "asset_number_sequences": ["year"],
    "work_order_number_sequences": ["year"],
    "qa_audit_number_sequences": ["year"],
    "procurement_number_sequences": ["prefix", "year"],
}

TENANT_TABLES = [
    "approval_policies", "approval_requests", "approval_steps", "assets", "audit_logs", "budgets",
    "campaigns", "categories", "goods_receipt_items", "goods_receipts", "guest_cases", "idempotency_keys",
    "issues", "meter_readings", "notifications", "outlets", "parts", "pics", "pm_schedules",
    "purchase_order_items", "purchase_orders", "purchase_request_items", "purchase_requests",
    "qa_audit_findings", "qa_audit_photos", "qa_audit_sessions", "qa_audit_template_items",
    "qa_audit_templates", "roles", "tasks", "training_enrollments", "training_programs", "users",
    "vendors", "whatsapp_outbox", "work_order_attachments", "work_order_checklist_items",
    "work_order_parts", "work_orders",
] + list(SEQUENCES)

# (table, old unique constraint, column(s)) → UNIQUE (company_id, column(s))
PER_COMPANY_UNIQUE = [
    ("outlets", "outlets_code_key", "code", "uq_outlets_company_code"),
    ("pics", "pics_email_key", "email", "uq_pics_company_email"),
    ("parts", "parts_sku_key", "sku", "uq_parts_company_sku"),
    ("issues", "issues_number_key", "number", "uq_issues_company_number"),
    ("tasks", "tasks_number_key", "number", "uq_tasks_company_number"),
    ("approval_requests", "approval_requests_number_key", "number", "uq_approval_requests_company_number"),
    ("assets", "assets_number_key", "number", "uq_assets_company_number"),
    ("work_orders", "work_orders_number_key", "number", "uq_work_orders_company_number"),
    ("purchase_requests", "purchase_requests_number_key", "number", "uq_purchase_requests_company_number"),
    ("purchase_orders", "purchase_orders_number_key", "number", "uq_purchase_orders_company_number"),
    ("goods_receipts", "goods_receipts_number_key", "number", "uq_goods_receipts_company_number"),
    ("qa_audit_sessions", "qa_audit_sessions_number_key", "number", "uq_qa_audit_sessions_company_number"),
    ("budgets", "budgets_outlet_id_period_key", "outlet_id, period", "uq_budget_outlet_period"),
]


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE companies (
            id          UUID PRIMARY KEY,
            name        VARCHAR(200) NOT NULL,
            slug        VARCHAR(80)  NOT NULL UNIQUE,
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """))
    company_id = uuid.uuid4()
    conn.execute(text("INSERT INTO companies (id, name, slug) VALUES (:id, :name, :slug)"),
                 {"id": company_id, "name": DEFAULT_COMPANY_NAME, "slug": DEFAULT_COMPANY_SLUG})

    # Old role-key foreign keys go before roles change shape.
    conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_role"))
    conn.execute(text("ALTER TABLE role_outlets DROP CONSTRAINT IF EXISTS role_outlets_role_key_fkey"))

    for table in TENANT_TABLES:
        is_sequence = table in SEQUENCES
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN company_id UUID"))
        conn.execute(text(f"UPDATE {table} SET company_id = :c"), {"c": company_id})
        if table != "users":
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN company_id SET NOT NULL"))
        on_delete = "CASCADE" if is_sequence else "RESTRICT"
        conn.execute(text(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_company_id_fkey "
            f"FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE {on_delete}"
        ))
        if is_sequence:
            cols = ", ".join(["company_id", *SEQUENCES[table]])
            conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey"))
            conn.execute(text(f"ALTER TABLE {table} ADD PRIMARY KEY ({cols})"))
        else:
            conn.execute(text(f"CREATE INDEX ix_{table}_company_id ON {table} (company_id)"))

    conn.execute(text("ALTER TABLE users ADD COLUMN is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE"))

    for table, old, cols, new in PER_COMPANY_UNIQUE:
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {old}"))
        conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {new} UNIQUE (company_id, {cols})"))

    # roles: surrogate id; key unique per company.
    conn.execute(text("ALTER TABLE roles ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid()"))
    conn.execute(text("ALTER TABLE roles ALTER COLUMN id DROP DEFAULT"))
    conn.execute(text("ALTER TABLE roles DROP CONSTRAINT roles_pkey"))
    conn.execute(text("ALTER TABLE roles ADD PRIMARY KEY (id)"))
    conn.execute(text("ALTER TABLE roles ADD CONSTRAINT uq_roles_company_key UNIQUE (company_id, key)"))
    conn.execute(text(
        "ALTER TABLE users ADD CONSTRAINT fk_users_company_role FOREIGN KEY (company_id, role) "
        "REFERENCES roles (company_id, key) ON UPDATE CASCADE"
    ))

    # role_outlets: role_key → role_id.
    conn.execute(text("ALTER TABLE role_outlets ADD COLUMN role_id UUID"))
    conn.execute(text("UPDATE role_outlets ro SET role_id = r.id FROM roles r WHERE r.key = ro.role_key"))
    conn.execute(text("ALTER TABLE role_outlets DROP CONSTRAINT role_outlets_pkey"))
    conn.execute(text("ALTER TABLE role_outlets DROP COLUMN role_key"))
    conn.execute(text("ALTER TABLE role_outlets ALTER COLUMN role_id SET NOT NULL"))
    conn.execute(text("ALTER TABLE role_outlets ADD PRIMARY KEY (role_id, outlet_id)"))
    conn.execute(text(
        "ALTER TABLE role_outlets ADD CONSTRAINT role_outlets_role_id_fkey "
        "FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(text("SELECT count(*) FROM companies")).scalar() > 1:
        raise RuntimeError("Cannot downgrade 040 with more than one company: data would collide.")
    if conn.execute(text("SELECT count(*) FROM users WHERE is_platform_admin")).scalar():
        raise RuntimeError("Delete platform admin users before downgrading 040.")

    # role_outlets back to role_key.
    conn.execute(text("ALTER TABLE role_outlets DROP CONSTRAINT role_outlets_role_id_fkey"))
    conn.execute(text("ALTER TABLE role_outlets ADD COLUMN role_key VARCHAR(50)"))
    conn.execute(text("UPDATE role_outlets ro SET role_key = r.key FROM roles r WHERE r.id = ro.role_id"))
    conn.execute(text("ALTER TABLE role_outlets DROP CONSTRAINT role_outlets_pkey"))
    conn.execute(text("ALTER TABLE role_outlets DROP COLUMN role_id"))
    conn.execute(text("ALTER TABLE role_outlets ALTER COLUMN role_key SET NOT NULL"))
    conn.execute(text("ALTER TABLE role_outlets ADD PRIMARY KEY (role_key, outlet_id)"))

    conn.execute(text("ALTER TABLE users DROP CONSTRAINT fk_users_company_role"))
    conn.execute(text("ALTER TABLE roles DROP CONSTRAINT uq_roles_company_key"))
    conn.execute(text("ALTER TABLE roles DROP CONSTRAINT roles_pkey"))
    conn.execute(text("ALTER TABLE roles DROP COLUMN id"))
    conn.execute(text("ALTER TABLE roles ADD PRIMARY KEY (key)"))
    conn.execute(text(
        "ALTER TABLE role_outlets ADD CONSTRAINT role_outlets_role_key_fkey FOREIGN KEY (role_key) "
        "REFERENCES roles(key) ON UPDATE CASCADE ON DELETE CASCADE"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD CONSTRAINT fk_users_role FOREIGN KEY (role) REFERENCES roles(key) ON UPDATE CASCADE"
    ))

    for table, old, cols, new in PER_COMPANY_UNIQUE:
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {new}"))
        conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {old} UNIQUE ({cols})"))

    conn.execute(text("ALTER TABLE users DROP COLUMN is_platform_admin"))

    for table in TENANT_TABLES:
        if table in SEQUENCES:
            cols = ", ".join(SEQUENCES[table])
            conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey"))
            conn.execute(text(f"ALTER TABLE {table} ADD PRIMARY KEY ({cols})"))
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN company_id"))   # drops its FK and index

    conn.execute(text("DROP TABLE companies"))
