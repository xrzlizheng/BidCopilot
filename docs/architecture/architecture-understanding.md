# BidCopilot 当前态架构理解

- 建模日期：2026-10-30
- 范围：当前态（current state），不含目标态设计
- 证据：`backend/app`、`frontend`、`deploy`、`docs/adr/ADR-0001-deployment-topology.md`、`README.md`
- 关联产物：`bidcopilot-system.structurizr.dsl`、`bidcopilot-containers.dot`、`system-model.evidence.md`、`system-model.summary.md`
- 口径说明：本文的“高置信”指代码/配置直接确认结构；运行态行为未在本轮重新实测，凡引用 README 验收快照的地方均为文档证据，不转抄为本人实测值。

## 1. 结论摘要

BidCopilot 是自托管、多租户的**投标决策与交付平台**，业务主线为：商机发现 → 自动匹配/立项 → 招标文件解析 → 标书生成与事实核查 → 合规预检与逐条响应 → AI 评标 → 报价策略 → 合同审查与履约 → 复盘。

当前态由四层构成：

1. **Web 工作台**：Next.js 14 App Router，浏览器端 `fetch` + TanStack Query，access token 只存内存，refresh token 为 HttpOnly Cookie（`frontend/src/lib/api.ts:39-63`、`auth-store.ts:14-21`）。
2. **API 服务**：FastAPI 模块化单体，`main.py` 注册 30 个 router（`backend/app/main.py:66-97`），业务代码分布在 21 个 `app/modules/*` 模块（社区版；企业版 platform 模块经 try-import 挂载为第 31 个）。
3. **数据与文件**：PostgreSQL 16（业务库，76 个迁移，head `0077_price_quotes`，社区版链）、Redis 7（登录限流、就绪探活、refresh 重放防护）、MinIO 或本地目录（网关快照、知识文件、图片）。
4. **后台任务**：进程内 APScheduler，8 个 job（订阅 tick、里程碑、投标截止、合同到期、解析任务回收、AI 运行回收、Webhook outbox 重试、情报监控），多实例互斥依赖 PostgreSQL advisory lock（`backend/app/workers/scheduler.py:158-225`、`app/db/locks.py`）。

外部依赖只有五类：KeepRisk 标讯网关、KeepRisk 企业数据 MCP、OpenAI 兼容 LLM/Judge、MiniMax VLM OCR、DeepSeek/MiniMax 联网搜索。MinerU 本地 OCR 不是外部系统，而是镜像内置的进程内组件（`backend/app/modules/tender/ocr.py:302-390`、`deploy/allin1/Dockerfile:49-69`）。

部署上只有一个构建产物 `bidcopilot:single`，三种 manifest 消费同一镜像：allin1 单容器（nginx + Next + FastAPI + PostgreSQL + Redis + MinIO）、external 数据层分离、Helm 单 Deployment 多副本（`docs/adr/ADR-0001-deployment-topology.md:20-26`、`deploy/allin1/docker-compose.yml:7-25`、`deploy/helm/bidcopilot/values.yaml:5-22`）。

## 2. 角色与权限边界

角色模板定义在 `backend/app/modules/identity/roles.py:9-96`，共 5 个系统角色：

| 角色 | 关键能力 | 证据 |
|---|---|---|
| （企业版）平台管理员 `platform_admin` | `platform:*`、审计读取 | 企业版 overlay `identity/roles_extra.py` |
| 企业管理员 `tenant_admin` | 用户/角色/组织、资产、商机、项目、知识库、模型与密钥、审计 | `identity/roles.py:14-40` |
| 投标负责人 `bid_manager` | 商机、项目、知识读写、模板、模型使用 | `identity/roles.py:41-57` |
| 标书工程师 `bid_engineer` | 项目读写、知识读写、模板、模型使用 | `identity/roles.py:58-75` |
| 合规/法务 `compliance` | 项目审批、合规复核/破玻璃/导出、审计 | `identity/roles.py:76-95` |

