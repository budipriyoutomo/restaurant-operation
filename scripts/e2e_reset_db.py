#!/usr/bin/env python3
"""Rebuild the end-to-end test database from scratch (Todo-Pilot §12).

Playwright (frontend/playwright.config.ts) runs this before starting the
backend, so every E2E run starts from: empty DB → `alembic upgrade head`
(the real migrations, unlike the pytest suite's create_all) → scripts.seed →
a platform admin for the platform E2E.

Refuses to touch any database whose name does not end in `_e2e`, so it can
never wipe the dev or production database by accident.

    DATABASE_URL=postgresql+psycopg://postgres:@localhost:5432/restaurantops_e2e \\
        python -m scripts.e2e_reset_db
"""

import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Operator account for the platform E2E (Todo-Pilot §11) — exists only in *_e2e databases.
E2E_PLATFORM_ADMIN = ("platform@e2e.test", "platform-e2e-pass")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    url = make_url(raw)
    if not (url.database or "").endswith("_e2e"):
        print(f"refusing to reset {url.database!r}: E2E database names must end in _e2e", file=sys.stderr)
        return 2

    # Create the database on first use.
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    # Wipe everything, including enum types and alembic_version.
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    env = {**os.environ, "DATABASE_URL": raw}
    platform_admin = [sys.executable, "-m", "scripts.create_platform_admin", E2E_PLATFORM_ADMIN[0], "E2E Platform",
                      "--password", E2E_PLATFORM_ADMIN[1]]
    for cmd in ([sys.executable, "-m", "alembic", "upgrade", "head"], [sys.executable, "-m", "scripts.seed"],
                platform_admin):
        result = subprocess.run(cmd, cwd=BACKEND_DIR, env=env)
        if result.returncode != 0:
            return result.returncode
    print(f"E2E database {url.database} ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
