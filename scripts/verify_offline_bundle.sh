#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="${1:-}"
if [[ -z "$BUNDLE_DIR" || ! -d "$BUNDLE_DIR" ]]; then
  echo "usage: $0 <bundle-dir>" >&2
  exit 2
fi

required=(
  "images.tar"
  "deploy/allin1/docker-compose.yml"
  "deploy/helm/bidcopilot/Chart.yaml"
  "scripts/backup.sh"
  "scripts/restore.sh"
  "SHA256SUMS"
)
for file in "${required[@]}"; do
  if [[ ! -f "$BUNDLE_DIR/$file" ]]; then
    echo "missing: $file" >&2
    exit 1
  fi
done

(
  cd "$BUNDLE_DIR"
  sha256sum -c SHA256SUMS
)
echo "offline bundle verified: $BUNDLE_DIR"
