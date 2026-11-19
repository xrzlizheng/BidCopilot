# BidCopilot 项目交付文档

> 版本口径：本文档为 **2026-10-24 交付快照**（当时 head 0038、53 张表、MinerU 4 OCR）；当前代码已到 head 0075、78 张业务表（2026-11-11 按 SQLAlchemy metadata 实测），0061 之后新增通知渠道、客户模板范围、回执附件、AI 用量计量、开放 API 令牌、Webhook 出站、合同审查升级、法规条文、模板版本、同步运行状态、成本台账排序、评测约束与审计哈希加固（迁移 0062–0075；0061 及更早增量见文末 2026-10-31 增量与 `2026-10-31-keeprisk-mcp-integration.md`）。
> 若与 README 验收快照（对应已提交 HEAD b565dd2，head 0027 / 45 表 / PaddleOCR）不一致，以本文口径为当前交付状态。
> 编写日期：2026-10-24。

---

## 0. 文档体系（交付包导航）

本交付包含以下文档，按读者角色取用：

| 文档 | 读者 | 内容 |
|---|---|---|
| [产品指南](2026-10-22-bidcopilot-product-guide.md) | 客户业务/售前 | 产品定位、功能全景、界面、价值陈述、培训脚本 |
| [API 参考](2026-10-24-bidcopilot-api-reference.md) | 客户集成/二开 | 155 端点速查、认证/权限/错误码/分页约定、链路示例 |
| [数据库参考](2026-10-24-bidcopilot-database-reference.md) | 客户 DBA/接手同事 | 表结构、约束、索引、枚举、迁移清单（当前口径 head 0075 / 78 表） |
| 交付文档（本文） | 双方项目经理 | 架构、功能清单、验收记录、部署运维、已知限制 |
| [运维手册](2026-10-24-bidcopilot-ops-runbook.md) | 客户运维/值班 | 巡检、备份恢复、升级、故障处置、安全运营 |
| [开发交接手册](2026-10-24-bidcopilot-dev-handbook.md) | 接手开发/测试 | 环境搭建、代码导航、常见任务、测试、发布 |
| [详细设计文档](2026-10-24-bidcopilot-detailed-design.md) | 接手核心开发 | 模块内部结构、关键算法、契约、状态机、权衡 |
| 仓库根 README.md / AGENTS.md / CONTRIBUTING.md / SECURITY.md | 全部 | 快速启动、硬约束、贡献规范、安全政策 |

---

## 1. 交付结论（TL;DR）

BidCopilot 是一套**私有化部署的招投标智能作业平台**：把招标文件（扫描件/Word/PDF 等）解析为结构化约束集，驱动资格预审、评分矩阵、Bid-No-Bid 决策、标书生成与证据链、合规预检、AI 评标、合同审查与复盘导出的全流程作业。

- **交付形态**：单容器（`bidcopilot-app`：FastAPI 应用 + PostgreSQL + Redis + MinIO + nginx + Next.js 前端），镜像 `bidcopilot:single`（5.14 GB，含 819MB MinerU 模型与 torch-cpu 运行时），本地测试与生产同一镜像同一 compose。
- **质量基线（2026-11-11 复验）**：后端单元测试 **708 passed / 2 skipped**（2 个环境性 skip：webp codec / Windows tzset）；前端 **18 个测试文件 / 59 用例全部通过**；Mypy **0 errors**；Ruff **0 findings**。真实 PostgreSQL 集成测试、Docker 镜像冒烟、Helm lint/install 因环境离线未执行，发布前需补跑。解析金标基线历史记录见第 7 节。
- **本轮替换**：本地 OCR 已由 PaddleOCR 整体替换为 **MinerU 4.0.12**（容器全链路实测：扫描 PDF 首扫 16.7s / 热模型 3.8s，OCR 文本与原文一字不差）。
- **已知边界**：standard/VLM 档需 CUDA 构建变体（当前镜像为 CPU 构建）；PP-UIE 本地兜底在单容器镜像内不可用（默认关）；详见第 9 节。

