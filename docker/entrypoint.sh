#!/bin/sh
# Container entrypoint: wait for Postgres, run migrations, then hand off to CMD.
set -e

echo "[entrypoint] waiting for database..."
python <<'PY'
import os, time, sys
import psycopg

url = os.environ["DATABASE_URL"].replace("+psycopg", "")
for attempt in range(1, 61):
    try:
        with psycopg.connect(url, connect_timeout=3):
            print("[entrypoint] database is up")
            break
    except Exception as exc:
        print(f"[entrypoint] db not ready ({attempt}/60): {exc}")
        time.sleep(2)
else:
    print("[entrypoint] database never became reachable", file=sys.stderr)
    sys.exit(1)
PY

echo "[entrypoint] running alembic migrations..."
alembic upgrade head

# One-time bootstrap of master data + first admin user. Safe to leave enabled:
# seed.py is written to be idempotent. Set RUN_SEED=0 after the first boot.
if [ "${RUN_SEED:-0}" = "1" ]; then
  echo "[entrypoint] seeding initial data..."
  python -m scripts.seed || echo "[entrypoint] seed skipped/failed (continuing)"
fi

echo "[entrypoint] starting: $*"
exec "$@"
