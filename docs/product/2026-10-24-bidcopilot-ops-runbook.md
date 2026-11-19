# BidCopilot 运维手册（Runbook）

> 适用对象：客户方运维人员、接手项目的值班同事。
> 前置阅读：[交付文档](2026-10-24-bidcopilot-delivery-report.md)（架构与部署）、[数据库参考](2026-10-24-bidcopilot-database-reference.md)（升级前查表结构）。
> 编写日期：2026-10-24。

## 1. 日常巡检（建议每日）

```bash
# 1) 容器与服务健康
docker ps --filter name=bidcopilot-app --format "{{.Status}}"
curl -fsS http://127.0.0.1:18080/healthz          # 存活：{"status":"ok","version":...}
curl -fsS http://127.0.0.1:18080/readyz           # 就绪：DB+Redis 探活，不可用 503

# 2) 日志（滚动查看，关注 ERROR/Exception/ocr_failed）
tail -f /data/backend.log /data/minio.log         # 容器内路径；宿主：docker exec -it bidcopilot-app tail -f /data/backend.log

# 3) 磁盘（数据卷：PG 数据 + MinIO 对象 + 日志）
docker system df -v | grep bidcopilot_data
docker exec bidcopilot-app du -sh /data/pgdata /data/minio

# 4) 备份完整性（见 §2）
ls -lh backups/
```

**关注项**：`/readyz` 503（DB/Redis 异常）；`backend.log` 反复出现 `ocr_failed`（OCR 通道问题，见 §4）；`keeprisk response pcode=... code=...` 非 100/110（网关契约问题）；磁盘增长异常（MinIO 快照无自动清理策略，见 §5）。

## 2. 备份与恢复

### 2.1 备份（每日，建议 cron）

```bash
bash scripts/backup.sh        # 产出 backups/<时间戳>/：postgres.dump + minio.tgz + metadata.txt + SHA256SUMS
```

内容与边界：
- **PG 逻辑备份**（`pg_dump -Fc`，自定义格式）+ **MinIO 数据目录 tar**，两者分离。
- **不备份**：`/data/jwt.key`（丢失则全部会话失效，用户重新登录即可，但 refresh cookie 全部作废）；Redis（纯内存无持久化，无需备份）；`/tmp/postgres.log`（容器重建即丢）。
- 每次备份附 `metadata.txt`（VERSION/UTC 时间/容器名）与 `SHA256SUMS`，恢复前先校验和。

### 2.2 恢复演练（非破坏性，每月一次）

```bash
bash scripts/verify_restore.sh --backup-dir backups/<ts>
```

在临时库 `bidcopilot_restore_check_<ts>` 上恢复并比对 tables/migration/users/projects 快照，源库只读，演练后自动清理。

### 2.3 恢复（破坏性，需维护窗口）

```bash
docker compose -f deploy/allin1/docker-compose.yml stop     # 先停业务
bash scripts/restore.sh --backup-dir backups/<ts> --yes     # 整体覆盖 PG + MinIO，需输入 RESTORE 确认
bash scripts/run_local_site.sh                              # 重新启动
```

恢复后必查：`/readyz` 200；登录正常；`alembic current` 版本与备份一致（`docker exec bidcopilot-app alembic current`）。

## 3. 升级流程

### 3.1 常规升级（版本发布）

```bash
git pull                       # 或获取交付介质
bash scripts/backup.sh         # 升级前备份（强制）
docker build -f deploy/allin1/Dockerfile -t bidcopilot:single .
docker compose -f deploy/allin1/docker-compose.yml up -d   # 同名容器替换，数据卷保留
curl -fsS http://127.0.0.1:18080/healthz
```

- 迁移由 entrypoint 自动执行（`alembic upgrade head`，**只前进**）；共享环境禁止 destructive downgrade。
- 确认版本：`/healthz` 的 `version` 字段；解析器版本见项目工作台约束集 `parse_meta.parser_version`。
- **回滚**：无向下迁移——回滚 = 停容器 + 用升级前备份恢复（§2.3）。

### 3.2 配置变更