权限执行方式：JWT 认证（`core/jwt.py:26,48`、`identity/deps.py:19`），路由通过 `require_permission` / `require_permission_or_org` 校验能力（`identity/deps.py:40-68`）；组织域角色由 `UserRole.scope_type/scope_id` 表达，登录时汇总为组织能力（`identity/service.py:43-120`）。资源级 ACL 表用于知识库/文档/模板等（`access/models.py:11`、`access/service.py:21`、`knowledge/access.py:55`），资产按组织可见性过滤（`assets/access.py:15`），字段策略在输出时脱敏（`assets/field_policies.py`）。

**边界事实**：仓库中未发现项目级成员表或项目级 ACL（检索 `project_acl|project_member|ProjectAcl|ProjectMember` 无结果）。项目访问目前由“租户 + 能力”控制；跨租户按 ID 读取返回 404 由 `TenantRepository.get_or_404` 保证（`db/repository.py:11-21`）。这是模型中的已知边界，是否属于产品缺口需业务确认，本文不将其升级为已实现的项目级权限。

## 3. 业务能力地图（L0/L1）

| 能力 | 主要模块 | 入口与证据 |
|---|---|---|
| 商机发现与匹配 | opportunities、company_intel、gateway | `opportunities/router.py`、`subscriptions.py:14-26`、`auto_match.py:24-48`、`buyer_intel.py:12-18` |
| 企业资产与知识 | assets、knowledge | `assets/router.py:67-289`、`knowledge/router.py:104`、`knowledge/kb.py:19-39` |
| 企业情报与关联核验 | company_intel、gateway | `company_intel/intel_router.py`、`gateway/keeprisk_mcp.py`、`gateway/enterprise_adapter.py` |
| 租户内隔离 | assets、projects、knowledge、access | 部门（`org_units`）+ 角色 + 资源 ACL + 字段策略；**不提供多客户模式**（多客户能力已由迁移 `0068_retire_client_scope` 下线） |
| 情报监控订阅 | company_intel、workers | `company_intel/watchlist_service.py`、`workers/scheduler.py`（advisory lock） |
| 套餐与用量 | platform | `platform/plan_service.py`、`platform/router.py`（项目/席位/AI 篇数/字数上限） |
| 交标回执 | projects | `projects/receipts.py`、`submission_receipts`（人工回执 + 自查快照） |
| 知识库冷启动/交付品牌 | knowledge、projects | 批量 ingest + 结构模板草稿自动生成；导出品牌取 `project.company_id → Company.name`，不做白标 |
| 文件体检 | file_check、tender | 10 条买方规则 + 4-gram Jaccard 相似度（`modules/file_check/`） |
| 投标项目与招标解析 | projects、tender | `projects/router.py:113-206`、`projects/service.py:25-62`、`tender/parser.py`、`tender/ocr.py` |
| 标书生成与导出 | generation | `generation/router.py:101-179`、`generation/service.py:21-49` |
| 合规预检与逐条响应 | compliance | `compliance/router.py:54`、`compliance/service.py:11-34`、`compliance/registry.py:22-25`、`backend/app/data/rules/compliance_rules.json`（29 条） |
| AI 评标 | evaluation | `evaluation/router.py:42`、`evaluation/service.py:11-22` |
| 报价策略与报价单 | pricing | `pricing/router.py:31`、`pricing/service.py:466`、`pricing/quote.py`、`pricing/price_band.py` |
| 合同审查与履约 | contracts | `contracts/router.py:105`、`contracts/lifecycle_router.py:64`、`contracts/service.py` |
| 复盘与运营 | projects/analytics、costs、audit | `projects/analytics.py`、`costs/router.py:9`、`audit/service.py:99` |
| 平台与租户治理 | identity、access、org、platform、system、notifications | `platform/service.py:10-48`、`system/router.py:40`、`notifications/service.py` |

前端路由与能力基本一一对应：`/opportunities`、`/projects/[id]`（标书工作区）、`/contracts`、`/assets`、`/knowledge`、`/templates`、`/policies`、`/data-ops`、`/audit`、`/users`、`/access`、`/admin/tenants`、`/help`（`frontend/src/app/(app)/*/page.tsx`）。`/research` 307 到 `/opportunities?source=web`（`frontend/next.config.mjs:5-13`）。

