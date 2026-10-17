"""Issue categories become master data (Todo-Ops-Checklist P1)

Revision ID: 044
Revises: 043
Create Date: 2026-10-15

Before: issues.category was the Postgres enum issue_category (9 fixed values)
and the categories table only fed the PIC mapping. After:

  categories  + system_key, module, default_generate_task,
                default_generate_approval, approval_type, icon, is_active;
                `type` (category_type enum) is replaced by `module`
                (maintenance → maintenance, operations → general).
                Active names unique per company (case-insensitive).
                Every company gets the system categories — existing rows with
                the default name are adopted, the rest inserted — Food Safety
                included.
  issues      category → VARCHAR(200) (denormalized name) + category_id FK,
              backfilled through the system key of the old enum value.
              issue_category is dropped.

Behaviour (corrective WO, guest case, approval type, module pages) now follows
categories.module — see app/core/category_defaults.py.
"""

import uuid

from alembic import op
from sqlalchemy import text

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None

# Frozen copy of app/core/category_defaults.SYSTEM_CATEGORIES at this revision:
# key → (name, description, module, task, approval, approval_type, icon)
_SYSTEM = {
    "maintenance":    ("Maintenance",    "Perbaikan peralatan dan fasilitas", "maintenance",    True, False, "maintenance",    "wrench"),
    "it_support":     ("IT Support",     "Perangkat, jaringan, dan POS",      "it_support",     True, False, None,             "monitor"),
    "compliance":     ("Compliance",     "Temuan audit dan kepatuhan",        "compliance",     True, False, None,             "shield-check"),
    "training":       ("Training",       "Pelatihan staff",                   "general",        True, True,  "training",       "graduation-cap"),
    "procurement":    ("Procurement",    "Pengadaan barang dan bahan",        "general",        True, True,  "procurement",    "shopping-cart"),
    "marketing":      ("Marketing",      "Promosi dan kampanye",              "general",        True, True,  "marketing",      "megaphone"),
    "asset_purchase": ("Asset Purchase", "Pembelian aset baru",               "asset_purchase", True, True,  "asset-purchase", "package"),
    "guest_service":  ("Guest Service",  "Keluhan dan pengalaman tamu",       "guest_service",  True, False, None,             "message-square-warning"),
    "other":          ("Other",          "Lain-lain",                         "general",        True, False, None,             "circle-help"),
    "food_safety":    ("Food Safety",    "Keamanan pangan dan log suhu",      "compliance",     True, False, None,             "thermometer"),
}
_LEGACY_ENUM = [d[0] for k, d in _SYSTEM.items() if k != "food_safety"]
_MODULES = ("maintenance", "it_support", "compliance", "guest_service", "asset_purchase", "general")
_APPROVAL_TYPES = ("procurement", "marketing", "training", "asset-purchase", "maintenance")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    conn = op.get_bind()

    # ── categories: new columns, module from the old type ────────────────────
    conn.execute(text("""
        ALTER TABLE categories
            ADD COLUMN system_key                VARCHAR(40),
            ADD COLUMN module                    VARCHAR(30) NOT NULL DEFAULT 'general',
            ADD COLUMN default_generate_task     BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN default_generate_approval BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN approval_type             VARCHAR(30),
            ADD COLUMN icon                      VARCHAR(40),
            ADD COLUMN is_active                 BOOLEAN NOT NULL DEFAULT true
    """))
    conn.execute(text("UPDATE categories SET module = 'maintenance' WHERE type = 'maintenance'"))
    conn.execute(text("UPDATE categories SET is_active = false WHERE deleted_at IS NOT NULL"))

    # Active names must be unique per company before the unique index: suffix
    # later duplicates ("HVAC", "HVAC (2)").
    conn.execute(text("""
        UPDATE categories c SET name = c.name || ' (' || d.rn || ')'
        FROM (
            SELECT id, row_number() OVER (PARTITION BY company_id, lower(name) ORDER BY id) AS rn
            FROM categories WHERE deleted_at IS NULL
        ) d
        WHERE c.id = d.id AND d.rn > 1
    """))

    # ── system categories per company ────────────────────────────────────────
    company_ids = [r[0] for r in conn.execute(text("SELECT id FROM companies"))]
    for company_id in company_ids:
        for key, (name, desc, module, task, approval, approval_type, icon) in _SYSTEM.items():
            params = {"c": company_id, "k": key, "n": name, "d": desc, "m": module, "t": task, "a": approval,
                      "at": approval_type, "i": icon}
            adopted = conn.execute(text("""
                UPDATE categories
                   SET system_key = :k, module = :m, default_generate_task = :t,
                       default_generate_approval = :a, approval_type = :at,
                       icon = COALESCE(icon, :i), is_active = true
                 WHERE id = (SELECT id FROM categories
                              WHERE company_id = :c AND deleted_at IS NULL AND system_key IS NULL
                                AND lower(name) = lower(:n)
                              LIMIT 1)
                RETURNING id
            """), params).first()
            if adopted is None:
                conn.execute(text("""
                    INSERT INTO categories (id, company_id, name, description, system_key, module,
                                            default_generate_task, default_generate_approval, approval_type, icon)
                    VALUES (:id, :c, :n, :d, :k, :m, :t, :a, :at, :i)
                """), {**params, "id": uuid.uuid4()})

    conn.execute(text("ALTER TABLE categories DROP COLUMN type"))
    conn.execute(text("DROP TYPE IF EXISTS category_type"))
    conn.execute(text(f"ALTER TABLE categories ADD CONSTRAINT categories_module_check CHECK (module IN ({_in(_MODULES)}))"))
    conn.execute(text(f"""ALTER TABLE categories ADD CONSTRAINT categories_approval_type_check
                          CHECK (approval_type IS NULL OR approval_type IN ({_in(_APPROVAL_TYPES)}))"""))
    conn.execute(text("""CREATE UNIQUE INDEX uq_categories_company_name ON categories (company_id, lower(name))
                         WHERE deleted_at IS NULL"""))
    conn.execute(text("""CREATE UNIQUE INDEX uq_categories_company_system_key ON categories (company_id, system_key)
                         WHERE system_key IS NOT NULL"""))

    # ── issues: enum → name + FK ─────────────────────────────────────────────
    conn.execute(text("ALTER TABLE issues ADD COLUMN category_id UUID"))
    key_case = " ".join(f"WHEN '{d[0]}' THEN '{k}'" for k, d in _SYSTEM.items() if k != "food_safety")
    conn.execute(text(f"""
        UPDATE issues i SET category_id = c.id
        FROM categories c
        WHERE c.company_id = i.company_id
          AND c.system_key = CASE i.category::text {key_case} ELSE 'other' END
    """))
    conn.execute(text("ALTER TABLE issues ALTER COLUMN category TYPE VARCHAR(200) USING category::text"))
    conn.execute(text("UPDATE issues i SET category = c.name FROM categories c WHERE c.id = i.category_id"))
    conn.execute(text("ALTER TABLE issues ALTER COLUMN category_id SET NOT NULL"))
    conn.execute(text("""ALTER TABLE issues ADD CONSTRAINT issues_category_id_fkey
                         FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE RESTRICT"""))
    conn.execute(text("CREATE INDEX ix_issues_category_id ON issues (category_id)"))
    conn.execute(text("DROP TYPE issue_category"))


