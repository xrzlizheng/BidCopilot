# BidCopilot 离线包

## 构建（联网环境）

```bash
cp deploy/.env.example deploy/.env
# 填入构建所需配置；离线包不会写入真实密钥
bash scripts/build_offline_bundle.sh
```

输出：`dist/bidcopilot-offline-<VERSION>.tar.gz`，包含：

- `images.tar`：单一构建产物 `bidcopilot:single`（本地构建名）与 `alpine:3.20`（离线校验脚本依赖）。
- `deploy/`：Compose、nginx、Helm Chart、`.env.example`（只含示例；`.env`/`.env.bak.*` 等真实密钥文件不会被打包）。
- `scripts/`：备份、恢复、离线校验。
- `licenses-python.txt`、`licenses-node.txt`、`SHA256SUMS`。

## 客户内网安装

```bash
tar xzf bidcopilot-offline-<VERSION>.tar.gz
cd bidcopilot-offline-<VERSION>
sha256sum -c SHA256SUMS
docker load -i images.tar
cp deploy/.env.example deploy/.env
# 填写 KeepRisk/LLM/MinIO 凭据；JWT_SECRET_KEY 留空时容器首启自动生成并持久化
docker compose -f deploy/allin1/docker-compose.yml up -d
curl -fsS http://localhost:18080/healthz
```

演示种子默认不灌入；确需演示数据时设置 `BIDCOPILOT_SEED_DEMO=1` 并提供
`BIDCOPILOT_DEMO_PASSWORD`（见 `deploy/.env.example`）。

## Helm 安装

```bash
helm upgrade --install bidcopilot deploy/helm/bidcopilot \
  --namespace bidcopilot --create-namespace \
  --set secrets.databaseUrl='postgresql+asyncpg://...' \
  --set secrets.jwtSecretKey='<32+ chars 随机值>' \
  --set secrets.modelSecretMasterKey='<base64 32 字节>' \
  --set secrets.keepriskAppCode='...' \
  --set secrets.minioAccessKey='...' \
  --set secrets.minioSecretKey='...' \
  --set ingress.enabled=true --set ingress.host=bidcopilot.example.com
```

镜像 tag 留空时取 `Chart.AppVersion`；升级前请把同版本镜像推到 `image.repository`，
`pullPolicy=Always` 会确保滚动升级拉取新镜像。

生产环境建议使用 `secrets.existingSecret` 或外部密钥管理，不要把真实密钥写入 values/仓库。

## 备份与恢复

```bash
bash scripts/backup.sh
bash scripts/verify_offline_bundle.sh <bundle-dir>   # 可选
bash scripts/restore.sh --backup-dir backups/<timestamp>   # 需输入 RESTORE 确认
```

恢复会覆盖 Postgres 与 MinIO；执行前先停业务并确认备份校验和。

## 回滚

```bash
helm rollback bidcopilot <revision>
# Compose：切换镜像 tag 后 up -d；数据库迁移遵循向前兼容，回滚前先备份
```