## 4. 端到端主线：商机 → 合同 → 复盘

以下步骤是**代码路径确认**的结构化主线；运行态验收见 README 的历史快照，本轮未复测。

1. **同步商机**：手动同步或订阅 tick 调用 KeepRisk 适配器，写入商机与 `source_sync_runs`，成功/失败均落 `cost_ledger`（`opportunities/router.py:15-19`、`subscriptions.py:14-26`、`gateway/keeprisk_adapter.py`）。
2. **匹配与立项**：按企业资质自检与命中项打分，阈值内可幂等自动创建 `BidProject`（`opportunities/auto_match.py:24-48`；资质读取 `assets/repository.py`）。
3. **上传与解析招标文件**：项目工作区上传 docx/pdf/txt/md，解析任务经 `ParseJob` 异步执行；解析器抽取约束集、评分项、资格、废标与预算，扫描件走 OCR（`projects/router.py:89-90`、`projects/service.py:53-62`、`tender/parser.py`、`tender/ocr.py`）。
4. **生成章节**：按项目与评分项生成大纲/章节，检索知识库证据并写 `evidence_links`，事实核查后可审批，导出 docx/暗标（`generation/service.py:21-49`、`generation/router.py:101-179`）。
5. **合规预检与逐条响应**：29 条确定性规则（数据源 `backend/app/data/rules/compliance_rules.json`，version 2026-10-25.4，经 `compliance/registry.py:22-25` 加载）+ 租户策略生成报告与 findings；逐条响应/偏离表需人工确认；导出受合规闸门约束（`compliance/service.py:11-34`、`compliance/router.py:54`）。
6. **AI 评标**：按评分项对章节打分并产出 findings 报告（`evaluation/service.py:11-22`、`evaluation/router.py:42`）。
7. **报价策略**：成本模型 + 历史价格带 + 敏感性 + 三档建议价；未校准数据时显式 `insufficient_data`（`pricing/service.py:13-45`）。
8. **合同审查与履约**：合同文本解析、条款风险、三方一致性，合同承诺对照投标承诺；合同记录进入审批状态机、履约台账、变更/续签与到期提醒（`contracts/service.py:18-25`、`contracts/lifecycle_router.py:172`）。
9. **复盘**：记录中标/失标与赢率，进入数据运营与成本对账（`projects/analytics.py`、`costs/router.py:5-7`）。

## 5. 容器与运行形态

| 容器 | 技术 | 职责 | 证据 |
|---|---|---|---|
| nginx | nginx | 同源入口；`/api`、`/healthz` → FastAPI，`/` → Next；请求体上限 25m | `deploy/allin1/nginx.conf:1-35` |
| Web 工作台 | Next.js 14.2.21 / Node 22 | 页面与交互；客户端请求 `/api/v1`；`/api` rewrite 到 `BACKEND_URL` | `frontend/package.json:14-20`、`next.config.mjs:1-21` |
| API 服务 | Python 3.12 / FastAPI / SQLAlchemy async | 社区版 30 个 router；JWT + RBAC；进程内 APScheduler（8 job）；企业数据 MCP 适配（工商/信用/股权/尽调）；站内通知外推企业微信/邮件/客户 Webhook | `backend/app/main.py:66-96`、`workers/scheduler.py:158-225`、`gateway/keeprisk_mcp.py` |
| PostgreSQL | PostgreSQL 16 | 业务数据、审计链（新事件 v2 HMAC，断链可 repair-audit-chain 修复）、成本台账、advisory lock | `alembic/versions`（head 0077）、`db/locks.py`、`audit/service.py` |
| Redis | Redis 7 | 登录失败计数、`/readyz` 探活、refresh token jti 重放防护 | `identity/rate_limit.py`、`identity/refresh_store.py`、`main.py:112-141` |
| 对象存储 | MinIO / 本地目录 | 网关原始快照、知识文件/图片、生成中间物 | `gateway/snapshots.py:16-95` |

部署形态：

