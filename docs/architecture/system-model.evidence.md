# 系统模型证据索引

- 建模日期：2026-10-30；**2026-11-17 刷新**：计数类断言（router/模块/迁移/job/表数）与新增能力证据行已更新；未逐行重验的历史行号锚点以「符号定位 + grep 复核」为准，如与代码不符以代码为准并更新本表。
- 模型源码：`docs/architecture/bidcopilot-system.structurizr.dsl`
- 图源码：`docs/architecture/bidcopilot-containers.dot`
- 置信度：high = 代码/配置/迁移直接确认；medium = 多信号一致或文档记录但本轮未复测；low = 目录/命名推断；unknown = 有意标注待查
- 路径均为仓库相对路径；行号来自直接读取或符号定位

## 1. 节点

| ID | 名称 | 类型 | 状态 | 置信度 | 证据 |
|---|---|---|---|---|---|
| person.platformAdmin | 平台管理员（企业版） | actor | enterprise-only | high | 企业版 overlay `identity/roles_extra.py` |
| person.tenantAdmin | 企业管理员 | actor | current | high | `backend/app/modules/identity/roles.py:14-40` |
| person.bidTeam | 投标团队 | actor | current | high | `backend/app/modules/identity/roles.py:41-95` |
| system.bidcopilot | BidCopilot 平台 | software-system | current | high | `docs/adr/ADR-0001-deployment-topology.md:20-26`；`README.md:306-358` |
| container.nginxProxy | nginx 反向代理 | container | current | high | `deploy/allin1/nginx.conf:1-35`；`deploy/allin1/entrypoint.sh:93` |
| container.webApp | Web 工作台 | container | current | high | `frontend/package.json:14-20`；`frontend/src/lib/api.ts:39-63`；`frontend/next.config.mjs:1-21` |
| container.apiService | API 服务 | container | current | high | `backend/app/main.py:46-82`；`backend/app/workers/scheduler.py:106-153` |
| container.db | PostgreSQL 16 | database | current | high | `backend/alembic/versions/`（76 个迁移，head `0077_price_quotes`，社区版链）；`backend/app/db/repository.py:11-21`；`backend/app/db/locks.py:10` |
| container.redis | Redis 7 | database | current | high | `backend/app/modules/identity/rate_limit.py`；`backend/app/main.py:98-127` |
| container.storage | 对象存储 | database | current | high | `backend/app/modules/gateway/snapshots.py:16-95`；`backend/app/modules/knowledge/kb.py:19-32` |
| system.keeprisk | KeepRisk 标讯网关 | external-system | current | high | `backend/app/modules/gateway/keeprisk_client.py:66`；`gateway/keeprisk_adapter.py:73`；`gateway/company_adapter.py:152` |
| system.keepriskMcp | KeepRisk 企业数据 MCP | external-system | current | high | `backend/app/modules/gateway/keeprisk_mcp.py`；`gateway/enterprise_adapter.py`；`company_intel/intel_router.py`；`config.py` 的 `KEEPRISK_MCP_*` |
| system.llmGateway | LLM / Judge 网关 | external-system | current | high | `backend/app/modules/generation/llm_client.py:35-68`；`backend/app/config.py:31-48` |
| system.minimaxOcr | MiniMax VLM OCR | external-system | current | high | `backend/app/modules/tender/ocr.py:116-217`；`backend/app/config.py:58-61` |
| system.searchApi | 联网搜索 API | external-system | current | high | `backend/app/modules/gateway/web_search.py:80,111`；`backend/app/config.py:73-80` |
| component.bidProjects | 投标项目工作区 | module | current | high | `backend/app/modules/projects/router.py:113-206`；`projects/service.py:25-62`；`projects/models.py` |
| component.tenderParsing | 招标文档解析 | module | current | high | `backend/app/modules/tender/parser.py`；`tender/bid_risks.py`；`tender/ocr.py` |
| component.mineruOcr | MinerU 本地 OCR | module | current | high | `backend/app/modules/tender/ocr.py:302-390`；`deploy/allin1/Dockerfile:49-69` |
| component.knowledgeBase | 知识库与检索 | module | current | high | `backend/app/modules/knowledge/router.py:104`；`knowledge/kb.py:19-39`；`knowledge/access.py:55` |
| component.bidGeneration | 标书生成 | module | current | high | `backend/app/modules/generation/router.py:101-179`；`generation/service.py:21-49` |
| component.complianceCheck | 合规预检 | module | current | high | `backend/app/modules/compliance/router.py:54`；`compliance/service.py:11-34`；`compliance/registry.py:22-25`；`backend/app/data/rules/compliance_rules.json`（29 条，version 2026-10-25.4） |
| component.aiEvaluation | AI 评标 | module | current | high | `backend/app/modules/evaluation/router.py:42`；`evaluation/service.py:11-22` |
| component.pricingStrategy | 报价策略与报价单 | module | current | high | `backend/app/modules/pricing/router.py:31`；`pricing/service.py:466`；`pricing/quote.py`（报价单快照/导出）；`pricing/price_band.py` |
| component.contractLifecycle | 合同审查与履约 | module | current | high | `backend/app/modules/contracts/router.py:105`；`contracts/lifecycle_router.py:64`；`contracts/service.py` |
| component.enterpriseAssets | 企业资产 | module | current | high | `backend/app/modules/assets/router.py:67-289`；`assets/service.py:12-34`；`assets/models.py` |
| component.opportunityHub | 商机中心 | module | current | high | `backend/app/modules/opportunities/router.py:15-26`；`opportunities/subscriptions.py:14-26`；`opportunities/auto_match.py:24-48` |
| component.companyIntel | 企业标讯画像 | module | current | high | `backend/app/modules/company_intel/router.py:8-21`；`company_intel/service.py:12-20` |
| component.dataGateway | 外部数据网关 | module | current | high | `backend/app/modules/gateway/deps.py:2-6`；`gateway/contract.py:30-52`；`gateway/normalize.py` |
| component.webSearch | 联网检索 | module | current | high | `backend/app/modules/web_search/router.py:6-18`；`gateway/web_search.py:178` |
| component.identityAuthz | 身份与授权 | module | current | high | `backend/app/modules/identity/deps.py:40-68`；`identity/service.py:43-120`；`access/service.py:21`；`access/models.py:11` |
| component.platformTenant | 平台租户开通（企业版闭源模块） | module | enterprise-only | high | 不在社区版仓库；见企业版 overlay |
| component.runtimeSettings | 运行时设置（含报价成本参数） | module | current | high | `backend/app/modules/system/router.py:40`；`system/pricing_settings.py`（pricing-settings 读写）；`system/secrets.py`；`system/url_policy.py` |
| component.costLedger | 付费调用台账 | module | current | high | `backend/app/modules/costs/router.py:5-7`；`costs/service.py:21`；`costs/quota.py:35,67` |
| component.auditLog | 审计（v2 HMAC 链 + 受控修复） | module | current | high | `backend/app/modules/audit/service.py:99`（append）、`:151`（verify_chain）、`:167`（repair_chain）；`app/cli.py`（repair-audit-chain）；`audit/router.py` |
| component.notificationsCenter | 站内消息 | module | current | high | `backend/app/modules/notifications/router.py:13`；`notifications/service.py:11` |

