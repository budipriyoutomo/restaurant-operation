"""Campaign budget as integer + currency; spend and results (Todo-Pilot §10)

Revision ID: 039
Revises: 038
Create Date: 2026-10-03

campaigns.budget was VARCHAR free text. It becomes BIGINT (major units) with a
currency column. Existing text is parsed by
campaign_service.parse_legacy_budget ("Rp 5.000.000", "1,5 juta", "RM 5,000"
→ MYR …); text that cannot be read is kept verbatim in budget_legacy and the
budget left NULL, so nothing is lost.

New, entered by hand: actual_cost, result_transactions, result_revenue and an
optional baseline (transactions/revenue of a comparable period before).
"""

from alembic import op
from sqlalchemy import text

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None

_MONEY = ("actual_cost", "result_revenue", "baseline_revenue")
_COUNTS = ("result_transactions", "baseline_transactions")


def upgrade() -> None:
    from app.services.campaign_service import parse_legacy_budget

    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE campaigns
            ADD COLUMN budget_amount BIGINT NULL CHECK (budget_amount >= 0),
            ADD COLUMN currency      VARCHAR(3) NOT NULL DEFAULT 'IDR',
            ADD COLUMN budget_legacy TEXT NULL
    """))
    for col in _MONEY:
        conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col} BIGINT NULL CHECK ({col} >= 0)"))
    for col in _COUNTS:
        conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col} INTEGER NULL CHECK ({col} >= 0)"))

    rows = conn.execute(text("SELECT id, budget FROM campaigns WHERE budget IS NOT NULL AND budget <> ''")).all()
    for cid, raw in rows:
        parsed = parse_legacy_budget(raw)
        if parsed is None:
            conn.execute(text("UPDATE campaigns SET budget_legacy = :raw WHERE id = :id"), {"raw": raw, "id": cid})
        else:
            amount, currency = parsed
            conn.execute(text("UPDATE campaigns SET budget_amount = :a, currency = :c WHERE id = :id"),
                         {"a": amount, "c": currency, "id": cid})

    conn.execute(text("ALTER TABLE campaigns DROP COLUMN budget"))
    conn.execute(text("ALTER TABLE campaigns RENAME COLUMN budget_amount TO budget"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE campaigns RENAME COLUMN budget TO budget_amount"))
    conn.execute(text("ALTER TABLE campaigns ADD COLUMN budget VARCHAR(100) NULL"))
    conn.execute(text("""
        UPDATE campaigns SET budget = COALESCE(budget_legacy,
            CASE WHEN budget_amount IS NULL THEN NULL ELSE currency || ' ' || budget_amount::text END)
    """))
    for col in ("budget_amount", "currency", "budget_legacy", *_MONEY, *_COUNTS):
        conn.execute(text(f"ALTER TABLE campaigns DROP COLUMN {col}"))
