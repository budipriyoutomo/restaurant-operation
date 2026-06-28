"""Training programs table

Revision ID: 010
Revises: 009
Create Date: 2026-06-28
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE training_program_status AS ENUM (
            'scheduled', 'ongoing', 'completed', 'cancelled'
        )
    """)
    op.execute("""
        CREATE TABLE training_programs (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            title            VARCHAR(300) NOT NULL,
            description      TEXT,
            target_role      VARCHAR(100) NOT NULL DEFAULT 'staff',
            outlet           VARCHAR(200),
            trainer          VARCHAR(200),
            scheduled_date   DATE,
            duration_hours   NUMERIC(5, 1),
            status           training_program_status NOT NULL DEFAULT 'scheduled',
            max_participants INTEGER,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_training_programs_status ON training_programs(status)")
    op.execute("CREATE INDEX idx_training_programs_outlet ON training_programs(outlet)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_training_programs_outlet")
    op.execute("DROP INDEX IF EXISTS idx_training_programs_status")
    op.execute("DROP TABLE IF EXISTS training_programs")
    op.execute("DROP TYPE IF EXISTS training_program_status")
