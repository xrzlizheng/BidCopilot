# 架构产物一致性复核报告（architecture-health）

- 日期：2026-10-30
- 复核对象：`docs/architecture/` 下 `bidcopilot-system.structurizr.dsl`、`bidcopilot-containers.dot`、交互 HTML 可视化、`architecture-understanding.md`、`system-model.evidence.md`、`system-model.summary.md`
- 对照证据：`backend/app`、`backend/alembic`、`frontend/src`、`deploy/*`、`backend/app/data/rules/compliance_rules.json`
- 方法：批量抽取模型引用的行号与原文比对（awk 逐行）、计数核对（grep/find/python）、跨产物边数与边界成员核对、浏览器内量 DOM 校验 HTML 渲染

## 1. 计数核对（全部确认）

| 断言 | 模型值 | 实测 | 结果 |
|---|---|---|---|
| FastAPI router 注册数 | 32 | `grep -c "app.include_router" backend/app/main.py` = 32 | 一致（§7 后 +4：api-tokens/webhooks/ai-usage/decision-evidence） |
| 业务模块数 | 21 | `ls backend/app/modules` = 21 | 一致 |
| Alembic 迁移数 / head | 67 / 0067 | 67 个 `.py`，末位 `0067_webhooks` | 一致 |
| 调度 job 数 | 5 | `grep -c "scheduler.add_job"` = 5 | 一致 |
| 系统角色数 | 5 | `roles.py` 5 个键 | 一致 |
| 前端页面路由数 | 19 | `find frontend/src/app -name page.tsx` = 19 | 一致 |
| 合规规则数 | 29 | `compliance_rules.json` rules = 29（version 2026-10-25.4） | 一致，且证据由 README 口径升级为数据文件 |
| Redis 用途 | 仅登录限流 + /readyz | 全仓 `Redis` 引用仅 `main.py:109` 与 `identity/rate_limit.py:12,14` | 一致 |
| CORS 中间件 | 无 | `grep CORSMiddleware/add_middleware` 无结果 | 一致 |
| 项目级 ACL | 无 | `project_acl|project_member|ProjectAcl|ProjectMember` 无结果 | 一致 |
| 邮件/Webhook 通知 | 无 | `smtp|sendgrid|webhook` 无结果 | 一致 |

## 2. 行号引用抽查（约 120 处，6 处错位，已修正）

| 原引用 | 实测 | 处置 |
|---|---|---|
| `audit/service.py:20`（append） | 20 为空行；`class AuditService` 在 17、`append` 在 21、`verify_chain` 在 73 | 改为 `17-73` |
| `costs/quota.py:32`（预占） | 32 为 `return periods`；`reserve_paid_call` 在 35、`release_paid_call` 在 67 | 改为 `35,67` |
| `gateway/keeprisk_client.py:55`（query） | 55 为赋值行；`query` 在 66、类在 41 | 改为 `66` |
| `gateway/web_search.py:178`（搜索实现） | 178 为 `_search_minimax`；客户端类在 80、`search` 在 111 | 改为 `80,111` |
| `core/jwt.py:11`（签发） | 11 为 `pass`；`create_access_token` 在 26、`create_refresh_token` 在 48 | 改为 `26,48` |
| `access/service.py:22`（判权入口） | 22 为 `@staticmethod`；`class AuthzService` 在 21 | 改为 `21` |

其余抽查全部命中（含 `main.py:46/59/82/93/98`、`roles.py` 五个角色起始行、`db/repository.py:11/18/21`、`db/locks.py:10`、`gateway/contract.py:30/52`、`tender/ocr.py:116/302/396/466`、`knowledge/kb.py:19/39/48`、`assets/importer.py:20`、`identity/router.py:51/103/144`、`frontend` 四个鉴权/代理文件、deploy 四份 manifest）。

## 3. 跨产物一致性

- DSL 关系总数 58；按视图过滤后：SystemContext 7、Containers 13、BidDeliveryCore 21、OpportunityFlow 13。
- HTML 可视化四个页签实测边数 7/13/21/13，与 DSL 逐视图一致；节点零重叠；系统边界框成员校验通过（使用者与外部系统均在框外）。
- DOT 容器图 13 条边与 DSL Containers 视图一致。
- **发现并已修正**：① DSL 原先缺少系统级关系（person→system、system→external），SystemContext 视图会渲染成无边孤岛；已补 7 条系统级关系，并同步补 5 条证据行（`person.*` 通配覆盖 3 条使用者关系），与 HTML 上下文视图对齐。② 证据边表 `nginx` 与模型标识 `nginxProxy` 命名漂移，已统一为 `nginxProxy`。
- Traceability 门禁：35/35 DSL 元素均有对应证据行；60 条 DSL 关系与 56 条证据边行双向匹配（`person.*` 通配覆盖 3 条），无孤儿元素、无未引用边，所有节点与边均带可解析 sourceRef。

## 4. 业务口径核对

- “29 条确定性合规规则”由 README 口径升级为代码数据证据（`backend/app/data/rules/compliance_rules.json`，29 条，version 2026-10-25.4，经 `compliance/registry.py:22-25` 加载）。
- 上传限制三处口径确认：nginx 25m（`deploy/allin1/nginx.conf:7`）、知识库 20MB（`knowledge/kb.py:48`）、资产导入 10MB（`assets/importer.py:20`）。
- OCR 供应商选择逻辑确认：`tender/ocr.py:396-466`（auto 优先 MiniMax，否则本地 MinerU）。
- 租户隔离、审计链、付费配额、DataResult 透传等横切机制的行号引用已按第 2 节修正后全部可解析。

## 5. 仍为 unknown（模型已显式标注，非本轮可闭合）