| 变更 | 生效方式 |
|---|---|
| `.env` 修改（LLM 密钥、OCR_PROVIDER 等） | 需重启容器：`docker compose ... up -d` |
| 模型配置（base_url/model/api_key） | 热生效：`PATCH /api/v1/system/model-configs/{purpose}`（`model:manage`），无需重启 |
| 个人模型（BYOK） | `PUT /api/v1/system/model-configs/personal/{purpose}`（`model:use` + 租户策略开启） |
| 合规策略 | 热生效：`PATCH /api/v1/policies/{rule_id}`（下次扫描即用新策略） |
| 报价成本参数 | 热生效：`PUT /api/v1/system/pricing-settings`（`tenant:settings:manage`；项目页「更多决策工具 → 报价成本参数」卡同路径），下次报价策略测算即用 |
| 合同租户规则 | 热生效：`POST .../contract-reviews/tenant-rules` |
| 企业数据 MCP（`KEEPRISK_MCP_*`） | 需重启容器；token 只经环境变量/Secret 注入 |
| 通知渠道 SMTP（`SMTP_*`） | 需重启容器；口令只经环境变量/Secret 注入；企业微信 Webhook 在「账号设置 → 通知渠道」页维护，无需重启 |
| 开放 API Webhook（`WEBHOOK_*`） | 需重启容器；默认拒绝内网接收端，`WEBHOOK_ALLOWED_HOSTS` 精确放行；API 令牌在「账号设置 → 开放 API」页签发，无需重启 |
| 情报监控订阅（`INTEL_WATCHLIST_*`） | 需重启容器；间隔/上限/批次在 `config.py` |

改 `.env` 前备份原文件；密钥类配置只经环境变量/密钥管理，不写仓库。

### 3.3 重新构建离线包

镜像含 MinerU 运行时与模型（819MB），构建需可访问 PyPI 与 modelscope：

```bash
bash scripts/build_offline_bundle.sh     # 产物 dist/bidcopilot-offline-<VERSION>.tar.gz + SHA256SUMS
bash scripts/verify_offline_bundle.sh    # 交付前校验
```

注意：仓库 `dist/` 内的旧 468MB 包为 MinerU 替换前构建，不可用于当前版本。

### 3.4 回填历史本地快照引用（升级到 MinIO 存储后）

早期版本用本机文件存储写入过知识库图片/文档的 `object_ref`（形如
`.snapshots\knowledge\...`），切到 MinIO 后这些引用不可读，接口返回 503、
页面显示「图片不可用」。升级后按下面步骤一次性回填（脚本默认 dry-run）：

```bash
# 1) 把宿主机上的 .snapshots 目录与脚本拷进容器（镜像不含 .snapshots）
docker cp backend/.snapshots bidcopilot-app:/tmp/bidcopilot-backend/.snapshots
docker cp scripts/backfill_snapshot_refs.py bidcopilot-app:/opt/bidcopilot/scripts/

# 2) 先看计划（不会改数据）
docker exec -e DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@127.0.0.1:5432/bidcopilot' \
  -w /opt/bidcopilot/backend bidcopilot-app \
  python /opt/bidcopilot/scripts/backfill_snapshot_refs.py --local-root /tmp/bidcopilot-backend

# 3) 确认「无法解析」为 0 后执行
docker exec -e DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@127.0.0.1:5432/bidcopilot' \
  -w /opt/bidcopilot/backend bidcopilot-app \
  python /opt/bidcopilot/scripts/backfill_snapshot_refs.py --local-root /tmp/bidcopilot-backend --apply

# 4) 清理容器内临时文件
docker exec bidcopilot-app rm -rf /tmp/bidcopilot-backend
```

回填后 `GET /api/v1/knowledge/images` 的每张图 `.../content` 应为 200；
源文件已丢失的行会列在「无法解析」里，只能由用户重新上传。

### 3.5 回填历史补遗约束集（升级到「补遗按维度继承」之后）

旧版本上传补遗时，新版本约束集只由补遗文本解析而成，未提及的维度会被清空，
表现为「上传补遗后评分矩阵为空、AI 评标 422 没有可评分的评分项、响应表归零」。
升级后对存量数据执行（脚本默认 dry-run）：