- **allin1（默认）**：单容器 `bidcopilot-app`，entrypoint 启动 PostgreSQL、Redis、MinIO、uvicorn、Next、nginx；启动时执行 `alembic upgrade head` 并按条件灌演示种子（`deploy/allin1/entrypoint.sh:32-140`、`docker-compose.yml:7-25`）。
- **external**：同一镜像 + 外部 PostgreSQL/Redis/MinIO，`BIDCOPILOT_EXTERNAL_SERVICES=1` 跳过内置服务（`deploy/external/docker-compose.yml:16-81`）。
- **Helm**：单 Deployment，默认 2 副本；`SCHEDULER_ENABLED=true`，多副本调度互斥靠 advisory lock；迁移走 pre-install/pre-upgrade Job（`deploy/helm/bidcopilot/values.yaml:11-22`、`templates/deployment.yaml:38-61`、`templates/migration-job.yaml:1-29`）。

逻辑容器图不表达三种形态的差异；部署落位与网络边界需要单独的部署拓扑分析。

## 6. 数据、状态与事务边界

- **租户隔离**：所有租户业务查询经 `TenantRepository.scoped()` 强制 `tenant_id`；按 ID 读取越租户返回 404（`db/repository.py:11-21`）。没有 session 级自动过滤，隔离依赖显式仓储调用。租户内部再叠加部门（`org_units`）+ 角色 + 资源 ACL + 字段策略；**不提供多客户/代理模式**（`client_id` 维度已由迁移 `0068_retire_client_scope` 删除）。
- **迁移**：77 个 Alembic 迁移，单链 head `0077_price_quotes`；只前进，共享环境禁止 destructive downgrade（AGENTS.md）。
- **审计**：`AuditService.append()` 用 `pg_advisory_xact_lock` 串行化，按 `prev_hash + payload` 上链——新事件 v2 HMAC-SHA256（密钥 JWT_SECRET_KEY），历史事件 v1 裸 SHA-256；提供链校验与受控修复 `repair_chain`（`audit/service.py:99/151/167`、`app/cli.py` repair-audit-chain）。
- **付费调用**：`paid_call_quotas` 原子预占/释放，`cost_ledger` 记录网关/搜索/LLM 调用；付费路径使用 advisory lock 防重复计费（`costs/quota.py:35,67`、`costs/service.py:21`、`opportunities/subscriptions.py`）。
- **后台任务**：8 个 job 均为“每次执行竞争 advisory lock，未获锁跳过”，因此 Helm 多副本可安全运行（`workers/scheduler.py:158-225`、`db/locks.py:10`）。
- **文件边界**：上传限制分散在 nginx 25m、知识库 20MB、资产导入 10MB；`config.py` 中没有统一上传大小配置项（`deploy/allin1/nginx.conf:7`、`knowledge/kb.py:48`、`assets/importer.py:20`）。

## 7. 外部集成与失败语义

- **KeepRisk**：只发送官方文档确认过的参数；`uid` 不是列表/详情过滤参数，详情用 `ids + contentFlag=1`，中标检索用 `InfoTypeList=["3"]`；正文 HTML 白名单清洗、URL scheme 校验、大小限制；截止时间不由源提供（`README.md:328-332`、`AGENTS.md`）。
- **KeepRisk 企业数据 MCP**：Streamable HTTP + `mcp-session-id`；工具调用统一映射 `DataResult`，`api_calls`/`billing_units` 与缓存命中口径入成本台账；token 只经环境变量（`gateway/keeprisk_mcp.py`、`gateway/enterprise_adapter.py`、`company_intel/intel_router.py`）。
- **DataResult**：外部数据结果带 `status`，必须原样透传到 API 与前端；非 `ok`/`empty` 不降级为空列表（`gateway/contract.py:30-52`、`AGENTS.md` 硬约束）。
- **LLM/Judge**：OpenAI 兼容 `/chat/completions`；未配置时相关功能明确失败而不是伪造结果；Judge 未配置时回退主模型并记录实际模型（`generation/llm_client.py:35-68`、`README.md:405`）。
- **OCR**：`OCR_PROVIDER ∈ auto/minimax/mineru/none`；auto 优先 MiniMax，否则本地 MinerU；本地通道支持 MinerU 全集输入，失败显式抛错（`tender/ocr.py:396-466`）。
- **联网搜索**：DeepSeek 或 MiniMax 提供方，经网关客户端调用并写成本台账与审计（`web_search/router.py:6-18`、`gateway/web_search.py:113`）。
- **通知**：站内消息 + 租户级外推渠道。站内通知写入后按渠道 best-effort 广播企业微信群机器人 Webhook（https）与 SMTP 邮件（收件人逗号分隔），单渠道失败只记 warning；渠道 CRUD 需 `tenant:settings:manage`，SMTP 参数只经环境变量/Secret 注入（`notifications/channels.py`、`notifications/channel_router.py`、`notifications/service.py:11`）。

