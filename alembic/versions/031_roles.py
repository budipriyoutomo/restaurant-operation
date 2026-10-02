"""Dynamic roles — module permissions + outlet access per role

Revision ID: 031
Revises: 030
Create Date: 2026-10-02

Until now `users.role` was a free string checked against a hardcoded
staff/manager/admin matrix. This adds:

  - `roles`        : configurable roles. `permissions` maps module key ->
                     none | view | manage; `all_outlets` / `role_outlets` give
                     the role's default outlet access; `approval_tier` is the
                     approver_role the role acts as in approval workflows.
  - `role_outlets` : default outlets for roles that are not `all_outlets`.
  - FK users.role -> roles.key.

The three existing roles are inserted with permissions that reproduce the old
hardcoded behaviour, so applying this migration changes nobody's access:
admin sees all outlets, staff/manager see none by default — their existing
`user_outlets` rows now act as a personal override, which is what they already
were in practice.
"""

import json

from alembic import op
from sqlalchemy import text

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.permissions import DEFAULT_PERMISSIONS

    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS roles (
            key            VARCHAR(50)  PRIMARY KEY,
            name           VARCHAR(100) NOT NULL,
            description    VARCHAR(500),
            permissions    JSONB        NOT NULL DEFAULT '{}'::jsonb,
            all_outlets    BOOLEAN      NOT NULL DEFAULT FALSE,
            approval_tier  VARCHAR(20)  NOT NULL DEFAULT 'staff'
                           CHECK (approval_tier IN ('staff', 'manager', 'admin')),
            is_system      BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS role_outlets (
            role_key   VARCHAR(50) NOT NULL REFERENCES roles(key)   ON DELETE CASCADE ON UPDATE CASCADE,
            outlet_id  UUID        NOT NULL REFERENCES outlets(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (role_key, outlet_id)
        )
    """))

    seed = [
        ("staff",   "Staff",   "Day-to-day operations",            False, "staff"),
        ("manager", "Manager", "Runs an outlet, approves requests", False, "manager"),
        ("admin",   "Admin",   "Full system access",               True,  "admin"),
    ]
    for key, name, desc, all_outlets, tier in seed:
        conn.execute(
            text("""
                INSERT INTO roles (key, name, description, permissions, all_outlets, approval_tier, is_system)
                VALUES (:key, :name, :desc, CAST(:perms AS JSONB), :all_outlets, :tier, TRUE)
                ON CONFLICT (key) DO NOTHING
            """),
            {"key": key, "name": name, "desc": desc, "perms": json.dumps(DEFAULT_PERMISSIONS[key]),
             "all_outlets": all_outlets, "tier": tier},
        )

    # Any stray role value would block the FK — fold it into staff (least access).
    conn.execute(text("UPDATE users SET role = 'staff' WHERE role NOT IN (SELECT key FROM roles)"))
    conn.execute(text("""
        ALTER TABLE users
        ADD CONSTRAINT fk_users_role FOREIGN KEY (role) REFERENCES roles(key) ON UPDATE CASCADE
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_role"))
    # Custom roles have no pre-031 meaning; demote their users to staff.
    conn.execute(text("UPDATE users SET role = 'staff' WHERE role NOT IN ('staff', 'manager', 'admin')"))
    conn.execute(text("DROP TABLE IF EXISTS role_outlets"))
    conn.execute(text("DROP TABLE IF EXISTS roles"))
