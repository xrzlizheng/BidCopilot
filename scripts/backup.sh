#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${CONTAINER:-bidcopilot-app}"
PG_USER="${PG_USER:-bidcopilot}"
PG_DB="${PG_DB:-bidcopilot}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups/$(date +%Y%m%d-%H%M%S)}"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "container not found: $CONTAINER" >&2
  exit 1
fi
mkdir -p "$BACKUP_DIR"
BACKUP_DIR_ABS="$(cd "$BACKUP_DIR" && pwd -W 2>/dev/null)" || BACKUP_DIR_ABS="$(cd "$BACKUP_DIR" && pwd)"

echo "==> backup postgres"
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$BACKUP_DIR_ABS/postgres.dump"

echo "==> backup minio"
docker exec "$CONTAINER" sh -c 'tar czf - -C /data/minio .' > "$BACKUP_DIR_ABS/minio.tgz"

{
  echo "version=$(cat "$ROOT/VERSION")"
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "container=$CONTAINER"
} > "$BACKUP_DIR_ABS/metadata.txt"

echo "==> checksums"
(
  cd "$BACKUP_DIR_ABS"
  sha256sum postgres.dump minio.tgz metadata.txt > SHA256SUMS
)
echo "backup complete: $BACKUP_DIR_ABS"