## 8. 未知与验证任务

| 未知/边界 | 现状证据 | 验证方式 |
|---|---|---|
| Helm 首次 install 冒烟 | README 记录 `helm lint/template` 通过，首次 install 待 K8s 环境（`README.md:167`、ADR 验证要求） | 在有 K8s 的环境执行 install，记录 `/healthz` 与迁移 Job 输出 |
| 运行态端到端未在本轮复测 | 本文以代码路径为高置信证据 | 用 `scripts/run_local_site.sh` + `scripts/smoke_demo.py` 复跑并记录输出 |
| 项目级 ACL/成员 | 未发现项目级 ACL 表；知识与资产有资源 ACL | 与产品确认是否预期；若需要，验证授权路径与回归 |
| SaaS 计费与租户生命周期 | `platform` 仅创建租户 + 企业管理员；无计费/订阅模块 | 检查产品规划；确认是否属于目标态 |
| 前端路由外壳权限 | 无权限角色直接访问 `/users`、`/access` 时外壳仍渲染，后端 403（`README.md:88`） | 浏览器复测并确认是否为纵深防御可接受边界 |
| 跨域部署 | 无 CORS 中间件；依赖 nginx 同源 | 若前后端分域部署，需验证 CORS 与 Cookie 策略 |
| MinerU GPU 档位 | 当前镜像为 CPU 构建，`auto` 恒收敛 basic | 需要 VLM/standard 档时验证 CUDA 构建变体 |
| MiniMax 实网配额 | 代码路径与错误码映射已确认，本轮未实网调用 | 配置有效 Key 后执行 `preflight.py --probe-ocr` 并记录计费/失败 |
| 企业数据 MCP 配额与缓存 | 已实测 5000 次/日、缓存命中 0 计费；上游策略可能调整 | 上线后监控 `data-ops` 的 `mcp:*` 台账与 429 告警 |
| 当前业务表数量 | 78 张（社区版口径，2026-11-19 实测；tenant_plans 属企业版） | 每次 head 变更后复测并记录 |

## 9. Agent 指令覆盖（Business Application Fitness）

`AGENTS.md` 已覆盖以下领域约束：禁止生产 Mock、外部数据只经 gateway、租户隔离与 404、`DataResult` 状态透传、KeepRisk 参数边界、HTML 清洗、截止时间口径、配置集中、迁移只前进、许可证边界、单一构建产物与后台执行模型、脚本 LF。

未覆盖或只给出部分口径的领域：项目级 ACL 是否应存在、站内通知之外的通知渠道、SaaS 计费/订阅生命周期、跨域部署假设、上传大小限制的统一配置口径。上述内容在本文中按未知项处理，不当作已实现能力。

## 10. 阅读顺序

1. 先看 `bidcopilot-containers.dot`：容器与外部依赖的全景。
2. 再看 `bidcopilot-system.structurizr.dsl` 的 `SystemContext` 与 `Containers` 视图：边界与技术选择。
3. 需要理解交付主线时看 `BidDeliveryCore` 组件视图；需要理解商机链路时看 `OpportunityFlow` 组件视图。
4. 事实核对该看 `system-model.evidence.md`；下一步做流程或部署专项时看 `system-model.summary.md` 的路由建议。

在 Qoder 中用 Structurizr DSL 查看器打开 `.structurizr.dsl`，用 DOT 查看器打开 `.dot`。两者均为源码；渲染结果只是派生视图。
