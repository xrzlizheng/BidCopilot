# BidCopilot API 参考

> 数据来源：运行实例路由源码解析（FastAPI 0.156 lazy router，经 `original_router.routes` 展开）+ 源码 AST 核对。编写日期：2026-10-24；计数与端点清单按 2026-11-19 社区版实测刷新（259 路由；多租户运营端点属企业版，不在社区版）。
> 交互式文档：容器内 `http://127.0.0.1:8000/docs`（Swagger UI，nginx 未对外代理，需 `docker exec -it bidcopilot-app curl -s http://127.0.0.1:8000/openapi.json` 或临时映射端口访问）。
> 姊妹文档：[详细设计文档](2026-10-24-bidcopilot-detailed-design.md) §11 API 设计约定。

## 1. 总览

- **259 个操作**（含健康检查 2 个；方法分布：GET 124 / POST 88 / PATCH 22 / DELETE 18 / PUT 7）。
- 公开端点 5 个（login/refresh/logout/healthz/readyz），需登录 259 个；其中多数另有细粒度权限要求。
- 所有业务端点统一前缀 `/api/v1`；健康检查 `/healthz`（存活）与 `/readyz`（实探 DB+Redis，不可用 503）不在前缀下。

## 2. 全局约定

### 2.1 认证

- 请求头：`Authorization: Bearer <access_token>`。
- 登录 `POST /api/v1/auth/login`（body `{"email","password"}`）→ `{"access_token","token_type":"bearer","expires_in":900,"user":{...}}`。
- access TTL 900s；refresh TTL 7 天，经 HttpOnly Cookie `refresh_token` 下发（路径 `/api/v1/auth`、`SameSite=Lax`、生产 `Secure`）；`POST /api/v1/auth/refresh` 仅读 Cookie 轮换签发。
- 401 场景：缺失令牌 / 无效过期 / 用户不可用 / `token_version` 不匹配（改密、角色变更、停用即使全部旧令牌失效）。
- 当前用户权限清单：`GET /api/v1/auth/capabilities`。

### 2.2 权限模型

路由层 27 个 `require_permission` 字面量权限串（另有 `require_permission_or_org`：asset:read、knowledge:read/write/manage 与 `require_all_permissions` 组合；全集见 `backend/app/modules/access/capabilities.py`）：

`audit:read`、`user:manage`、`role:manage`、`role:assign`、`org:read`、`org:manage`、`asset:read`、`asset:write`、`asset:import`、`template:read`、`template:write`、`project:read`、`project:write`、`project:approve`、`project:export`、`opportunity:read`、`opportunity:write`、`opportunity:sync`、`model:use`、`model:manage`、`model:policy:manage`、`compliance:review`、`compliance:export`、`compliance:break_glass`、`tenant:read`、`tenant:settings:manage`。

- `require_permission(x)`：具备 x；`require_all_permissions(x,y)`：同时具备；`require_permission_or_org(x)`：租户级 x 或任一组织单元内的 x。
- **跨租户按 ID 读取一律 404**，不泄露存在性。

### 2.3 错误响应

统一错误体 `{"code","message"}`；注意 **422 有两类**：本表 `validation_error`（业务校验）与 FastAPI `HTTPValidationError`（参数校验，body 带 `detail` 数组）。

| code | HTTP | 含义 |
|---|---|---|
| `unauthorized` | 401 | 未认证/令牌无效/会话失效 |
| `forbidden` | 403 | 已登录但缺权限 |
| `not_found` | 404 | 资源不存在（含跨租户） |
| `conflict` | 409 | 状态冲突/唯一约束/并发互斥未获锁 |
| `validation_error` | 422 | 业务校验失败 |
| `internal_error` | 500 | 未分类内部错误 |
| `not_initialized` | 500 | 系统角色未初始化 |
| `generation_failed` / `evaluation_failed` | 502 | 生成/评标失败 |
| `llm_not_configured` / `service_unavailable` | 503 | LLM 未配置/依赖不可用 |

### 2.4 分页与列表