---

## 2. 系统架构

### 2.1 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 / FastAPI / SQLAlchemy 2.0 async（asyncpg）/ Alembic 75 迁移 |
| 前端 | Next.js 14.2 App Router / React 18 / TypeScript / Tailwind / TanStack Query / Zustand |
| 数据 | PostgreSQL 16（业务库）、Redis（缓存/调度触发，纯内存）、MinIO（快照对象存储） |
| 解析 | pdfplumber（文字层表格）+ MinerU 4（本地 ONNX OCR，tier=auto 显存自适应）+ MiniMax VLM（云端备用） |
| LLM | OpenAI 兼容网关（生成模型 + 独立 Judge 模型，均支持租户/个人 BYOK 信封加密） |
| 调度 | APScheduler（订阅同步 60s / 履约提醒 24h）；Celery 仅为占位未接入 |
| 部署 | 单容器 all-in-one（主形态）；Helm Chart 与离线包（次形态） |

### 2.2 拓扑（单容器）

```
浏览器 ──80──► nginx ─┬─ /api/* ──► 127.0.0.1:8000  FastAPI app（22 个 router）
                      └─ /* ──────► 127.0.0.1:3000  Next.js（next start）
app ─┬─ PostgreSQL  127.0.0.1:5432（PGDATA=/data/pgdata）
     ├─ Redis       127.0.0.1:6379（无持久化）
     ├─ MinIO       127.0.0.1:9000（SNAPSHOT_STORE=minio，/data/minio）
     ├─ KeepRisk 付费网关（商情/企业情报，原始响应落 MinIO 快照）
     └─ LLM / Judge 网关（OpenAI 兼容；OCR 走进程内 MinerU，无外部服务）
```

nginx 对 `/api/` 与 `/` 的 send/read 超时放宽到 300s（LLM 生成与评标是同步长请求，默认 60s 会在真实模型上 504）。

### 2.3 模块清单（backend/app/modules，22 个 include_router + 2 个无路由模块）

| 模块 | 职责 | 路由前缀 |
|---|---|---|
| identity | JWT 登录/刷新、用户与角色（系统角色 + 租户自定义 + org 作用域）、token_version 会话版本链 | `/auth` `/users` `/roles` |
| platform | （企业版闭源）多租户运营 | — |
| org | 组织单元树与成员 | `/org-units` |
| access | 能力词表 + AuthzService + resource_acl（无路由，授权内核） | — |
| audit | 审计事件与哈希链校验 | `/audit` |
| opportunities | 商情同步（手动/订阅/买家情报）、收藏、中标记录 | `/opportunities` `/opportunity-subscriptions` |
| projects | **投标主链路**：项目 CRUD、上传即解析、约束集、评分矩阵、资格预审、决策、响应矩阵、复盘、异步 ParseJob、策略问答 | `/projects` |
| generation | 大纲/章节生成、事实核查（evidence_links）、审批、一键匿名化、docx 导出 | `/projects/{id}/...` |
| evaluation | AI 评标（独立 judge 模型） | `/projects/{id}/evaluations` |
| compliance | 合规预检（29 条规则）、逐条响应、策略配置、break-glass 审批、个人资源转移 | `/compliance` |
| contracts | 合同审查（切分/风险规则/三方对照/确认流）、LLM 深审（按次计费）、履约台账、补充协议、租户自定义规则 | `/projects/{id}/contract-reviews` |
| assets | 企业资产四类（资质/人员/业绩/信用）+ 导入 + 到期提醒 + 字段级脱敏策略 | `/companies` `/assets` |
| company_intel | 企业档案与中标同步；企业情报（工商/信用/股权关联/尽调）、批量核验（≤100）、企业对比（2–5） | `/companies/{id}/...` `/company-intel/*` |
| knowledge | 知识库/模板/技能/文档/分块/图片，四层访问控制（scope/classification/clearance/ACL） | `/knowledge` `/knowledge-packs` |
| system | AI 状态、模型配置与策略、BYOK 密钥信封加密 | `/system` |
| costs | 网关与 LLM 兜底计费台账 | `/analytics/data-ops` |
| notifications | 消息中心 | `/notifications` |
| web_search | 联网检索 | `/web-search` |
| gateway | KeepRisk 客户端/适配器/快照/DataResult 契约；企业数据 MCP 客户端与适配器（无路由） | — |
| tender | 解析器/OCR 供应商/双层兜底（无路由） | — |