补充节点：APScheduler 不是独立容器，而是 `container.apiService` 进程内组件，模型以“API 服务”描述中的调度职责表达（`backend/app/workers/scheduler.py:106-153`）。

## 2. 关系

| ID | From → To | 类型 | 置信度 | 证据 |
|---|---|---|---|---|
| edge.users-nginx | person.* → container.nginxProxy | calls | high | `deploy/allin1/nginx.conf:1-35`；`deploy/helm/bidcopilot/values.yaml:12-27` |
| edge.users-system | person.* → system.bidcopilot | calls | high | `backend/app/modules/identity/roles.py:9-96`；`backend/app/main.py:58-82` |
| edge.system-keeprisk | system.bidcopilot → system.keeprisk | calls | high | `backend/app/modules/opportunities/subscriptions.py:14-26`；`gateway/keeprisk_client.py:66` |
| edge.system-keepriskMcp | system.bidcopilot → system.keepriskMcp | calls | high | `backend/app/modules/company_intel/intel_router.py`；`gateway/keeprisk_mcp.py` |
| edge.system-llm | system.bidcopilot → system.llmGateway | calls | high | `backend/app/modules/generation/llm_client.py:35-68`；`evaluation/router.py:18-20` |
| edge.system-minimax | system.bidcopilot → system.minimaxOcr | calls | high | `backend/app/modules/tender/ocr.py:116-217` |
| edge.system-search | system.bidcopilot → system.searchApi | calls | high | `backend/app/modules/gateway/web_search.py:111` |
| edge.nginx-web | nginxProxy → webApp | routes-to | high | `deploy/allin1/nginx.conf:29-35` |
| edge.nginx-api | nginxProxy → apiService | routes-to | high | `deploy/allin1/nginx.conf:13-27` |
| edge.web-api | webApp → apiService | calls | high | `frontend/next.config.mjs:1-21`；`frontend/src/lib/api.ts:39-63` |
| edge.api-db | apiService → db | reads/writes | high | `backend/app/db/repository.py:11-21`；`db/session.py` |
| edge.api-redis | apiService → redis | calls | high | `backend/app/modules/identity/rate_limit.py`；`main.py:98-127` |
| edge.api-storage | apiService → storage | reads/writes | high | `backend/app/modules/gateway/snapshots.py:16-95`；`knowledge/kb.py:19-32` |
| edge.api-keeprisk | apiService → keeprisk | calls | high | `opportunities/subscriptions.py:14-26`；`company_intel/router.py:18-19`；`workers/scheduler.py:20-33` |
| edge.api-keepriskMcp | apiService → keepriskMcp | calls | high | `company_intel/intel_router.py`；`gateway/enterprise_adapter.py` |
| edge.api-llm | apiService → llmGateway | calls | high | `generation/service.py:21-49`；`evaluation/router.py:18-20`；`contracts/service.py`；`pricing/service.py:13-45` |
| edge.api-minimax | apiService → minimaxOcr | calls | high | `tender/ocr.py:116-217,438-466` |
| edge.api-search | apiService → searchApi | calls | high | `gateway/web_search.py:80,111`；`web_search/router.py:6-18` |
| edge.projects-tender | bidProjects → tenderParsing | calls | high | `projects/service.py:53-62`；`projects/router.py:89-90` |
| edge.projects-assets | bidProjects → enterpriseAssets | reads | high | `projects/service.py:25-28`；`projects/self_check.py:7` |
| edge.projects-notifications | bidProjects → notificationsCenter | calls | high | `workers/scheduler.py:54-70`；`projects/reminders.py` |
| edge.tender-mineru | tenderParsing → mineruOcr | calls | high | `tender/ocr.py:302-390` |
| edge.tender-minimax | tenderParsing → minimaxOcr | calls | high | `tender/ocr.py:187-217,456-466` |
| edge.knowledge-tender | knowledgeBase → tenderParsing | calls | high | `knowledge/kb.py:39` |
| edge.knowledge-storage | knowledgeBase → storage | writes | high | `knowledge/kb.py:19-32` |
| edge.generation-knowledge | bidGeneration → knowledgeBase | reads | high | `generation/service.py:43-45` |
| edge.generation-projects | bidGeneration → bidProjects | reads | high | `generation/service.py:48-49` |
| edge.generation-storage | bidGeneration → storage | writes | high | `generation/service.py:22` |
| edge.generation-llm | bidGeneration → llmGateway | calls | high | `generation/llm_client.py:35-68` |
| edge.compliance-projects | complianceCheck → bidProjects | reads | high | `compliance/service.py:27-28` |
| edge.compliance-knowledge | complianceCheck → knowledgeBase | reads | high | `compliance/service.py:21-26` |
| edge.compliance-generation | complianceCheck → bidGeneration | calls | high | `compliance/service.py:18` |
| edge.compliance-assets | complianceCheck → enterpriseAssets | reads | high | `compliance/service.py:11-12` |
| edge.evaluation-generation | aiEvaluation → bidGeneration | calls | high | `evaluation/service.py:12-20` |
| edge.evaluation-projects | aiEvaluation → bidProjects | reads | high | `evaluation/service.py:21-22` |
| edge.evaluation-llm | aiEvaluation → llmGateway | calls | high | `evaluation/router.py:18-20` |
| edge.pricing-projects | pricingStrategy → bidProjects | reads | high | `pricing/service.py:43-44` |
| edge.pricing-llm | pricingStrategy → llmGateway | calls | high | `pricing/service.py:14` |
| edge.contracts-tender | contractLifecycle → tenderParsing | calls | high | `contracts/service.py:24-25` |
| edge.contracts-projects | contractLifecycle → bidProjects | reads | high | `contracts/service.py:24` |
| edge.contracts-notifications | contractLifecycle → notificationsCenter | calls | high | `contracts/service.py:823`；`workers/scheduler.py:35-51` |
| edge.opportunities-gateway | opportunityHub → dataGateway | calls | high | `opportunities/router.py:17-19`；`opportunities/subscriptions.py:16-17` |
| edge.opportunities-assets | opportunityHub → enterpriseAssets | reads | high | `opportunities/auto_match.py:24-25`；`opportunities/buyer_intel.py:12-13` |
| edge.opportunities-projects | opportunityHub → bidProjects | writes | high | `opportunities/auto_match.py:46-47` |
| edge.opportunities-notifications | opportunityHub → notificationsCenter | calls | high | `opportunities/subscriptions.py:18-19` |
| edge.opportunities-costs | opportunityHub → costLedger | calls | high | `opportunities/router.py:16`；`opportunities/subscriptions.py:15` |
| edge.companyIntel-gateway | companyIntel → dataGateway | calls | high | `company_intel/router.py:18-19`；`company_intel/service.py:14-20` |
| edge.companyIntel-assets | companyIntel → enterpriseAssets | reads/writes | high | `company_intel/service.py:12-13` |
| edge.companyIntel-costs | companyIntel → costLedger | calls | high | `company_intel/router.py:16-17` |
| edge.gateway-keeprisk | dataGateway → keeprisk | calls | high | `gateway/keeprisk_client.py:66`；`gateway/keeprisk_adapter.py:73` |
| edge.gateway-search | dataGateway → searchApi | calls | high | `gateway/web_search.py:111` |
| edge.gateway-storage | dataGateway → storage | writes | high | `gateway/snapshots.py:16-39`；`gateway/keeprisk_client.py:10` |
| edge.webSearch-gateway | webSearch → dataGateway | calls | high | `web_search/router.py:9` |
| edge.webSearch-costs | webSearch → costLedger | calls | high | `web_search/router.py:6-8` |
| edge.identity-audit | identityAuthz → auditLog | calls | high | `identity/router.py:12`；`identity/users.py:7` |
| edge.platform-identity | platformTenant → identityAuthz | calls | enterprise-only | — | 企业版 overlay |

