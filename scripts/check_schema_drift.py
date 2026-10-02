#!/usr/bin/env python3
"""Compare a *migrated* database with the SQLAlchemy models.

The test suite builds its schema from the models (create_all), so a model that
disagrees with the migrations passes every test and fails in production. This
was how training_programs.status and campaigns.type/status (native Postgres
enums declared as String) broke every INSERT.

Checks: tables/columns the models use but the DB lacks, and DB enum columns the
models do not declare as Enum (or declare with different values).

Run from backend/ against a migrated DB (DATABASE_URL):
    python -m scripts.check_schema_drift
Exit code 1 when drift is found — suitable for CI after `alembic upgrade head`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import Enum, text

import app.main  # noqa: F401 — registers every model
from app.database import Base, SessionLocal


def main() -> int:
    db = SessionLocal()
    problems = []
    try:
        cols = db.execute(text("""
            SELECT c.table_name, c.column_name, c.data_type, c.udt_name,
                   (SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
                    FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid WHERE t.typname = c.udt_name)
            FROM information_schema.columns c WHERE c.table_schema = 'public'
        """)).all()
        db_cols = {(t, c): (dt, udt, labels) for t, c, dt, udt, labels in cols}
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                info = db_cols.get((table.name, col.name))
                if info is None:
                    problems.append(f"missing in DB: {table.name}.{col.name}")
                    continue
                data_type, udt, labels = info
                if data_type == "USER-DEFINED" and labels:
                    if not isinstance(col.type, Enum):
                        problems.append(f"{table.name}.{col.name}: DB enum {udt} but model {type(col.type).__name__}")
                    # Order is irrelevant (ALTER TYPE … ADD VALUE appends); values must match.
                    elif set(col.type.enums) != set(labels):
                        problems.append(f"{table.name}.{col.name}: enum values differ — DB {labels} vs model {col.type.enums}")
    finally:
        db.close()
    for p in problems:
        print("DRIFT", p)
    print(f"schema drift check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