横切：`app/core`（JWT/权限/错误）、`app/db`（TenantRepository/ advisory lock /会话）、`app/workers`（APScheduler）。

> 2026-10-31 增量：企业数据 MCP（工商/信用/股权/尽调）已接入，接口 `/company-intel/*`；入口按对象拆分（资产=工商自检、商机=采购人、项目=投标主体、合同=相对方）；批量核验 1 次/批、企业对比约 10 次/家、缓存命中不计费。单测 459 passed / 2 skipped，集成 3 passed，镜像 `659f071c8689`。

### 2.4 核心链路（以投标作业为例）

```
商机同步（KeepRisk，advisory lock 防并发、记账容错）
  └─► 一键转项目 ─► 上传招标文件
        ├─ 图片 / 扫描 PDF（无文字层）/ 本地解析器不识别的类型 ─► MinerU OCR（parse_meta 记 provider/tier/pages）
        ├─ docx ─► python-docx；PDF 文字层 ─► pdfplumber（表格 bbox 去重）
        └─► build_constraint_set ─► tender_constraints（版本链）
              └─► 评分矩阵 / 资格预审 / Bid-No-Bid / 生成（证据链）/ 合规预检 / AI 评标 / 复盘导出
```

约束集产出键：`project / reject_clauses / scoring_items / qualification_requirements / substantive_requirements / clarifications / packages / format_rules / timeline / budget / submission_checklist / dark_bid / parse_meta`。澄清文件按“原招标文件为准”合并；`bid_deadline` 仅取显式“投标截止/递交”带时刻的条目，不用发布时间或猜测值填充。

---

## 3. 功能交付清单

### 3.1 已交付（默认可用）

| 域 | 功能 |
|---|---|
| 商情 | 手动/订阅同步（间隔可调、每租户订阅上限）、买家竞争情报（历史中标聚合）、联网检索、中标记录、赢率分析 |
| 解析 | 多类型解析（docx/pdf/txt/md/图片 + MinerU 全集 24 种）、OCR 双供应商（MiniMax 云端 / MinerU 本地，auto 语义）、扫描件表格→table_cell 块走表格优先抽取、澄清文件合并、长文档异步 ParseJob |
| 项目作业 | 评分矩阵、资格预审、Bid-No-Bid 决策、响应矩阵与偏离表、章节生成与事实核查（无证据结论拒收）、审批流、docx 导出、一键匿名化（主动脱敏） |
| 合规 | 29 条规则预检、逐条响应、策略按类目可调（含租户可调查重阈值）、break-glass 提权审批 |
| 使用帮助 | 产品内 `/help`：9 步主线、20 个模块章节、状态速查、FAQ、权限与边界说明；侧边栏按角色显示 |
| 合同 | 条款切分/风险规则/三方一致性对照/确认流、LLM 深审（显式触发、按次计费、无证据结论拒收）、履约台账提醒、补充协议对照、租户自定义规则 |
| 资产与知识 | 四类企业资产 + CSV/XLSX 导入 + 到期提醒、知识库（文档/分块/召回/图片）+ 模板与技能目录 |
| 治理 | 多租户、capability 化权限（系统角色/自定义角色/org 作用域）、组织单元、字段级脱敏策略、知识库四层 ACL、离职资源转移、审计哈希链、模型 BYOK（信封加密） |
| 平台 | 平台租户管理、计费台账（网关 + LLM 兜底，透明可查）、通知中心 |

