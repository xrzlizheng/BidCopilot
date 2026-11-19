FROM python:3.12-slim AS backend
WORKDIR /opt/bidcopilot/backend
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra ocr-local
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

FROM node:24-alpine AS frontend
WORKDIR /app
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY frontend .
ARG BACKEND_URL=http://127.0.0.1:8000
ENV BACKEND_URL=$BACKEND_URL
RUN pnpm build

FROM python:3.12-slim
LABEL org.opencontainers.image.title="BidCopilot all-in-one" \
      org.opencontainers.image.description="Single-container delivery: FastAPI app + Next.js frontend + PostgreSQL + Redis + MinIO + nginx (private/on-premise deployment)"

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
  && apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl nginx nodejs postgresql redis-server libgl1 libglib2.0-0t64 libgomp1 \
  && rm -rf /var/lib/apt/lists/* \
  && mkdir -p /data/minio /opt/bidcopilot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/bidcopilot/backend/.venv/bin:$PATH" \
    APP_ENV=production \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    SNAPSHOT_STORE=minio \
    MINIO_ENDPOINT=127.0.0.1:9000 \
    MINIO_BUCKET=bidcopilot \
    MINIO_SECURE=false \
    SCHEDULER_ENABLED=true \
    BACKEND_URL=http://127.0.0.1:8000

COPY --from=backend /opt/bidcopilot/backend /opt/bidcopilot/backend
COPY --from=frontend /app /opt/bidcopilot/frontend
# minio 二进制从官方镜像提取（容器内置 MinIO 服务用）。
# 注意：minio/minio:latest 未固定版本——交付发布前必须固定为已验证的稳定 tag
# （当前交付机无法访问 registry 核实最新稳定版，见交付报告「已知限制」）。
COPY --from=minio/minio:latest /usr/bin/minio /usr/local/bin/minio
COPY deploy/allin1/nginx.conf /etc/nginx/sites-available/default

# 构建期预热 MinerU basic 档模型进镜像（ONNX 小模型，纯 CPU）。
# 模型权重不进 git；运行期 MINERU_MODEL_SOURCE=local 强制离线读镜像内模型，
# 因此模型预热/校验失败必须让构建失败，不能用 WARN 放行出“离线但缺模型”的镜像。
# deploy/allin1/mineru-models/ 是本地权重缓存（.gitignore 排除，只留 .gitkeep）：
# 缓存非空时直接烘焙，省掉每次改代码都从 modelscope 重下 ~800MB；为空则回落到联网下载。
# 两条路径都必须通过 models verify。
COPY deploy/allin1/mineru-models/ /opt/mineru-models/
RUN if [ -d /opt/mineru-models/MinerU-4_models_onnx ]; then \
      mkdir -p /root/.mineru/models \
      && cp -a /opt/mineru-models/MinerU-4_models_onnx /root/.mineru/models/; \
    else \
      MINERU_MODEL_SOURCE=modelscope mineru-kit models download --tier basic --small-backend onnx; \
    fi \
    && mineru-kit models verify --tier basic \
    && echo "mineru models baked"

# entrypoint 放在模型层之后拷贝：改启动脚本不会让上面的模型缓存失效。
COPY deploy/allin1/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV MINERU_MODEL_SOURCE=local

EXPOSE 80
CMD ["/entrypoint.sh"]
