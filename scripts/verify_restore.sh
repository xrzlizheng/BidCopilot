#!/usr/bin/env bash
set -euo pipefail
export MSYS_NO_PATHCONV=1

CONTAINER="${PG_CONTAINER:-bidcopilot-app}"
TMP_DB="bidcopilot_restore_check_$(date +%s)"
DUMP_PATH="/tmp/bidcopilot-restore-check.dump"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "postgres container not found: $CONTAINER" >&2
  exit 1
fi

PG_USER="${PG_USER:-$(docker exec "$CONTAINER" printenv POSTGRES_USER)}"
PG_DATABASE="${PG_DATABASE:-$(docker exec "$CONTAINER" printenv POSTGRES_DB)}"
for value in "$PG_USER" "$PG_DATABASE" "$TMP_DB"; do
  [[ "$value" =~ ^[A-Za-z0-9_]+$ ]] || { echo "invalid identifier: $value" >&2; exit 1; }
done

cleanup() {
  docker exec "$CONTAINER" sh -c "dropdb -U $PG_USER --if-exists $TMP_DB; rm -f $DUMP_PATH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

snapshot() {
  docker exec "$CONTAINER" psql -U "$PG_USER" -d "$1" -tAc "
    select 'tables=' || count(*) from information_schema.tables
      where table_schema = 'public' and table_name <> 'alembic_version';
    select 'migration=' || version_num from alembic_version;
    select 'users=' || count(*) from users;
    select 'projects=' || count(*) from bid_projects;
  "
}

echo "==> dump $PG_DATABASE"
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DATABASE" -Fc -f "$DUMP_PATH"

echo "==> restore into $TMP_DB"
docker exec "$CONTAINER" createdb -U "$PG_USER" "$TMP_DB"
docker exec "$CONTAINER" pg_restore -U "$PG_USER" --no-owner --no-privileges --exit-on-error \
  -d "$TMP_DB" "$DUMP_PATH"

SOURCE_SNAPSHOT="$(snapshot "$PG_DATABASE")"
RESTORED_SNAPSHOT="$(snapshot "$TMP_DB")"
echo "==> source"
echo "$SOURCE_SNAPSHOT"
echo "==> restored"
echo "$RESTORED_SNAPSHOT"

if [[ "$SOURCE_SNAPSHOT" != "$RESTORED_SNAPSHOT" ]]; then
  echo "restore verification failed: snapshots differ" >&2
  exit 1
fi

SIZE="$(docker exec "$CONTAINER" stat -c %s "$DUMP_PATH")"
echo "restore verification passed: dump ${SIZE} bytes, temporary database removed"