- `page`+`page_size`（offset，page 从 1）：`GET /audit/events`（默认 1/50，上限 200）、`GET /opportunities`（1/20，上限 100）、`GET /companies/{id}/bids`（1/20，上限 100）。响应信封 `{"items","total","page","page_size"}`。
- `limit`（无 page）：`GET /knowledge/search`（默认 4，1–20）、`GET /notifications`（默认 20，1–100）。
- 其余列表端点（documents、projects、contract-reviews 等）为全量返回，**勿默认可分页**。

### 2.5 文件上传

6 个上传端点均为 `multipart/form-data`，文件字段名统一 `file`：

| 端点 | 其他表单字段 |
|---|---|
| `POST /assets/import` | `kind`（必填） |
| `POST /knowledge/documents` | `title`/`kind`/`tags`/`company_id`/`kb_id` |
| `POST /knowledge/images` | `title`/`caption`/`tags`/`sensitive`/`company_id`/`kb_id` |
| `POST /projects/{id}/documents` | — |
| `POST /projects/{id}/contract-reviews` | — |
| `POST /projects/{id}/contract-reviews/{rid}/supplements` | — |

### 2.6 外部数据状态透传

商机同步、企业画像等经网关取数的端点，`status` 原样透传 `DataResult.status`：`ok/empty/unavailable/rate_limited/unauthorized/contract_error`；**非 ok/empty 不得降级为空列表**（AGENTS.md 硬约束）。时间 ISO-8601；金额单位元；UUID 路径参数。

---

## 3. 模块端点速查

### 3.1 认证 auth（6）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/auth/login` | 公开 | 登录，签发 access + refresh cookie |
| POST | `/auth/refresh` | 公开 | Cookie 轮换刷新 access |
| POST | `/auth/logout` | 公开 | 清除 refresh cookie |
| GET | `/auth/me` | 登录 | 当前用户（含权限/密级/组织） |
| GET | `/auth/capabilities` | 登录 | 当前用户权限清单 |
| POST | `/auth/change-password` | 登录 | 改密并使全部旧令牌失效 |

### 3.2 用户与角色 users（8）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/users` | user:manage | 租户用户列表 |
| POST | `/users` | user:manage | 建用户（email/password/display_name/role_codes） |
| PATCH | `/users/{user_id}` | user:manage | 改用户（display_name/status/role_codes/password） |
| PUT | `/users/{user_id}/role-assignments` | role:assign | 设置角色分配（支持 tenant/org_unit 作用域）；含最后管理员保护 |
| GET | `/roles` | user:manage | 角色列表（系统+自定义） |
| POST | `/roles` | role:manage | 建自定义角色（禁 `*`/platform 前缀、禁越权授权） |
| PATCH | `/roles/{role_id}` | role:manage | 改自定义角色（系统角色只读） |
| DELETE | `/roles/{role_id}` | role:manage | 删自定义角色（仍被分配则 409） |

### 3.3 组织架构 org（5）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/org-units/tree` | org:read | 组织单元树 |
| POST | `/org-units` | org:manage | 建部门/团队（name/kind/parent_id） |
| GET | `/org-units/{id}/members` | org:manage | 部门成员 |
| POST | `/org-units/{id}/members` | org:manage | 加成员（member_role=member/manager，is_primary 全局唯一主部门） |
| DELETE | `/org-units/{id}/members/{user_id}` | org:manage | 移出成员 |