未建模的关系：渲染/打印链路（PDF/Excel 导出）、前端导出下载（`frontend/src/lib/api.ts:80-137`）、`AuditService` 的跨模块显式调用（每个业务路由各自调用，边会爆炸，改为文字说明）。`assets` 复用 `knowledge.access.CLEARANCE_RANK` 的静态引用（`assets/field_policies.py:12`）是编译期耦合，不是运行时调用，故未画边。

## 3. 关键横切机制

| 机制 | 位置 | 说明 |
|---|---|---|
| 租户隔离 | `backend/app/db/repository.py:11-21`；`db/base.py:24` | `TenantRepository.scoped()` 强制 `tenant_id`；无 session 级自动过滤 |
| RBAC | `identity/roles.py:9-96`；`identity/deps.py:40-68`；`access/service.py:22` | 能力字符串 + 角色模板 + 组织域范围 |
| 资源 ACL | `access/models.py:11`；`knowledge/access.py:55`；`assets/access.py:15` | 知识库/文档/模板有资源 ACL；资产按组织可见性 |
| 审计哈希链 | `audit/service.py:17-73` | `pg_advisory_xact_lock` 串行化，`prev_hash + payload` 生成 SHA-256 链 |
| 调度互斥 | `workers/scheduler.py:20-153`；`db/locks.py:10` | 每次执行竞争 `pg_try_advisory_lock(hashtext(key))` |
| 付费配额 | `costs/quota.py:35,67`；`costs/service.py:21` | 原子预占/释放，失败不降级 |
| 外部数据契约 | `gateway/contract.py:30-52` | `DataResult.status` 原样透传，非 ok/empty 不降级 |
| 鉴权会话 | `core/jwt.py:26,48`；`identity/router.py:51-144`；`frontend/src/lib/api.ts:39-63` | access token 内存 + refresh cookie；登出递增 token_version |
| 企业数据 MCP | `gateway/keeprisk_mcp.py`；`gateway/enterprise_adapter.py`；`company_intel/intel_router.py` | Streamable HTTP 会话/SSE；`DataResult` 透传；`api_calls`/`billing_units`/缓存；配额预占与回冲 |

## 4. 未知与低置信项

| 项 | 置信度 | 说明 |
|---|---|---|
| Helm 首次 install 冒烟 | unknown | README 记录 lint/template 通过，install 待 K8s 环境 |
| 运行态端到端 | medium | 本轮未复测，README 验收快照为文档证据 |
| 项目级 ACL | unknown | 未发现项目级 ACL/成员表；产品是否预期待确认 |
| SaaS 计费/租户生命周期 | unknown | 仅创建租户与管理员，无计费模块 |
| 跨域部署 | unknown | 无 CORS 中间件；仅同源路径配置确认 |
| MiniMax 实网配额 | unknown | 代码与错误码映射确认，未实网调用 |
| MinerU GPU 档位 | medium | CPU 镜像 + basic 档确认；standard 档需 CUDA 变体 |
