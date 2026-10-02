"""Guest Service: guest case per Guest Service issue (Todo-Pilot §8)

Revision ID: 037
Revises: 036
Create Date: 2026-10-03

One guest_cases row per Issue in category "Guest Service": guest name/contact,
channel, reported/first-response/resolved timestamps (response KPIs) and the
recovery given (compensation type + integer IDR value + currency).

Existing Guest Service issues are backfilled with channel 'other' and
reported_at = the issue's creation time. guest-service becomes manageable
(manage = record compensation); the built-in manager role gets it.
"""

from alembic import op
from sqlalchemy import text

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE guest_cases (
            id                  UUID PRIMARY KEY,
            issue_id            UUID NOT NULL UNIQUE REFERENCES issues(id) ON DELETE CASCADE,
            outlet              VARCHAR(200) NOT NULL DEFAULT '',
            outlet_id           UUID NULL REFERENCES outlets(id) ON DELETE RESTRICT,
            guest_name          VARCHAR(200) NOT NULL DEFAULT '',
            guest_contact       VARCHAR(200) NOT NULL DEFAULT '',
            channel             VARCHAR(20) NOT NULL DEFAULT 'other'
                CHECK (channel IN ('walk-in', 'phone', 'google-review', 'instagram', 'whatsapp', 'other')),
            reported_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            first_response_at   TIMESTAMPTZ NULL,
            first_response_by   VARCHAR(200) NOT NULL DEFAULT '',
            first_response_note TEXT NOT NULL DEFAULT '',
            resolved_at         TIMESTAMPTZ NULL,
            compensation_type   VARCHAR(20) NOT NULL DEFAULT 'none'
                CHECK (compensation_type IN ('none', 'discount', 'free-item', 'voucher', 'refund', 'other')),
            compensation_value  BIGINT NOT NULL DEFAULT 0 CHECK (compensation_value >= 0),
            currency            VARCHAR(3) NOT NULL DEFAULT 'IDR',
            compensation_note   TEXT NOT NULL DEFAULT '',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX ix_guest_cases_outlet_reported ON guest_cases (outlet_id, reported_at)"))
    conn.execute(text("""
        INSERT INTO guest_cases (id, issue_id, outlet, outlet_id, reported_at, resolved_at)
        SELECT gen_random_uuid(), i.id, i.outlet, i.outlet_id, i.created_at,
               CASE WHEN i.status IN ('resolved', 'closed') THEN i.updated_at END
        FROM issues i
        WHERE i.category = 'Guest Service'
          AND NOT EXISTS (SELECT 1 FROM guest_cases g WHERE g.issue_id = i.id)
    """))
    conn.execute(text("""
        UPDATE roles SET permissions = permissions || '{"guest-service": "manage"}'::jsonb
        WHERE key = 'manager'
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS guest_cases"))
    conn.execute(text("""
        UPDATE roles SET permissions = permissions || '{"guest-service": "view"}'::jsonb
        WHERE key = 'manager' AND permissions->>'guest-service' = 'manage'
    """))
