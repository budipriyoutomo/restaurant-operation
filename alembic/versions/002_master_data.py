"""Master Data — outlets, categories, PICs

Adds outlet_status and category_type enum types, then creates the
outlets, categories, pics, and pic_categories tables.

Revision ID: 002
Revises: 001
Create Date: 2026-06-20
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum types
    op.execute("CREATE TYPE outlet_status AS ENUM ('operational', 'warning', 'critical')")
    op.execute("CREATE TYPE category_type AS ENUM ('operations', 'maintenance')")

    # outlets
    op.execute("""
        CREATE TABLE outlets (
            id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            name   VARCHAR(200) NOT NULL,
            code   VARCHAR(10)  NOT NULL UNIQUE,
            status outlet_status NOT NULL DEFAULT 'operational'
        )
    """)
    op.execute("CREATE INDEX idx_outlets_status ON outlets(status)")

    # categories
    op.execute("""
        CREATE TABLE categories (
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(200) NOT NULL,
            description TEXT         DEFAULT '',
            type        category_type NOT NULL DEFAULT 'operations'
        )
    """)

    # pics
    op.execute("""
        CREATE TABLE pics (
            id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name       VARCHAR(200) NOT NULL,
            email      VARCHAR(200) NOT NULL UNIQUE,
            phone      VARCHAR(50)  NOT NULL,
            department VARCHAR(100) NOT NULL
        )
    """)

    # pic_categories junction
    op.execute("""
        CREATE TABLE pic_categories (
            pic_id      UUID NOT NULL REFERENCES pics(id)       ON DELETE CASCADE,
            category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            PRIMARY KEY (pic_id, category_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pic_categories CASCADE")
    op.execute("DROP TABLE IF EXISTS pics CASCADE")
    op.execute("DROP TABLE IF EXISTS categories CASCADE")
    op.execute("DROP TABLE IF EXISTS outlets CASCADE")
    op.execute("DROP TYPE IF EXISTS category_type")
    op.execute("DROP TYPE IF EXISTS outlet_status")
