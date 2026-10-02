#!/bin/sh
# RestaurantOps — restore a backup made by scripts/backup_db.sh.
#
#   scripts/restore_db.sh /backups/daily/restaurantops-2026-10-02_030000.dump --yes
#
# Connection uses the standard libpq variables (PGHOST, PGPORT, PGUSER,
# PGPASSWORD, PGDATABASE). The target database must already exist.
#
# DESTRUCTIVE: every object in the dump is dropped and recreated in the target
# database (pg_restore --clean). Stop the api service first so no writes land
# mid-restore. The --yes flag is required to proceed.

set -eu

if [ $# -lt 1 ]; then
    echo "usage: $0 <file.dump> --yes" >&2
    exit 2
fi

DUMP="$1"
CONFIRM="${2:-}"
DB_NAME="${PGDATABASE:-restaurantops}"

if [ ! -f "$DUMP" ]; then
    echo "restore: file not found: $DUMP" >&2
    exit 2
fi

if [ "$CONFIRM" != "--yes" ]; then
    echo "restore: this overwrites database '$DB_NAME' on '${PGHOST:-localhost}'." >&2
    echo "restore: re-run with --yes to proceed." >&2
    exit 2
fi

echo "[restore] $(date -Iseconds) restoring $DUMP into $DB_NAME"
pg_restore --clean --if-exists --no-owner --exit-on-error --single-transaction \
    --dbname="$DB_NAME" "$DUMP"
echo "[restore] done"
