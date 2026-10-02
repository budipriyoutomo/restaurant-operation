"""Training enrollments + outlet_id backfill for programs (Todo-Pilot §9)

Revision ID: 038
Revises: 037
Create Date: 2026-10-03

- training_enrollments: one row per participant per program; status
  registered | attended | no-show, optional score 0–100. The participant's
  outlet and role name are snapshotted at enrollment for the attendance recap.
- training_programs.outlet_id was never written by the API, so every program
  was visible to every outlet. Fill it from the outlet name where it matches.
"""

from alembic import op
from sqlalchemy import text

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE training_enrollments (
            id          UUID PRIMARY KEY,
            program_id  UUID NOT NULL REFERENCES training_programs(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_name   VARCHAR(200) NOT NULL DEFAULT '',
            role_name   VARCHAR(100) NOT NULL DEFAULT '',
            outlet      VARCHAR(200) NOT NULL DEFAULT '',
            status      VARCHAR(20) NOT NULL DEFAULT 'registered'
                        CHECK (status IN ('registered', 'attended', 'no-show')),
            score       INTEGER NULL CHECK (score BETWEEN 0 AND 100),
            notes       TEXT NOT NULL DEFAULT '',
            enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            marked_at   TIMESTAMPTZ NULL,
            CONSTRAINT uq_training_enrollment UNIQUE (program_id, user_id)
        )
    """))
    conn.execute(text("CREATE INDEX ix_training_enrollments_program ON training_enrollments (program_id)"))
    conn.execute(text("""
        UPDATE training_programs tp SET outlet_id = o.id
        FROM outlets o
        WHERE tp.outlet_id IS NULL AND tp.outlet = o.name AND o.deleted_at IS NULL
    """))


def downgrade() -> None:
    op.get_bind().execute(text("DROP TABLE IF EXISTS training_enrollments"))