def downgrade() -> None:
    conn = op.get_bind()

    # issues: back to the 9-value enum. Categories without a legacy system key
    # (custom ones, Food Safety) fold into Other.
    conn.execute(text(f"CREATE TYPE issue_category AS ENUM ({_in(_LEGACY_ENUM)})"))
    name_case = " ".join(f"WHEN '{k}' THEN '{d[0]}'" for k, d in _SYSTEM.items() if k != "food_safety")
    conn.execute(text(f"""
        UPDATE issues i SET category = CASE c.system_key {name_case} ELSE 'Other' END
        FROM categories c WHERE c.id = i.category_id
    """))
    conn.execute(text("DROP INDEX IF EXISTS ix_issues_category_id"))
    conn.execute(text("ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_category_id_fkey"))
    conn.execute(text("ALTER TABLE issues DROP COLUMN category_id"))
    conn.execute(text("ALTER TABLE issues ALTER COLUMN category TYPE issue_category USING category::issue_category"))

    # categories: module → type. System rows stay as plain categories.
    conn.execute(text("CREATE TYPE category_type AS ENUM ('operations', 'maintenance')"))
    conn.execute(text("ALTER TABLE categories ADD COLUMN type category_type NOT NULL DEFAULT 'operations'"))
    conn.execute(text("UPDATE categories SET type = 'maintenance' WHERE module = 'maintenance'"))
    conn.execute(text("DROP INDEX IF EXISTS uq_categories_company_system_key"))
    conn.execute(text("DROP INDEX IF EXISTS uq_categories_company_name"))
    conn.execute(text("ALTER TABLE categories DROP CONSTRAINT IF EXISTS categories_approval_type_check"))
    conn.execute(text("ALTER TABLE categories DROP CONSTRAINT IF EXISTS categories_module_check"))
    conn.execute(text("""
        ALTER TABLE categories
            DROP COLUMN system_key, DROP COLUMN module, DROP COLUMN default_generate_task,
            DROP COLUMN default_generate_approval, DROP COLUMN approval_type, DROP COLUMN icon,
            DROP COLUMN is_active
    """))
