#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT="${WEB_PORT:-18080}"
SINGLE_COMPOSE="${SINGLE_COMPOSE:-$ROOT/deploy/allin1/docker-compose.yml}"
COMPOSE=(docker compose -f "$SINGLE_COMPOSE")

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

# 本地与生产使用同一个单容器（应用 + PostgreSQL + Redis + MinIO + nginx），
# 容器名、镜像与启动方式完全一致，不再在宿主机另起 uvicorn / pnpm dev。
# 本地冒烟显式开启演示种子；生产形态默认不灌入（见 deploy/.env.example）。
# 不内置默认演示口令（与 deploy/allin1/entrypoint.sh 口径一致）：开启种子时必须
# 显式提供 BIDCOPILOT_DEMO_PASSWORD，否则直接报错退出，避免公开仓库留下已知口令账号。
BIDCOPILOT_SEED_DEMO="${BIDCOPILOT_SEED_DEMO:-1}"
if [ "$BIDCOPILOT_SEED_DEMO" = "1" ] && [ -z "${BIDCOPILOT_DEMO_PASSWORD:-}" ]; then
  echo "本地冒烟开启演示种子必须显式提供 BIDCOPILOT_DEMO_PASSWORD（不再内置默认口令）：" >&2
  echo "  BIDCOPILOT_DEMO_PASSWORD='<your-demo-password>' bash scripts/run_local_site.sh" >&2
  echo "不需要演示账号时：BIDCOPILOT_SEED_DEMO=0 bash scripts/run_local_site.sh" >&2
  exit 1
fi
BIDCOPILOT_SEED_DEMO="$BIDCOPILOT_SEED_DEMO" \
BIDCOPILOT_DEMO_PASSWORD="${BIDCOPILOT_DEMO_PASSWORD:-}" \
  "${COMPOSE[@]}" up -d

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/healthz" >/dev/null 2>&1; then
    echo "BidCopilot: http://127.0.0.1:$WEB_PORT/login"
    echo "API:       http://127.0.0.1:$WEB_PORT/api/v1"
    echo "日志:      docker compose -f deploy/allin1/docker-compose.yml logs -f"
    echo "停止:      docker compose -f deploy/allin1/docker-compose.yml down"
    exit 0
  fi
  sleep 1
done

echo "container did not become healthy in 120s; check logs:" >&2
echo "  docker compose -f deploy/allin1/docker-compose.yml logs --tail=80" >&2
exit 1
