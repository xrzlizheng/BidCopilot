#!/bin/bash
set -euo pipefail

: "${DATABASE_URL:=postgresql+asyncpg://bidcopilot:bidcopilot@127.0.0.1:5432/bidcopilot}"
: "${MINIO_ROOT_USER:=bidcopilot}"
# 不使用弱默认口令：未显式提供时生成随机口令并持久化，重启后客户端仍可连接。
if [ -z "${MINIO_ROOT_PASSWORD:-}" ]; then
  mkdir -p /data
  if [ -f /data/minio-root.key ]; then
    MINIO_ROOT_PASSWORD="$(cat /data/minio-root.key)"
  else
    MINIO_ROOT_PASSWORD="$(head -c 24 /dev/urandom | base64 -w 0)"
    printf '%s' "$MINIO_ROOT_PASSWORD" > /data/minio-root.key
    chmod 600 /data/minio-root.key
  fi
fi
: "${MINIO_ACCESS_KEY:=$MINIO_ROOT_USER}"
: "${MINIO_SECRET_KEY:=$MINIO_ROOT_PASSWORD}"
: "${BIDCOPILOT_DEMO_PASSWORD:=}"

# 外置数据层开关（ADR-0001）：=1 时 PostgreSQL/Redis/MinIO 由外部提供
# （K8s / VM 分离部署 / SaaS），本容器只运行应用与 nginx；演示种子数据
# 仅在显式 BIDCOPILOT_SEED_DEMO=1 时灌入。
EXTERNAL_SERVICES="${BIDCOPILOT_EXTERNAL_SERVICES:-0}"
# helm/compose 可能传 true/1/yes：统一归一化，避免 "true" != "1" 仍启动内置数据层。
case "$EXTERNAL_SERVICES" in
  1|true|TRUE|yes|on) EXTERNAL_SERVICES=1 ;;
  *) EXTERNAL_SERVICES=0 ;;
esac

# 不使用弱默认密钥：未显式提供时生成随机密钥并持久化到数据卷，重启后会话仍有效。
# 示例/占位值（如 .env.example 的 replace-*）视同未配置，避免整站使用公开密钥。
case "${JWT_SECRET_KEY:-}" in
  ""|replace-*|change-me*|changeme*|placeholder*|example*) JWT_SECRET_KEY="" ;;
esac
if [ -z "${JWT_SECRET_KEY:-}" ]; then
  mkdir -p /data
  if [ -f /data/jwt.key ]; then
    JWT_SECRET_KEY="$(cat /data/jwt.key)"
  else
    JWT_SECRET_KEY="$(head -c 48 /dev/urandom | base64 -w 0)"
    printf '%s' "$JWT_SECRET_KEY" > /data/jwt.key
    chmod 600 /data/jwt.key
  fi
fi

# 加密主密钥（模型 Key / Webhook secret / 通知渠道等 AES-GCM 信封加密）：
# 同样缺省生成并持久化；轮换会使存量 secret_store 记录无法解密，需按运维手册迁移。
if [ -z "${MODEL_SECRET_MASTER_KEY:-}" ]; then
  mkdir -p /data
  if [ -f /data/model-master.key ]; then
    MODEL_SECRET_MASTER_KEY="$(cat /data/model-master.key)"
  else
    MODEL_SECRET_MASTER_KEY="$(head -c 32 /dev/urandom | base64 -w 0)"
    printf '%s' "$MODEL_SECRET_MASTER_KEY" > /data/model-master.key
    chmod 600 /data/model-master.key
  fi
fi
export DATABASE_URL JWT_SECRET_KEY MODEL_SECRET_MASTER_KEY MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY BIDCOPILOT_DEMO_PASSWORD

PGBIN="$(ls -d /usr/lib/postgresql/*/bin | head -n1)"
export PGDATA=/data/pgdata

if [ "$EXTERNAL_SERVICES" != "1" ]; then
  if command -v pg_lsclusters >/dev/null 2>&1; then
    while read -r version cluster _; do
      pg_ctlcluster "$version" "$cluster" stop || true
    done < <(pg_lsclusters -h)
  fi

  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    su postgres -c "$PGBIN/initdb -D $PGDATA -A trust" >/dev/null
  fi

  # 允许经发布到宿主 loopback 的端口连接（本地测试与工具需要），使用 scram 认证。
  if ! grep -q "bidcopilot-published-port" "$PGDATA/pg_hba.conf"; then
    echo 'host all all 0.0.0.0/0 scram-sha-256  # bidcopilot-published-port' >> "$PGDATA/pg_hba.conf"
  fi

  su postgres -c "$PGBIN/pg_ctl -D $PGDATA -l /tmp/postgres.log -o '-c listen_addresses=0.0.0.0 -p 5432' start" >/dev/null
  for _ in $(seq 1 60); do
    if su postgres -c "$PGBIN/pg_isready -h 127.0.0.1 -q"; then break; fi
    sleep 0.5
  done
  su postgres -c "$PGBIN/psql -h 127.0.0.1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='bidcopilot'\" | grep -q 1" \
    || su postgres -c "$PGBIN/psql -h 127.0.0.1 -c \"CREATE USER bidcopilot PASSWORD 'bidcopilot'\"" >/dev/null
  su postgres -c "$PGBIN/psql -h 127.0.0.1 -tc \"SELECT 1 FROM pg_database WHERE datname='bidcopilot'\" | grep -q 1" \
    || su postgres -c "$PGBIN/psql -h 127.0.0.1 -c \"CREATE DATABASE bidcopilot OWNER bidcopilot\"" >/dev/null
fi

if [ "$EXTERNAL_SERVICES" != "1" ]; then
  redis-server --daemonize yes --port 6379 --save '' --appendonly no --protected-mode no

  minio server /data/minio --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 >/data/minio.log 2>&1 &
  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
fi

cd /opt/bidcopilot/backend
# K8s 形态由 migration Job（helm pre-install/pre-upgrade）执行迁移，避免多副本重复升级。
if [ "${BIDCOPILOT_SKIP_MIGRATE:-0}" != "1" ]; then
  alembic upgrade head
fi
# 演示种子一律显式开启（BIDCOPILOT_SEED_DEMO=1），避免默认交付机留下已知口令账号；
# 开启时口令必须显式提供，不再内置弱默认口令。
if [ "${BIDCOPILOT_SEED_DEMO:-0}" = "1" ]; then
  if [ -z "$BIDCOPILOT_DEMO_PASSWORD" ]; then
    echo "BIDCOPILOT_SEED_DEMO=1 requires BIDCOPILOT_DEMO_PASSWORD" >&2
    exit 1
  fi
  python -m app.cli seed-demo --password "$BIDCOPILOT_DEMO_PASSWORD"
fi

cd /opt/bidcopilot/backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 >/data/backend.log 2>&1 &
cd /opt/bidcopilot/frontend
node node_modules/next/dist/bin/next start -H 127.0.0.1 -p 3000 >/data/frontend.log 2>&1 &

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 0.5
done
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3000/login >/dev/null 2>&1; then break; fi
  sleep 0.5
done

nginx -g 'daemon off;' &

cleanup() {
  pkill -f 'next start' || true
  pkill -f 'uvicorn app.main:app' || true
  if [ "$EXTERNAL_SERVICES" != "1" ]; then
    pkill -f 'minio server' || true
    redis-cli -p 6379 shutdown nosave || true
    su postgres -c "$PGBIN/pg_ctl -D $PGDATA stop -m fast" || true
  fi
}
trap cleanup EXIT INT TERM

wait -n