### 3.2 默认关闭或需外部依赖

| 功能 | 条件 |
|---|---|
| 规则后双层兜底抽取（PP-UIE 本地 → 云端 LLM） | `TENDER_UIE_FALLBACK_ENABLED` / `TENDER_LLM_FALLBACK_ENABLED`，默认全关；LLM 兜底按次计费 |
| MiniMax 云端 OCR | 配置 `MINIMAX_API_KEY/HOST` |
| 商情同步 | 配置 `KEEPRISK_API_BASE_URL` + `KEEPRISK_APP_CODE` |
| LLM 生成/评标 | 配置 `LLM_*` / `JUDGE_*`；未配置时明确 503，不伪造结果 |
| MinerU standard/VLM 档 | CUDA 构建变体 + 对应模型 bake（当前镜像 CPU 构建，auto 恒收敛 basic） |

---

## 4. 非功能与安全设计（与 AGENTS.md 硬约束对齐）

| 约束 | 落实 |
|---|---|
| 生产禁 Mock | 外部数据只经 `app/modules/gateway`；测试替身只在 `backend/tests/`（respx / dependency_overrides / monkeypatch） |
| 租户隔离 | 所有租户业务查询经 `TenantRepository`；跨租户按 ID 读取返回 404，不泄露存在性（`test_capability_boundary.py` 锁定） |
| 诚实失败 | `DataResult` 状态原样上传 API 与前端；非 ok/empty 不降级为空列表；解析失败落 `parse_error`（`ocr_failed` / `ocr_not_configured` / `scanned_pdf_requires_ocr`） |
| 防幻觉 | 生成/深审强制证据引用（evidence_links），无证据结论拒收；LLM 兜底产出标 `confidence="uie"/"llm"` |
| 计费透明 | 三条付费路径（手动同步/订阅调度/buyer-intel）统一 advisory lock + 记账容错——记账失败不 500、重试不二次计费；LLM 深审显式按次计费 |
| 凭据安全 | 无弱默认密钥（启动自动生成落 `/data/jwt.key`）；BYOK 密钥 AESGCM 信封加密；`check_secrets.py` 门禁；许可证白名单（Apache-2.0 兼容） |
| 公告内容清洗 | 外部公告 HTML 白名单清洗、限 URL scheme、限正文大小，不渲染原始 HTML |
| 截止时间 | `bid_deadline` 不由外部提供；显式带时刻才解析，不用发布时间/猜测值 |
| 脚本换行 | 所有 `.sh` LF 校验（`b"\r" not in data`） |

---

## 5. 测试与质量门禁

### 5.1 测试分层（264 条）

| 层 | 数量 | 说明 |
|---|---|---|
| unit（35 文件） | 195 | 解析/规则/格式/JWT/权限/配置/客户端（respx 打桩） |
| integration（37 文件） | 68 | 真实 PostgreSQL（compose 55432 口）+ ASGITransport 打真实 app；覆盖全部主要 API 面 |
| gateway_live | 1 | 真实 KeepRisk 计费冒烟，默认 skip（`GATEWAY_LIVE=1` 手工门禁） |
| 金标基线 | 语料级 | 19 份对抗语料（docx/pdf/txt/md 四版式）：评分 54/54、资格 36/36、废标 22/22，micro P/R 1.000 |

历史收集口径合计 264 条；2026-10-24 全量执行 **257 passed / 3 skipped**（skip 含 gateway_live 与条件性用例）。**2026-11-11 复验**：后端单元 **708 passed / 2 skipped**；前端 **18 文件 / 59 用例**；Mypy/Ruff 0 findings；真实 PostgreSQL 集成测试、Docker 镜像冒烟、Helm lint/install 因当前离线未执行。