### 3.4 资产库 assets（12）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/companies` | asset:read | 企业列表（q 过滤） |
| POST | `/companies` | asset:write | 建企业 |
| PATCH | `/companies/{company_id}` | asset:write | 改企业 |
| GET | `/assets/{kind}` | asset:read（或组织范围） | 资产列表（kind=qualifications/personnel/performances/credits；company_id/q 过滤；按字段策略脱敏） |
| POST | `/assets/{kind}` | asset:read（或组织范围） | 建资产（body 按 kind 动态 schema） |
| PATCH | `/assets/{kind}/{asset_id}` | asset:read（或组织范围） | 改资产 |
| DELETE | `/assets/{kind}/{asset_id}` | asset:read（或组织范围） | 删资产 |
| POST | `/assets/import` | asset:import | CSV/XLSX 导入（kind 必填） |
| GET | `/assets/expiring` | asset:read（或组织范围） | 临期资产（days 默认 90） |
| GET | `/asset-field-policies` | asset:write | 字段脱敏策略列表 |
| PUT | `/asset-field-policies` | asset:write | 设字段策略（min_clearance/mask_strategy=hide/mask/partial） |
| DELETE | `/asset-field-policies/{policy_id}` | asset:write | 删字段策略 |

### 3.5 企业画像 company-intel（15）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/companies/{company_id}/profile` | asset:read | 企业画像（KeepRisk，计费） |
| POST | `/companies/{company_id}/bids/sync` | asset:write | 同步中标记录（roles 默认 winner） |
| GET | `/companies/{company_id}/bids` | asset:read | 中标记录列表（分页） |
| GET | `/company-intel/resolve` | asset:read | 主体锚定（keyword/region → 候选企业） |
| GET | `/company-intel/profile` | asset:read | 工商照面（ent_name；1 次调用） |
| GET | `/company-intel/credit` | asset:read | 信用评分（6 次调用，四维评分+风险等级） |
| GET | `/company-intel/relations` | asset:read | 关联图谱（4 次调用；digest_only 默认 true） |
| POST | `/company-intel/due-diligence` | asset:read | 尽调快照（sections 按节计费；digest_only） |
| POST | `/company-intel/batch` | asset:read | 批量工商核验（1–100 家，1 次网关调用/批） |
| POST | `/company-intel/compare` | asset:read | 企业对比（2–5 家，约 10 次调用/家） |
| GET | `/company-intel/watchlist` | asset:read | 监控订阅列表（按客户可见性过滤） |
| POST | `/company-intel/watchlist` | asset:read | 加入监控（受 `INTEL_WATCHLIST_MAX_ITEMS` 限制） |
| PATCH | `/company-intel/watchlist/{id}` | asset:read | 更新标签/风险节/启用状态 |
| DELETE | `/company-intel/watchlist/{id}` | asset:read | 移除监控 |
| POST | `/company-intel/watchlist/{id}/run` | asset:read | 立即检查；风险变化发站内通知 |

企业数据 MCP 接口统一返回 `status`（`ok/empty/unavailable/rate_limited/unauthorized/contract_error`）、`data`、`charged`、`api_calls`、`billing_units`、`order_no`、`warnings`、`error`；状态原样透传，缓存命中时 `billing_units=0`。Token 只经 `KEEPRISK_MCP_TOKEN` 环境变量注入。

### 3.6 商机 opportunities（13）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/opportunities` | opportunity:read | 商机列表（q/region/industry/预算区间/发布时间/info_type/favorite/created_after 过滤；order_by=publish_at/budget/match） |
| POST | `/opportunities/sync` | opportunity:sync | 手动同步（advisory lock 互斥，同查询 409）；返回 SyncRunResponse |
| GET | `/opportunities/sync/{run_id}` | opportunity:read | 同步运行记录 |
| GET | `/opportunities/{opportunity_id}` | opportunity:read | 商机详情（正文 ids+contentFlag 懒加载，计费） |
| POST | `/opportunities/{opportunity_id}/favorite` | opportunity:read | 收藏 |
| DELETE | `/opportunities/{opportunity_id}/favorite` | opportunity:read | 取消收藏 |
| GET | `/opportunities/{opportunity_id}/buyer-intel` | opportunity:read | 采购人情报缓存（不重复计费） |
| POST | `/opportunities/{opportunity_id}/buyer-intel/sync` | opportunity:sync | 采购人情报同步（tenderer 角色，前 50 条聚合） |
| GET | `/opportunity-subscriptions` | opportunity:read | 订阅列表（用户级） |
| POST | `/opportunity-subscriptions` | opportunity:sync | 建订阅（name/interval_minutes≥30/query；每租户上限 10） |
| PATCH | `/opportunity-subscriptions/{subscription_id}` | opportunity:sync | 改订阅 |
| DELETE | `/opportunity-subscriptions/{subscription_id}` | opportunity:sync | 删订阅 |
| POST | `/opportunity-subscriptions/{subscription_id}/run` | opportunity:sync | 立即执行订阅 |

