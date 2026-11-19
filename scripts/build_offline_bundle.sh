#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/deploy/allin1/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env.example}"
OUT="${OUT:-$ROOT/dist/bidcopilot-offline-$VERSION}"

if [[ -e "$OUT" ]]; then
  echo "output exists, set OUT to a new directory: $OUT" >&2
  exit 1
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

echo "==> build images"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build

echo "==> save images"
mapfile -t images < <(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" config --images | sort -u)
images+=("alpine:3.20")
docker save "${images[@]}" -o "$OUT/images.tar"

echo "==> copy deployment assets"
mkdir -p "$OUT/deploy"
# 白名单拷贝：只收部署 manifest 与示例配置，避免 .env / .env.bak.* 等真实密钥进入客户包。
for asset in allin1 external helm offline; do
  tar -C "$ROOT/deploy" -cf - "$asset" | tar -C "$OUT/deploy" -xf -
done
cp "$ROOT/deploy/.env.example" "$OUT/deploy/"
tar -C "$ROOT" --exclude='*/__pycache__/*' -cf - scripts | tar -C "$OUT" -xf -
cp "$ROOT/VERSION" "$ROOT/README.md" "$OUT/"

echo "==> scan bundle for credentials"
python "$OUT/scripts/check_secrets.py" --scan-dir "$OUT"

echo "==> collect licenses"
(
  cd "$ROOT/backend"
  uv run --no-sync pip-licenses --format=plain > "$OUT/licenses-python.txt"
)
(
  cd "$ROOT/frontend"
  pnpm licenses list > "$OUT/licenses-node.txt" || true
)

echo "==> checksums"
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
tar -czf "$OUT.tar.gz" -C "$OUT" .
echo "offline bundle: $OUT.tar.gz"