运行方式：`cd backend && uv run pytest tests/unit -q`（单测）；全量 `pytest -q` 含集成（需 PG）。

### 5.2 CI（`.gitlab-ci.yml`，四门禁）

`backend-lint`（ruff + mypy app）→ `backend-test`（PG/Redis 服务 + alembic upgrade head + 全量 pytest）→ `frontend`（lint / typecheck / vitest / build）→ `security`（版本一致性 / AST 禁导入边界 / 许可证 / 凭据扫描）→ `gateway-live`（manual）。

### 5.3 冒烟脚本（`scripts/`）

`run_local_site.sh`（唯一启动入口）、`smoke_demo.py`（部署后功能冒烟，不调外部 LLM）、`smoke_ai.py`（真实生成+评标，计费）、`fresh_db_smoke.py`（空库部署验收）、`preflight.py`（试点预检，可探 OCR 计费）。演示口令一律经 `BIDCOPILOT_DEMO_PASSWORD` 环境变量或 `--password` 显式提供，仓库与镜像均不内置默认口令（缺口令拒绝启动/报错退出）。

---

## 6. 部署与运维

### 6.1 单容器启动顺序（entrypoint.sh）

JWT 密钥兜底生成 → PostgreSQL（initdb/启动/建用户库）→ Redis → MinIO → `alembic upgrade head` → `seed-demo` → uvicorn（/data/backend.log）→ next start（/data/frontend.log）→ 就绪轮询 → nginx。任一子进程退出整体退出（trap cleanup）。

### 6.2 配置分类（`deploy/.env.example`）

- **必填**：`JWT_SECRET_KEY`（≥32 位，未提供自动生成）、`DATABASE_URL`（entrypoint 兜底）、`KEEPRISK_*`（同步商机才需要）
- **演示种子**：`BIDCOPILOT_SEED_DEMO=1` 时必须显式提供 `BIDCOPILOT_DEMO_PASSWORD`（无内置默认口令，缺失拒绝启动）；默认 0 不灌演示数据
- **敏感（禁入库）**：`JWT_SECRET_KEY` / `KEEPRISK_APP_CODE` / `LLM_API_KEY` / `JUDGE_API_KEY` / `MINIMAX_API_KEY` / `*_SEARCH_API_KEY` / `MODEL_SECRET_MASTER_KEY` / `MINIO_*`
- **OCR**：`OCR_PROVIDER=auto|minimax|mineru|none`、`MINERU_TIER=auto`（显存自适应）、`MINERU_GPU_MIN_VRAM_GB=8`、`MINERU_TIMEOUT_SECONDS=300`、`MINIMAX_OCR_MAX_PAGES`（同时是 MinerU 页数预算）
- **注意**：`POSTGRES_DB/USER/PASSWORD` 对单容器无效（entrypoint 硬编码 bidcopilot/bidcopilot），仅 Helm 形态有意义——属已知配置面漂移，见第 9 节。

### 6.3 备份与恢复

`scripts/backup.sh`：`pg_dump -Fc` + `/data/minio` tar + `SHA256SUMS`。恢复（`restore.sh`）整体覆盖、需输入 `RESTORE` 确认；`verify_restore.sh` 非破坏性演练（临时库比对 tables/migration/users/projects）。**未覆盖**：`/data/jwt.key`（丢失则全部会话失效）；Redis 无持久化无需备份。

### 6.4 其他形态

Helm Chart（backend/frontend/worker Deployment + Ingress + HPA + migration-job + Secret 支持 existingSecret）；离线包（`build_offline_bundle.sh` / `verify_offline_bundle.sh`，产物 `dist/bidcopilot-offline-*.tar.gz`，含镜像 tar + SHA256SUMS）。

### 6.5 健康检查

`GET /healthz`（存活，nginx 同源）；`GET /readyz`（实探 DB+Redis，不可用 503）。容器层无 HEALTHCHECK 段，由 entrypoint 轮询 + 外部 curl 判定。