### 3.7 知识库 knowledge（25）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/knowledge/bases` | knowledge:read（或组织范围） | 知识库列表（四层过滤后） |
| POST | `/knowledge/bases` | knowledge:write（或组织范围） | 建库（scope=personal/tenant/org_unit，classification） |
| GET | `/knowledge/bases/{kb_id}` | knowledge:read（或组织范围） | 库详情 |
| GET | `/knowledge/documents` | knowledge:read（或组织范围） | 文档列表（kb_id 过滤） |
| POST | `/knowledge/documents` | knowledge:write（或组织范围） | 上传文档（docx/pdf/txt/md/xlsx/xlsm/pptx） |
| GET | `/knowledge/documents/{document_id}` | knowledge:read（或组织范围） | 文档详情（chunks/text_preview） |
| PATCH | `/knowledge/documents/{document_id}` | knowledge:write（或组织范围） | 改文档元数据（title/kind/tags） |
| DELETE | `/knowledge/documents/{document_id}` | knowledge:write（或组织范围） | 删文档 |
| GET | `/knowledge/images` | knowledge:read（或组织范围） | 图片列表 |
| POST | `/knowledge/images` | knowledge:write（或组织范围） | 上传图片（png/jpg/jpeg/webp→转 PNG，sensitive 标记） |
| PATCH | `/knowledge/images/{image_id}` | knowledge:write（或组织范围） | 改图片（title/caption/tags/sensitive） |
| DELETE | `/knowledge/images/{image_id}` | knowledge:write（或组织范围） | 删图片 |
| GET | `/knowledge/images/{image_id}/content` | knowledge:read（或组织范围） | 图片字节（Bearer 鉴权） |
| GET | `/knowledge/search` | knowledge:read（或组织范围） | 检索（q≤300，limit 1–20） |
| GET/POST/DELETE | `/knowledge/bases/{kb_id}/acl[/{acl_id}]` | knowledge:manage | 库级 ACL（subject_type=user/tenant/org_unit × role=viewer/user/editor/manager × expires_at） |
| GET/POST/DELETE | `/knowledge/documents/{document_id}/acl[/{acl_id}]` | knowledge:manage | 文档级 ACL（有 ACL 即白名单） |
| GET | `/knowledge-packs` | template:read | 模板/技能包列表（category=structure/format/material） |
| POST | `/knowledge-packs` | template:write | 建包（scope/classification） |
| PATCH | `/knowledge-packs/{pack_id}` | template:write | 改包（status=draft/published/deprecated） |
| GET | `/skills` | template:read | 技能目录 |
| GET | `/projects/{project_id}/materials` | project:read + knowledge:use | 项目材料四态 |
| GET | `/projects/{project_id}/sections/{section_id}/knowledge-context` | project:read + knowledge:use | 章节知识召回预览 |
| PUT | `/projects/{project_id}/template` | project:write + template:use | 绑定结构/格式模板 |
| GET | `/projects/{project_id}/workspace` | project:read + knowledge:use | 工作台四阶段状态 |
| GET | `/projects/{project_id}/submission-receipts` | project:read | 交标回执列表（含自查快照摘要） |
| POST | `/projects/{project_id}/submission-receipts` | project:write | 记录递交时间/平台/回执号并冻结交标自查快照 |
| GET | `/projects/{project_id}/delivery-summary` | project:read | 客户交付摘要（客户品牌/自查/最新回执；用于打印或另存 PDF） |

