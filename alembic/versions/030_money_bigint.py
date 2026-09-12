"""Money columns INTEGER → BIGINT

Revision ID: 030
Revises: 029
Create Date: 2026-09-12

Every money column was INTEGER (max 2,147,483,647) while the currency is IDR.
Rp 2.147.483.647 is an ordinary figure here — a kitchen renovation, a POS fleet
purchase — so any such amount made Postgres raise `integer out of range`.
That surfaced in the browser as a CORS error, because the resulting 500 is
produced above CORSMiddleware and therefore carries no CORS headers.

Meter readings and PM intervals are deliberately left as INTEGER: they are
counts, not money.
"""

from alembic import op
from sqlalchemy import text

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


# table, column
MONEY_COLUMNS = [
    ("approval_policies",      "min_amount"),
    ("approval_policies",      "max_amount"),
    ("approval_requests",      "amount"),
    ("assets",                 "purchase_cost"),
    ("budgets",                "amount"),
    ("parts",                  "unit_cost"),
    ("purchase_order_items",   "unit_cost"),
    ("purchase_order_items",   "line_total"),
    ("purchase_orders",        "total"),
    ("purchase_request_items", "est_unit_cost"),
    ("purchase_request_items", "line_total"),
    ("purchase_requests",      "total_est"),
    ("work_order_parts",       "unit_cost"),
    ("work_order_parts",       "line_cost"),
    ("work_orders",            "estimated_cost"),
    ("work_orders",            "labor_cost"),
    ("work_orders",            "parts_cost"),
]


def _alter(conn, table: str, column: str, to_type: str) -> None:
    # to_regclass returns NULL for a table that does not exist, so a database
    # that predates one of these tables migrates without failing.
    exists = conn.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
    ).scalar()
    if not exists:
        return
    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {to_type}"))


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in MONEY_COLUMNS:
        _alter(conn, table, column, "BIGINT")


def downgrade() -> None:
    # Narrowing back can overflow on rows that only BIGINT could hold; those
    # values are clamped rather than failing the migration outright.
    conn = op.get_bind()
    for table, column in MONEY_COLUMNS:
        exists = conn.execute(
            text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
        ).scalar()
        if not exists:
            continue
        conn.execute(text(f"""
            UPDATE {table} SET {column} = 2147483647
            WHERE {column} > 2147483647
        """))
        conn.execute(text(f"""
            UPDATE {table} SET {column} = -2147483648
            WHERE {column} < -2147483648
        """))
        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE INTEGER"))