```bash
docker cp scripts/backfill_amendment_constraints.py bidcopilot-app:/opt/bidcopilot/scripts/
docker exec -e DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@127.0.0.1:5432/bidcopilot' \
  -w /opt/bidcopilot/backend bidcopilot-app \
  python /opt/bidcopilot/scripts/backfill_amendment_constraints.py          # 看计划
docker exec -e DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@127.0.0.1:5432/bidcopilot' \
  -w /opt/bidcopilot/backend bidcopilot-app \
  python /opt/bidcopilot/scripts/backfill_amendment_constraints.py --apply  # 执行
```

脚本做两件事：把上一版约束集里补遗未提及的维度补进最新 `payload`；并在
`payload` 有评分项但 `scoring_items` 表为空时补表记录（矩阵/评标按
`constraint_id` 读表，只补 payload 不足以恢复）。补齐后
`payload.parse_meta.amendment_inherited_dimensions` 会记录继承了哪些维度。

## 4. 故障处置

| 现象 | 定位 | 处置 |
|---|---|---|
| `/readyz` 503 | `backend.log`；`docker exec ... pg_isready` / `redis-cli ping` | PG：查 `/data/pgdata` 磁盘与权限；Redis 纯内存，重启容器即重建 |
| 上传解析失败 `ocr_failed: ...` | `/system/ai-status` 的 `ocr.provider`；`backend.log` | provider=mineru：确认镜像内 `mineru-kit models verify --tier basic` ok、`MINERU_MODEL_SOURCE=local`；首扫含模型加载约十几秒，超时调 `MINERU_TIMEOUT_SECONDS`（重启生效） |
| 生成/评标 503 `llm_not_configured` | ai-status 的 llm/judge | 配 `LLM_*`/`JUDGE_*` 或热改模型配置；未配置属预期行为（不伪造） |
| 商情同步 409 | 响应文案"相同查询的同步正在执行中" | 并发互斥（advisory lock）属预期；稍后重试，勿并行双开 |
| 同步/情报无数据但网关计费 | `SyncRunResponse.warnings`（如 `missing_uid`）、`snapshot_ref` | 告警码透传上游问题；原始响应在 MinIO 快照（`keeprisk/{pcode}/{日期}/`），凭 `snapshot_ref` 取 |
| MinIO 上传失败 | `minio.log` | 查 `/data/minio` 磁盘；桶不存在会自动建（`make_bucket`） |
| 登录 401"会话已失效" | 改密/角色变更/停用所致 | 预期行为（token_version）；重新登录 |
| 页面数据不更新 | 后端无热重载 | 重启 API 进程（容器）后刷新 |
| 知识库图片/文档「图片不可用」或 503 `知识库图片存储不可用` | `knowledge_images.object_ref` / `knowledge_documents.object_ref` 是否以 `s3://` 开头 | 历史版本用本地存储（`SNAPSHOT_STORE=local`）写入的引用在切 MinIO 后不可读。用 `scripts/backfill_snapshot_refs.py` 回填（先 dry-run 再 `--apply`），操作见 §3.4 |
| 上传补遗后评分矩阵为空、AI 评标 422「没有可评分的评分项」、响应表归零 | `tender_constraints.payload->'scoring_items'` 与 `scoring_items` 表是否为空 | 旧版本补遗只解析补遗文本，未继承原招标维度。升级后用 `scripts/backfill_amendment_constraints.py` 补齐（先 dry-run 再 `--apply`），操作见 §3.5 |
| 磁盘持续增长 | MinIO 快照无 TTL | 定期清理 `/data/minio/keeprisk/**` 旧日期目录（注意 `raw_ref` 指向的快照被删除后溯源失效，按留存策略权衡） |
| 企业情报 `unavailable` / `mcp_not_configured` | 容器 env；`/healthz` | 确认 `KEEPRISK_MCP_ENABLED=true` 且 `KEEPRISK_MCP_TOKEN` 已注入；重启容器生效 |
| 企业情报 `unauthorized` / `rate_limited` | 响应 `error.code`；上游配额 | 401/403 查 token；429 查当天 5000 次配额或上游限流；成本见「数据运营」 |
| 企业情报 `billing_units=0` 但 `api_calls>0` | 上游缓存命中 | 正常：缓存不计费，不要重复调用 |
| 企业对比 `risk_level=red` 但 `risk_summary=green` | `credit_score.risk_level` | 原样展示并提示单家尽调；不得用绿色摘要判定通过 |
| 监控订阅不触发 | `INTEL_WATCHLIST_ENABLED`、调度日志 `intel watchlist` | 确认开关/间隔；调度器互斥锁未获锁属正常；`/company-intel/watchlist` 可手动 run |
| 监控检查无通知 | 首次检查只建基线；仅风险变化才通知 | 查看 `last_digest` 是否变化；通知写入 `notifications` |
| 版本形态 | 社区版不限量；企业版才有套餐 429 | 套餐超限处置属企业版运维（平台页调套餐/留空上限） |
| 审计链校验失败（`/audit/verify` valid=false） | 历史清理删行导致 prev_hash 断链 | 容器/部署机执行 `python -m app.cli repair-audit-chain --tenant-id <uuid> --dry-run` 确认断点与影响行数，再去掉 --dry-run 应用；修复事件 `audit.chain.repair` 留痕，完成后复跑 `/audit/verify` 应 true |