### 3.8 投标项目 projects（23）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/projects` | project:read | 项目列表 |
| POST | `/projects` | project:write | 建项目（opportunity_id/company_id/name/code/bid_deadline） |
| GET | `/projects/{project_id}` | project:read | 项目详情 |
| PATCH | `/projects/{project_id}` | project:write | 改项目 |
| POST | `/projects/{project_id}/documents` | project:write | **上传招标文件**（≤20MB；>10MB 异步 ParseJob；图片/扫描 PDF/不识别的类型走 OCR） |
| GET | `/projects/{project_id}/documents` | project:read | 文档列表（parse_status/parse_error/ocr_used） |
| GET | `/projects/{project_id}/constraints` | project:read | 最新约束集（version/payload/parse_meta） |
| GET | `/projects/{project_id}/document-diff` | project:read | 近两版差异（budget/requirements/scoring） |
| GET | `/projects/parse-jobs/{job_id}` | project:read | 异步解析任务状态（queued/running/succeeded/failed + progress） |
| GET | `/projects/{project_id}/scoring-items` | project:read | 评分项列表 |
| POST | `/projects/{project_id}/matrix` | project:write | 生成/刷新评分矩阵（幂等 upsert） |
| GET | `/projects/{project_id}/matrix` | project:read | 矩阵行（covered/partial/missing） |
| POST | `/projects/{project_id}/qualification-check` | project:write | 资格预审（PASS/FAIL/UNKNOWN） |
| POST | `/projects/{project_id}/decision` | project:write | 生成 Bid-No-Bid（Go/Conditional Go/No-Go） |
| GET | `/projects/{project_id}/decision` | project:read | 决策结果 |
| POST | `/projects/{project_id}/responses/build` | project:write | 构建逐条响应矩阵（对账删除） |
| GET | `/projects/{project_id}/responses` | project:read | 响应列表（items/summary/total/unresolved） |
| PATCH | `/projects/{project_id}/responses/{response_id}` | project:write | 单条响应（status/note/evidence_refs/section_ref） |
| POST | `/projects/{project_id}/responses/batch` | project:write | 批量响应（混入非本项目的 id 整体失败） |
| GET | `/projects/{project_id}/responses/export` | project:read | 导出 Excel（有未处理条目需 acknowledge） |
| GET | `/projects/{project_id}/result` | project:read | 复盘结果 |
| PUT | `/projects/{project_id}/result` | project:write | 记录复盘（outcome=won/lost/cancelled） |
| POST | `/projects/{project_id}/policy-qa` | project:read + knowledge:use | 制度口径问答（model_scope=tenant/personal） |

### 3.9 标书生成 generation（9）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{project_id}/outline` | project:write | 生成大纲（模板优先；reset 时有 approved 章节拒绝） |
| GET | `/projects/{project_id}/sections` | project:read | 章节列表 |
| PATCH | `/sections/{section_id}` | project:write | 编辑章节（title/content_text/content_json） |
| POST | `/projects/{project_id}/generate` | project:write | 生成章节（mode=rules/llm；model_scope） |
| POST | `/sections/{section_id}/factcheck` | project:write | 事实核查（sourced/unsourced + traceability_rate） |
| GET | `/sections/{section_id}/evidence` | project:read | 证据链列表 |
| POST | `/sections/{section_id}/approve` | project:approve | 审批章节（unsourced>0 需 acknowledge_unsourced） |
| POST | `/projects/{project_id}/export/docx` | project:export | 导出 docx（dark_bid=true 匿名化；未处理条目需 acknowledge_unresolved） |
| POST | `/projects/{project_id}/anonymize` | project:export | 一键匿名化导出（绕过闸门，仅清洗） |

### 3.10 评标 evaluation（4）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{project_id}/evaluations` | project:write | 发起 AI 评标（judge 模型，model_scope） |
| GET | `/projects/{project_id}/evaluations` | project:read | 评标历史 |
| GET | `/projects/{project_id}/evaluations/latest` | project:read | 最新评标 |
| GET | `/evaluations/{evaluation_id}` | project:read | 评标详情（逐项得分/理由/需复核） |

