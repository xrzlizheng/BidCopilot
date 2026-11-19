#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${CONTAINER:-bidcopilot-app}"
PG_USER="${PG_USER:-bidcopilot}"
PG_DB="${PG_DB:-bidcopilot}"
BACKUP_DIR=""
CONFIRM="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir) BACKUP_DIR="${2:-}"; shift 2 ;;
    --yes) CONFIRM="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BACKUP_DIR" || ! -f "$BACKUP_DIR/postgres.dump" || ! -f "$BACKUP_DIR/minio.tgz" ]]; then
  echo "usage: $0 --backup-dir <dir> [--yes]" >&2
  exit 2
fi
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "container not found: $CONTAINER" >&2
  exit 1
fi
BACKUP_DIR_ABS="$(cd "$BACKUP_DIR" && pwd -W 2>/dev/null)" || BACKUP_DIR_ABS="$(cd "$BACKUP_DIR" && pwd)"

if [[ "$CONFIRM" != "true" ]]; then
  echo "WARNING: restore will overwrite Postgres and MinIO data inside $CONTAINER."
  read -r -p "Type RESTORE to continue: " answer
  [[ "$answer" == "RESTORE" ]] || { echo "aborted"; exit 1; }
fi

echo "==> restore postgres"
docker exec -i "$CONTAINER" pg_restore -U "$PG_USER" -d "$PG_DB" \
  --clean --if-exists --no-owner --no-privileges < "$BACKUP_DIR_ABS/postgres.dump"

echo "==> restore minio"
docker exec -i "$CONTAINER" sh -c 'rm -rf /data/minio/* && tar xzf - -C /data/minio' \
  < "$BACKUP_DIR_ABS/minio.tgz"

echo "restore complete. restart the container and verify /healthz:"
echo "  docker restart $CONTAINER"