## 5. 安全运营

- **密钥轮换**：`MODEL_SECRET_MASTER_KEY` 轮换需重加密存量 `secret_store`（AAD 绑定租户/作用域/属主，直接换主密钥会导致解密失败 503——按密钥管理流程迁移）；JWT 轮换 = 改 `JWT_SECRET_KEY` 重启（全部会话失效）；`KEEPRISK_MCP_TOKEN` 轮换 = 改 env/Secret 并重启，旧 token 失效由上游侧控制。
- **审计巡检**：`GET /api/v1/audit/verify`（哈希链校验，返回 `{"valid","events"}`）；异常操作（导出、调阅审批、离职移交、策略变更）定期抽查。
- **破窗监视**：`GET /api/v1/compliance/access-requests` 关注 approved 记录（申请人≠审批人为硬约束；到期惰性失效）。
- **离职处置**：停用用户即触发个人资源孤儿化（知识库/模板/密钥同步失效），随后经 `POST /compliance/personal-resources/{type}/{id}/transfer` 移交。
- **凭据红线**：任何密钥不进 git、不进日志、不进工单正文；泄漏怀疑时先轮换再排查（`scripts/check_secrets.py` 可扫仓库）。

## 6. 性能与容量参考（实测基线）

| 项 | 基线 | 说明 |
|---|---|---|
| 镜像 | 5.14GB（含 819MB 模型 + torch-cpu） | 数据卷另计 |
| 扫描 PDF 解析 | 首扫约 17s（含模型加载）、热模型约 4s/页级 | MinerU 进程内模块级锁**串行化**，并发上传不会加速，但也不会内存翻倍 |
| nginx 超时 | send/read 300s | LLM 生成/评标同步长请求；调大需同步改 `deploy/allin1/nginx.conf` |
| 商情同步 | 单次请求秒级 | 付费调用有 advisory lock；订阅 tick 60s |
| 订阅间隔 | ≥30 分钟（每租户 ≤10 个订阅） | `SYNC_MIN_INTERVAL_MINUTES` / `OPPORTUNITY_MAX_SUBSCRIPTIONS_PER_TENANT` |
| PG 连接池 | SQLAlchemy 默认 5 + 10 溢出 | 单容器形态足够；多副本（Helm）时按副本数评估 |

**扩容路径**：单容器垂直扩容（CPU/内存）优先；水平扩容走 Helm 形态（注意 advisory lock 跨进程互斥依赖同一 PG；系统无消息 broker——Celery 已退役，Webhook 投递重试由 PG outbox + 调度 job 承担，Redis 仅承担限流/探活/refresh 重放防护）。

## 7. 应急联系与信息收集

报障时请附：`/healthz` 与 `/readyz` 输出、`docker ps` 状态、`backend.log` 相关时段（脱敏）、`/system/ai-status` 输出、出错请求的响应体（`code/message`）。排查口径：**非 ok/empty 的网关状态一律原样透传，不得降级为空列表**——看到空列表先怀疑客户端处理，而非服务端丢数。
