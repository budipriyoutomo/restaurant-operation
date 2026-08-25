"""Procurement — PR → PO → Goods Receipt (Tier 6.1)

Revision ID: 028
Revises: 027
Create Date: 2026-07-06

Closes the maintenance→purchasing loop: a part hitting its reorder level raises a
Purchase Request, which is approved through the EXISTING approval engine (type
`procurement`), turned into a Purchase Order to a vendor, and received — the
receipt topping the stock back up.

Approval reuse means ApprovalRequest becomes polymorphic: it attaches to an
Issue **or** a PurchaseRequest. `issue_id` therefore becomes nullable and a
`purchase_request_id` is added. The one-approval-per-issue rule (arch decision
#1) is preserved: the unique on issue_id still holds for non-null values, and a
matching unique guards purchase_request_id.

Money is INTEGER rupiah throughout (migration 014 convention).
"""

from alembic import op
from sqlalchemy import text

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Number sequences (reuse the per-year pattern used elsewhere)
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS procurement_number_sequences (
            prefix VARCHAR(8) NOT NULL,
            year   INTEGER     NOT NULL,
            last_seq INTEGER   NOT NULL DEFAULT 0,
            PRIMARY KEY (prefix, year)
        )
    """))

    # ------------------------------------------------------------------
    # Purchase Requests
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS purchase_requests (
            id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            number       VARCHAR(30)   NOT NULL UNIQUE,     -- PR-2026-00001
            status       VARCHAR(20)   NOT NULL DEFAULT 'pending_approval',
                          -- pending_approval | approved | rejected | ordered | received | cancelled
            outlet       VARCHAR(200),
            outlet_id    UUID          REFERENCES outlets(id) ON DELETE RESTRICT,
            source       VARCHAR(20)   NOT NULL DEFAULT 'manual',   -- manual | auto_reorder
            requested_by VARCHAR(200),
            notes        TEXT,
            total_est    INTEGER       NOT NULL DEFAULT 0,   -- IDR
            created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS purchase_request_items (
            id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
            purchase_request_id UUID    NOT NULL REFERENCES purchase_requests(id) ON DELETE CASCADE,
            part_id             UUID    REFERENCES parts(id) ON DELETE SET NULL,
            part_name           VARCHAR(300) NOT NULL,
            quantity            INTEGER NOT NULL,
            est_unit_cost       INTEGER NOT NULL DEFAULT 0,
            line_total          INTEGER NOT NULL DEFAULT 0
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pr_items_pr ON purchase_request_items(purchase_request_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pr_items_part ON purchase_request_items(part_id)"
    ))
    # "One open auto-reorder PR per part" is enforced in procurement_service
    # (query for an existing open auto PR), not by a DB constraint — a part may
    # legitimately appear across many historical PRs.

    # ------------------------------------------------------------------
    # Purchase Orders
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            number              VARCHAR(30)   NOT NULL UNIQUE,   -- PO-2026-00001
            purchase_request_id UUID          REFERENCES purchase_requests(id) ON DELETE SET NULL,
            vendor_id           UUID          REFERENCES vendors(id) ON DELETE RESTRICT,
            vendor_name         VARCHAR(200),
            status              VARCHAR(20)   NOT NULL DEFAULT 'sent',
                                 -- sent | partially_received | received | cancelled
            outlet              VARCHAR(200),
            outlet_id           UUID          REFERENCES outlets(id) ON DELETE RESTRICT,
            total               INTEGER       NOT NULL DEFAULT 0,
            created_by          VARCHAR(200),
            created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id                UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
            purchase_order_id UUID    NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
            part_id           UUID    REFERENCES parts(id) ON DELETE SET NULL,
            part_name         VARCHAR(300) NOT NULL,
            quantity_ordered  INTEGER NOT NULL,
            quantity_received INTEGER NOT NULL DEFAULT 0,
            unit_cost         INTEGER NOT NULL DEFAULT 0,
            line_total        INTEGER NOT NULL DEFAULT 0
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_po_items_po ON purchase_order_items(purchase_order_id)"
    ))

    # ------------------------------------------------------------------
    # Goods Receipts (a receipt event; items carry per-line quantities so
    # partial receipts and discrepancies are recordable)
    # ------------------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS goods_receipts (
            id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            number            VARCHAR(30)   NOT NULL UNIQUE,   -- GRN-2026-00001
            purchase_order_id UUID          NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
            received_by       VARCHAR(200),
            notes             TEXT,
            received_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS goods_receipt_items (
            id                    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
            goods_receipt_id      UUID    NOT NULL REFERENCES goods_receipts(id) ON DELETE CASCADE,
            purchase_order_item_id UUID   NOT NULL REFERENCES purchase_order_items(id) ON DELETE CASCADE,
            part_id               UUID    REFERENCES parts(id) ON DELETE SET NULL,
            quantity_received     INTEGER NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_grn_items_grn ON goods_receipt_items(goods_receipt_id)"
    ))

    # ------------------------------------------------------------------
    # Make ApprovalRequest polymorphic (Issue OR PurchaseRequest)
    # ------------------------------------------------------------------
    conn.execute(text("ALTER TABLE approval_requests ALTER COLUMN issue_id DROP NOT NULL"))
    conn.execute(text("ALTER TABLE approval_requests ALTER COLUMN issue_number DROP NOT NULL"))
    conn.execute(text("""
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS purchase_request_id UUID
                REFERENCES purchase_requests(id) ON DELETE CASCADE
    """))
    conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_purchase_request
        ON approval_requests(purchase_request_id)
        WHERE purchase_request_id IS NOT NULL
    """))
    # Exactly one source must be set.
    conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE approval_requests
                ADD CONSTRAINT ck_approval_one_source
                CHECK ((issue_id IS NOT NULL) <> (purchase_request_id IS NOT NULL));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE approval_requests DROP CONSTRAINT IF EXISTS ck_approval_one_source"))
    conn.execute(text("DROP INDEX IF EXISTS uq_approval_purchase_request"))
    conn.execute(text("ALTER TABLE approval_requests DROP COLUMN IF EXISTS purchase_request_id"))
    # Re-tighten issue columns (only valid if no PR-linked approvals remain).
    conn.execute(text("DELETE FROM approval_requests WHERE issue_id IS NULL"))
    conn.execute(text("ALTER TABLE approval_requests ALTER COLUMN issue_id SET NOT NULL"))
    conn.execute(text("ALTER TABLE approval_requests ALTER COLUMN issue_number SET NOT NULL"))

    for t in ("goods_receipt_items", "goods_receipts",
              "purchase_order_items", "purchase_orders",
              "purchase_request_items", "purchase_requests",
              "procurement_number_sequences"):
        conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
