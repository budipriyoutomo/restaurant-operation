"""CMMS — Assets and Work Orders

Revision ID: 006
Revises: 005
Create Date: 2026-06-28
"""

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # PostgreSQL enum types — values identical to frontend TypeScript types
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TYPE asset_status AS ENUM (
            'operational', 'warning', 'maintenance', 'critical'
        )
    """)
    op.execute("""
        CREATE TYPE work_order_type AS ENUM ('corrective', 'preventive')
    """)
    op.execute("""
        CREATE TYPE work_order_status AS ENUM (
            'scheduled', 'in-progress', 'completed', 'cancelled'
        )
    """)

    # ------------------------------------------------------------------
    # Sequence tables — atomic per-year counters for AST-/WO- numbers
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE asset_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE work_order_number_sequences (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ------------------------------------------------------------------
    # assets
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE assets (
            id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            number        VARCHAR(30)  NOT NULL UNIQUE,
            name          VARCHAR(300) NOT NULL,
            category      VARCHAR(100) NOT NULL,
            outlet        VARCHAR(200) NOT NULL,
            status        asset_status NOT NULL DEFAULT 'operational',
            serial_number VARCHAR(100),
            brand         VARCHAR(100),
            model         VARCHAR(100),
            install_date  DATE,
            last_pm       DATE,
            next_pm       DATE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_assets_outlet ON assets(outlet)")
    op.execute("CREATE INDEX idx_assets_status ON assets(status)")
    op.execute("CREATE INDEX idx_assets_next_pm ON assets(next_pm)")

    # ------------------------------------------------------------------
    # work_orders
    # asset_id / issue_id nullable with SET NULL so work orders survive
    # deletion of the parent asset or issue
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE work_orders (
            id             UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
            number         VARCHAR(30)       NOT NULL UNIQUE,
            type           work_order_type   NOT NULL DEFAULT 'corrective',
            asset_id       UUID              REFERENCES assets(id) ON DELETE SET NULL,
            asset_name     VARCHAR(300)      NOT NULL,
            outlet         VARCHAR(200)      NOT NULL,
            issue_id       UUID              REFERENCES issues(id) ON DELETE SET NULL,
            issue_number   VARCHAR(30),
            title          VARCHAR(500)      NOT NULL,
            description    TEXT              NOT NULL DEFAULT '',
            priority       priority          NOT NULL DEFAULT 'medium',
            status         work_order_status NOT NULL DEFAULT 'scheduled',
            assignee       VARCHAR(200)      NOT NULL DEFAULT 'Unassigned',
            scheduled_date DATE,
            completed_date DATE,
            created_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_work_orders_asset_id  ON work_orders(asset_id)")
    op.execute("CREATE INDEX idx_work_orders_issue_id  ON work_orders(issue_id)")
    op.execute("CREATE INDEX idx_work_orders_outlet    ON work_orders(outlet)")
    op.execute("CREATE INDEX idx_work_orders_status    ON work_orders(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS work_orders")
    op.execute("DROP TABLE IF EXISTS assets")
    op.execute("DROP TABLE IF EXISTS work_order_number_sequences")
    op.execute("DROP TABLE IF EXISTS asset_number_sequences")
    op.execute("DROP TYPE IF EXISTS work_order_status")
    op.execute("DROP TYPE IF EXISTS work_order_type")
    op.execute("DROP TYPE IF EXISTS asset_status")