### 3.11 合同审查 contracts（10）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{project_id}/contract-reviews` | project:write | 上传合同创建审查（≤10MB；切分+10 规则+三方对照） |
| GET | `/projects/{project_id}/contract-reviews` | project:read | 审查列表 |
| GET | `/projects/{project_id}/contract-reviews/{review_id}` | project:read | 审查详情（clauses/findings/disclaimer） |
| PATCH | `/projects/{project_id}/contract-reviews/findings/{finding_id}` | project:write | finding 处置（confirmed/dismissed） |
| POST | `/projects/{project_id}/contract-reviews/{review_id}/deep-review` | project:write | LLM 深审（**显式触发=按次计费**；无证据结论拒收） |
| GET | `/projects/{project_id}/contract-reviews/{review_id}/milestones` | project:read | 履约节点 |
| POST | `/projects/{project_id}/contract-reviews/{review_id}/supplements` | project:write | 上传补充协议（自动对照） |
| GET | `/projects/{project_id}/contract-reviews/{review_id}/export` | project:read | 导出 xlsx（format=xlsx） |
| GET | `/projects/{project_id}/contract-reviews/tenant-rules` | project:read | 租户自定义规则 |
| POST | `/projects/{project_id}/contract-reviews/tenant-rules` | project:write | 建租户规则（pattern/message_template/severity） |

### 3.12 合规 compliance（14）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{project_id}/compliance/scan` | project:read | 运行合规预检（29 规则 + 租户策略） |
| GET | `/projects/{project_id}/compliance/reports` | project:read | 报告列表 |
| GET | `/projects/{project_id}/compliance/latest` | project:read | 最新报告 |
| GET | `/compliance/reports/{report_id}` | project:read | 报告详情（findings 全量） |
| POST | `/compliance/reports/{report_id}/export` | project:read | 导出报告（format=xlsx/pdf） |
| PATCH | `/findings/{finding_id}` | project:approve | finding 处置（open/fixed/accepted） |
| GET | `/policies` | project:read | 合规策略（default vs effective） |
| PATCH | `/policies/{rule_id}` | project:approve | 改策略（enabled/severity_override/threshold_override） |
| GET | `/compliance/access-requests` | 登录 | 破窗调阅申请列表（审查人看全租户） |
| POST | `/compliance/access-requests` | compliance:break_glass | 申请调阅个人资源（requested_minutes≤60） |
| POST | `/compliance/access-requests/{request_id}/approve` | compliance:review | 审批（审批人≠申请人） |
| POST | `/compliance/access-requests/{request_id}/deny` | compliance:review | 拒绝 |
| GET | `/compliance/personal-resources` | audit:read | 个人资源元数据（离职交接视图） |
| POST | `/compliance/personal-resources/{resource_type}/{resource_id}/transfer` | user:manage | 个人资源转移 |

### 3.13 其余模块