---

## 7. 验收记录

### 7.1 MinerU OCR 替换（2026-10-24，本次交付）

| 项 | 实测值 | 命令/位置 |
|---|---|---|
| 镜像构建 | exit 0；uv sync 1283s；模型 bake 41.9s | `docker build -f deploy/allin1/Dockerfile -t bidcopilot:single .` |
| 镜像体积 | 5.14GB（含 819MB ONNX 模型 + torch-cpu 187MB 轮子） | `docker images bidcopilot:single` |
| 模型离线验证 | `MinerU-4_models_onnx: ok`，`MINERU_MODEL_SOURCE=local` 可读 | `docker exec bidcopilot-app mineru-kit models verify --tier basic` |
| AI 状态透出 | `ocr.provider=mineru / tier=auto / choice=mineru` | `GET /api/v1/system/ai-status` |
| 扫描 PDF 解析 | 首扫 16.7s（含模型加载）、热模型 3.8s；`ocr_provider=mineru`、`ocr_structure_used=true` | 上传 290KB 无文字层扫描模拟 PDF（0 字符文字层） |
| 抽取质量 | 资格要求 4 条、预算 480 万元、评分项文本与原图一字不差，`source_span` 指向 OCR 段落 | 项目约束集 |
| 测试 | 257 passed / 3 skipped（含 26 个重写 OCR 单测 + 集成） | `pytest -q` |

金标基线与容器冒烟历史数字（45 表/head 0027 口径）见 README「验收快照」。

### 7.2 权限治理波（0028–0038 迁移，未提交）

capability 化权限（系统角色→能力词表）、org 作用域角色、组织单元、字段级脱敏策略、知识库四层 ACL、break-glass 审批、离职资源转移、模型策略/BYOK——每个阶段均有配套集成测试（`test_capability_boundary` / `test_scoped_roles` / `test_field_policies` / `test_break_glass` / `test_offboarding_transfer` 等）。

---

## 8. 目录结构

```
BidCopilot/
├── backend/app/            FastAPI 模块化单体（modules/ core/ db/ workers/ cli/）
├── backend/tests/          unit(35) integration(37) gateway_live(1) + support/gold_corpus
├── backend/alembic/        75 个迁移（0001–0075）
├── frontend/src/           Next.js App Router（app/ components/ lib/）
├── deploy/allin1/          单容器（Dockerfile / entrypoint.sh / nginx.conf / compose）
├── deploy/helm/            K8s Chart；deploy/offline/ 离线包说明
├── scripts/                启动/备份/冒烟/门禁脚本（见 5.3、6.3）
├── docs/product/           产品指南（对外）+ 本文档
└── demo-site/dist/         静态演示站（宣贯材料，与 frontend 无代码关系）
```

---

## 9. 已知限制与后续项

| # | 项 | 说明 / 建议 |
|---|---|---|
| 1 | MinerU standard/VLM 档 | 当前镜像为 CPU 构建（torch 钉 CPU 轮子），`MINERU_TIER=auto` 恒收敛 basic。需要 VLM 质量时构建 CUDA 变体（onnxruntime-gpu / llama.cpp CUDA wheel）并 bake standard 模型——属部署选项，非缺陷。 |
| 2 | PP-UIE 本地兜底 | 镜像内无 paddlenlp，`TENDER_UIE_FALLBACK_ENABLED` 开启亦不可用；云端 LLM 兜底可用。已记录为已知边界。 |
| 3 | README 快照滞后 | README 验收快照对应已提交 HEAD（head 0027 / 45 表 / Paddle）；当前工作区为 0075 / 78 表 / MinerU。本文档为当前口径。 |
| 4 | 配置面漂移 | `deploy/.env.example` 的 `POSTGRES_*` 对单容器无效；`DATABASE_URL`/`REDIS_URL` 未列而依赖默认值。建议下轮收敛。 |
| 5 | CI 托管 | 仅有 `.gitlab-ci.yml`；若仓库迁 GitHub 需迁移门禁。 |
| 6 | 集成测试默认全量跑 | 无 `-m "not integration"` 兜底，无本地 PG 的环境需显式 `pytest tests/unit`。 |
| 7 | Redis 无持久化 | 单容器演示形态可接受；生产化需评估调度/broker 重启语义。 |
| 8 | fakeredis 未使用 | dev 依赖清理项。 |
| 9 | `minio/minio:latest` 未固定版本 | `deploy/allin1/Dockerfile`（提取 minio 二进制）与 `deploy/external/docker-compose.yml`（MinIO 服务）引用 `minio/minio:latest`；当前交付机无法访问 registry 核实最新稳定版。**发布前必须固定为已验证的稳定 tag 并重新构建验证**（见上线检查单）。 |