1. Helm 首次 install 冒烟（ADR-0001 验证要求未闭合）。
2. 运行态端到端未在本轮复测（README 快照为文档证据）。
3. 项目级 ACL 是否为产品预期，需业务确认。
4. SaaS 计费/租户生命周期仅有建租户+建管理员。
5. 跨域部署假设（无 CORS 中间件）。
6. MiniMax 实网配额、MinerU GPU 档位、当前业务表数量。

## 6. 可信度结论与后续校验

- 结构类断言：高可信（计数与行号引用全部可解析）。
- Traceability：DSL 元素 35/35、关系 60/60 均可回溯到证据行；证据表节点与边全部带 sourceRef。
- 运行态断言：中可信（文档证据，未复测）。
- 图与文本口径一致，未知项在两处均显式标注。
- 建议的活架构校验（未实现，留作后续）：在 `scripts/` 增加一个引用校验脚本，输入为 evidence 表的 `path:line` 列表，断言每行包含预期符号、计数断言（router/模块/迁移/job/角色/规则数）不变；接入 CI 后模型与代码漂移即可被发现。

## 7. 增量复核（2026-10-31）

- 新增外部系统「KeepRisk 企业数据 MCP」及 `apiService → MCP` 关系（企业情报：工商/信用/股权/尽调/批量/对比）。
- 当前 DSL：35 个元素、60 条关系；按视图过滤后 SystemContext 8、Containers 14、BidDeliveryCore 21、OpportunityFlow 13。证据表已补对应节点与边，其余结论与未知项不变。
- 运行态证据：全量 `pytest tests\unit tests\integration -q` → 782 passed / 2 skipped；`e2e_release_check` 14 PASS / 0 FAIL；`e2e_functional_sweep` 42 PASS / 0 FAIL；批量/对比/采购人情报/合同相对方/监控订阅/套餐额度/交标回执/交付品牌/文件体检均完成浏览器或集成验收。
- HTML 可视化是派生快照：需按新 DSL 重新生成（上下文 8 / 容器 14），旧快照不作为事实来源。

## 8. 增量复核（2026-10-31，通知渠道切片后）

| 断言 | 旧值（§7 前） | 实测 | 处置 |
|---|---|---|---|
| FastAPI router 注册数 | 32 | `grep -c "app.include_router" backend/app/main.py` = 32 | 一致（+api-tokens/webhooks/ai-usage/decision-evidence） |
| 业务模块数 | 21 | `ls backend/app/modules` = 25 | 模型更新 |
| Alembic 迁移数 / head | 67 / 0067 | 67 个 `.py`，末位 `0067_webhooks` | 一致 |
| 调度 job 数 | 5 | `grep -c "scheduler.add_job" backend/app/workers/scheduler.py` = 6 | 模型更新（+情报监控） |
| 前端页面路由数 | 19 | `find frontend/src/app -name page.tsx` = 22 | 模型更新 |
| 邮件/Webhook 通知 | 无 | `notifications/channels.py`：企业微信 Webhook + SMTP 邮件，租户级渠道表 | 断言翻转，见下 |
| 合规规则数 | 29 | `compliance_rules.json` = 29（version 2026-10-25.4） | 仍一致 |
| 系统角色数 | 5 | `roles.py` 5 个键 | 仍一致 |

- 通知渠道为租户级外部推送：站内通知提交后按渠道 best-effort 广播，单渠道失败只记 warning；SMTP 参数只经环境变量/Secret 注入，`smtp|webhook` 首次出现在 `app/config.py` 与 `notifications/channels.py`。
- 运行态证据：单测 465+ passed / 集成 133+ passed（全量 778 passed / 1 skipped，含并发代理切片）（真实 PG 55432 + Redis 6380）；ruff 0.17.1 全量通过（含清理 18 处存量 I001/F401/UP035/UP032）；mypy 与 HEAD 基线持平（51 处存量，本轮不新增）。
- 待办：HTML 可视化快照与 `system-model.*` 的计数行需按本节刷新。

---

## 9. 2026-11-17 增量（追加节，上文为 2026-10-30 快照不回改）

- 计数刷新（实测）：`include_router` 社区版 **30**（§1 的 32 已过期；企业版 platform 经 try-import 为 31）、`app/modules` **22** 个、社区版迁移 **76** 个 / head `0077_price_quotes`（tenant_plans 已移出社区版链）、调度 job **8** 个（新增 ai-run-recovery、webhook-outbox-retry；`build_scheduler` 现 `workers/scheduler.py:155`）、前端 page.tsx **21** 个、业务表 **78** 张（社区版口径；tenant_plans 属企业版）。
- 模型源已同步：containers.dot / structurizr DSL 的 router/迁移/job 计数标签、Redis 用途（+refresh 重放防护）、审计（v2 HMAC + repair）、pricingStrategy（+报价单）、runtimeSettings（+pricing-settings）均已更新；evidence 表新增/修正对应证据行并标注锚点刷新口径。
- 新增能力（§7 待办之外的增量）：报价单闭环（0077 + 三端点 + 前端面板）、报价成本参数配置（system/pricing_settings.py）、审计链受控修复（repair-audit-chain，演示租户历史断链已修复，/audit/verify valid=true）、Webhook 投递 outbox（0076）。
- 版本分拆（2026-11-19）：多租户运营（platform 模块/套餐计量/平台管理员）从社区版摘除为企业版闭源 overlay；社区版引入 quota/roles/caps/cli/nav/help 六个 seam（try-import 或空实现覆盖）。
- 宿主端口 18080/25432/6390 已落位 compose（55432 落 Windows 保留段、6380 Docker 分配器泄漏）。
- 仍未执行：Helm 首次 install 冒烟（ADR-0001 验证要求）；evidence 表剩余历史行号锚点未逐行重验（口径已在文件头声明）。