| 模块 | 端点 |
|---|---|
| analytics（1） | `GET /analytics/bid-results`（赢率/失标分析，project:read） |
| data-ops（1） | `GET /analytics/data-ops?days=30`（成本台账/源健康，audit:read） |
| notifications（6） | `GET /notifications`（unread_only/limit）、`GET /notifications/unread-count`、`POST /notifications/read`（ids/all）；`GET/POST /notifications/channels`、`DELETE /notifications/channels/{id}`（均需 tenant:settings:manage） |
| 开放 API（5） | `GET/POST /api-tokens`、`DELETE /api-tokens/{id}`（tenant:settings:manage；Bearer `btc_…` 认证，权限=scopes ∩ 创建者权限）；`GET/POST /webhooks`、`DELETE /webhooks/{id}`、`GET /webhooks/{id}/deliveries`（HMAC-SHA256 签名投递，仅 https） |
| clients | **已随 0068_retire_client_scope 整体下线**（客户级隔离/客户配额取消，租户内改用角色+组织单元+ACL）；本文不再描述其端点 |
| audit（2） | `GET /audit/events`（page/page_size≤200 + action/resource_type 过滤）、`GET /audit/verify`（哈希链校验） |
| 版本形态 | 多租户运营端点（租户开通/套餐/用量）为**企业版闭源能力**，社区版不包含 |
| system（12） | `GET /system/ai-status`（仅登录）；`GET/PATCH /system/model-configs/{purpose}`（model:manage）；`GET/PUT/DELETE /system/model-configs/personal/{purpose}`（model:use）；`GET/PATCH /system/model-policy`（model:policy:manage）；`GET/PUT /system/pricing-settings`（报价成本参数，见 §3.16）；`GET/PUT /system/decision-settings`（决策权重/阈值/成本模型/提醒） |
| web-search（1） | `POST /web-search`（query/count；project:read；失败也记账后 503） |
| contract-records（13） | `GET/POST /contract-records`；`GET/PATCH /contract-records/{id}`；`POST .../draft|submit|approve|reject|terminate|archive`；`GET .../approvals|ledger|changes`；`POST .../ledger`、`POST .../changes`、`POST .../changes/{change_id}/approve|reject`；`GET .../export`（project:read / project:write） |
| 项目其他补充 | `GET /projects/{id}/decision-evidence`（project:read，决策证据链只读聚合）；`GET /projects/{id}/ai-usage`；`GET /projects/{id}/documents`、`GET /projects/{id}/parse-jobs/{job_id}`；`POST /knowledge/bases/{kb_id}/template-draft`（knowledge:write，知识库冷启动生成模板草稿）；`POST /projects/{id}/submission-receipts`、`GET .../submission-receipts`、`POST .../attachments`、`GET|DELETE .../attachments/{attachment_id}` |
| 健康检查 | `GET /healthz`（存活）、`GET /readyz`（DB+Redis 就绪） |

---

## 3.14 文件体检 file-check（3）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/file-check/tender` | project:read | multipart `file` + `province?`；买方侧 10 类规则体检，返回 findings/证据/建议 |
| POST | `/file-check/similarity` | project:read | multipart `files`（2–10）；4-gram Jaccard 两两相似度 + 共同片段 |
| POST | `/file-check/anbiao` | project:read | multipart `file`；投标人侧暗标自查：企业名/简称、信用代码、联系方式、人员姓名与证书号、自指表述、元数据、页眉页脚、文件名；返回 BID-101~108 证据与建议 |

只做合规提示/自查，不做串标定性、不提供规避检测；扫描件需先 OCR 或上传可复制文本版本。

---

## 3.15 报价定价 pricing（6）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{project_id}/pricing/strategy` | project:write | 生成/重算报价策略；inputs：quote_amount/budget_amount/other_direct_cost/labor_items[{role,days}]/industry/include_narrative；样本不足时显式 `insufficient_data` + missing |
| GET | `/projects/{project_id}/pricing/strategy/latest` | project:read | 最新策略快照（id/engine_version/inputs/cost/price_band/tiers/sensitivity/narrative） |
| GET | `/projects/{project_id}/pricing/price-band` | project:read | 历史价格带（P25/P50/P75 与对应金额、样本来源统计），与项目当前预算/行业口径 |
| GET | `/projects/{project_id}/pricing/sensitivity` | project:read | 基于最新策略的敏感性行（±3/5/8%，净利率/折扣/赢率估计/价格位置） |
| POST | `/projects/{project_id}/pricing/quote` | project:write | 生成报价单不可变快照；body：tier/quote_amount/items[{name,spec,quantity,unit,unit_price}]/supplier_name/notes/validity_days；档位无价 422、明细合计≠总额 422 |
| GET | `/projects/{project_id}/pricing/quote/latest` | project:read | 最新报价单（quote_number/total_amount/amount_in_words/items/valid_until） |
| POST | `/projects/{project_id}/pricing/quote/export/docx` | project:export | 导出最新报价单 DOCX（文件名 `quote_<编号>.docx`） |

报价单生成与导出均写审计（`project.pricing.quote.generate` / `.export`）；策略/报价单均为快照表，跨租户 404。