---

## 10. 附录：运维命令速查

```bash
# 启动（唯一入口；演示口令显式提供，不内置默认值）
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' bash scripts/run_local_site.sh   # http://127.0.0.1:18080/login

# 测试
cd backend && uv run pytest tests/unit -q # 单测
cd backend && uv run pytest -q            # 全量（需 PG:55432）
cd backend && uv run ruff check . && uv run mypy app

# 冒烟
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' python scripts/smoke_demo.py   # 部署后功能冒烟
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' python scripts/fresh_db_smoke.py   # 空库部署验收

# 备份/恢复
bash scripts/backup.sh                    # → backups/<时间戳>/
bash scripts/verify_restore.sh --backup-dir backups/<ts>
bash scripts/restore.sh --backup-dir backups/<ts> --yes

# 容器运维
docker compose -f deploy/allin1/docker-compose.yml logs -f
docker exec -it bidcopilot-app bash       # 日志在 /data/*.log

# 演示账号：demo@example.com；口令为启动时显式设置的 BIDCOPILOT_DEMO_PASSWORD（不内置默认口令）
```

---

# 2026-11-17 增补（对正文 dated 快照的当前态更正，正文不回改）

- 迁移：head `0077_price_quotes`；社区版业务表 **78 张**（`tenant_plans` 属企业版）。正文 :3/:17/:43 的 0075/78 为 11-11 口径。
- API：社区版 **259 条路由**（多租户运营 5 端点属企业版）；正文 :16 的 155 端点为历史口径，详见 api-reference §1/§3.15/§3.16。
- 测试：后端 **986 passed / 2 skipped**（集成对真实 PostgreSQL 实跑，`localhost:25432/bidcopilot_test` + Redis `6390`）；前端 **20 文件 / 69 用例**（`docker build --target frontend` 镜像内）；`tsc --noEmit` 0。正文 :31 的"集成离线未跑"已过时。
- 部署：单容器 `bidcopilot-app`，镜像 `bidcopilot:single` = `5e621053831e`；宿主端口 **18080 / 25432 / 6390**（55432 落 Windows 保留段、6380 Docker 分配器泄漏，compose 有注释）。smoke_demo 对最终镜像 **SMOKE OK**；浏览器完成「配置卡→策略→报价→明细校验→导出」全链路验收。
- 本轮新增能力：**报价单生成/导出闭环**（`price_quotes` 0077 + 三端点 + 面板）、**报价成本参数配置**（`GET/PUT /system/pricing-settings`）、**审计哈希链受控修复**（`repair-audit-chain` CLI，演示租户断链已修复，`/audit/verify` valid=true）、Webhook 投递 outbox（0076）、Card aria-label 无障碍修复、help-content 七处口径修正。
- 测试文件数：unit 77 个 / integration 75 个（正文 :233 的 35/37 为早期口径）。
- Helm 仍为单 Deployment（无 frontend/worker Deployment）；后台执行模型仅 APScheduler + PG advisory lock（Celery 已退役）。