## 3.16 系统配置与运维端点补充

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/system/pricing-settings` | tenant:read | 报价成本参数：cost_model（direct_rate/indirect_rate/tax_rate/labor_rates）与 target_margins 及来源标注 |
| PUT | `/system/pricing-settings` | tenant:settings:manage | 更新上述两块（省略=不变，提供=整体替换）；非法值 422；写审计 `pricing_settings.update` |
| GET | `/projects/{project_id}/ai-usage` | project:read | 项目 AI 用量汇总（来自 ai_usage_ledger） |

运维 CLI（非 HTTP）：`python -m app.cli repair-audit-chain [--tenant-id <uuid>] [--dry-run]`——审计哈希链断链（历史清理删行）时受控重算 prev_hash/hash，事件内容不动，修复事件 `audit.chain.repair` 留痕；先 dry-run 后应用。

---

## 4. 核心链路示例

### 4.1 上传招标文件并轮询解析（扫描件走 MinerU OCR）

```bash
# 1. 登录（DEMO_PASSWORD 为启动时显式设置的 BIDCOPILOT_DEMO_PASSWORD；不内置默认口令）
TOKEN=$(curl -fsS -X POST http://127.0.0.1:18080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"demo@example.com\",\"password\":\"$DEMO_PASSWORD\"}" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. 上传（≤10MB 同步解析，直接返回约束集；>10MB 返回 parse_job）
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@scan.pdf;type=application/pdf"

# 3. 异步任务轮询（仅 >10MB）
curl -fsS "http://127.0.0.1:18080/api/v1/projects/parse-jobs/$JOB_ID" \
  -H "Authorization: Bearer $TOKEN"
```

响应要点：`document.parse_status=parsed`、`document.ocr_used=true`、`constraint.payload.parse_meta.ocr_provider="mineru"`、`ocr_structure_used=true`（表格已结构化）。

### 4.2 商机同步（付费调用，注意互斥）

```bash
curl -fsS -X POST http://127.0.0.1:18080/api/v1/opportunities/sync \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"keywords":["政务云"],"publish_from":"2026-10-01","page_size":20}'
```

- 同查询内容并发 → **409**（advisory lock）；返回 `SyncRunResponse`：`status`（网关状态原样）/`rows_fetched`/`rows_new`/`warnings`（含 `missing_uid` 等）/`gateway_order_no`/`charged`。

### 4.3 评分矩阵与资格预审

```bash
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/matrix" -H "Authorization: Bearer $TOKEN"
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/qualification-check" -H "Authorization: Bearer $TOKEN"
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/decision" -H "Authorization: Bearer $TOKEN"
```

资格逐条 PASS/FAIL/UNKNOWN（缺证据不伪造）；决策硬性 FAIL → No-Go。

### 4.4 生成→核查→审批→导出

```bash
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/outline" -H "Authorization: Bearer $TOKEN"
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/generate" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"mode":"rules"}'
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/sections/$SID/factcheck" -H "Authorization: Bearer $TOKEN"
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/sections/$SID/approve" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
curl -fsS -X POST "http://127.0.0.1:18080/api/v1/projects/$PID/export/docx" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dark_bid":false,"acknowledge_unresolved":false}' -o bid.docx
```

LLM 模式未配置时 503 `llm_not_configured`（不静默降级）；导出遇未处理条目 409（需 `acknowledge_unresolved=true`）。

---

## 5. 维护提示

- **端点权限不在 openapi.json 中**：本文档权限列来自路由源码 `require_permission` 依赖注入；后端改权限后需重新核对（`grep -rn "require_permission" backend/app/modules/*/router.py`）。
- **错误码同理**：openapi 仅声明 200/201/204/422；401/403/404/409/500/502/503 由业务层 `AppError` 家族抛出（见 §2.3）。
- 新增端点后建议重新导出 openapi.json 与本文档对拍（方法：`docker exec bidcopilot-app curl -s http://127.0.0.1:8000/openapi.json`）。
