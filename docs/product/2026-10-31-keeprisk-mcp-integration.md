# KeepRisk 企业数据 MCP 接入说明（P0 只读）

> **2026-10-31 收尾口径**：本文件记录 MCP 接入时的验收现场，其中涉及「客户管理 / 客户隔离 / 白标」的条目对应已下线能力（迁移 `0068_retire_client_scope`），仅作历史证据保留；现行口径见 `docs/product/2026-10-31-competitive-research.md` §0。


- 日期：2026-10-31
- 状态：后端 P0 已实现并通过单测；默认关闭（`KEEPRISK_MCP_ENABLED=false`），token 只从环境变量读取
- 相关：`docs/product/2026-10-31-competitive-research.md` §6.7（关联关系能力的产品化）

## 1. MCP 实测事实

| 项 | 实测值 |
|---|---|
| 端点 | `https://mcp.keeprisk.com/mcp`（Streamable HTTP，需 `mcp-session-id`） |
| 鉴权 | `Authorization: Bearer kpm_...`（只放环境变量/密钥管理） |
| 工具数 | 47 个 |
| 日配额 | 5000 次（`quota_status`：`day_used` 按计费调用递增） |
| 空结果 | `code=110` 表示“查询成功但无记录”，不是错误 |
| 计费口径 | 聚合工具返回 `api_calls`（并行接口数）与 `billing_units`（实际计费节数）；缓存/无记录节不计费 |
| 缓存 | 同一主体短时间重复查询会命中缓存：实测第二次关联图谱 `api_calls=4` 但 `billing_units=0` |
| 工商照面 | 返回网关原始包（`code=100`、`data`、`orderNo`、`charge`） |

已验证样本（华为技术有限公司）：

- `enterprise_graph_relations`（digest）首次：4 个 section，`api_calls=4`；重复查询走缓存 `billing_units=0`。
- `enterprise_due_diligence_snapshot`（4 节，`digest_only`）：`billing_units=2`、`cached_sections=1`、`free_sections=1`、`risk_summary=green`。
- `enterprise_basic_profile`：返回工商全量字段与 `orderNo`。

## 2. 工具到产品能力的映射

| BidCopilot 能力 | MCP 工具 | 说明 |
|---|---|---|
| 主体锚定 | `resolve_entity` / `fuzzy_search` | 先确认企业，避免同名串户 |
| 工商照面 | `enterprise_basic_profile` | 名称/信用码/法人/注册资本/地址/经营范围 |
| 信用评分 | `enterprise_credit_score` | 四维评分 + 风险等级（6 接口） |
| 关联图谱 | `enterprise_graph_relations` | 企业图谱/股权穿透/实质关联；`digest_only=true` 控制 token |
| 股权穿透 | `control_penetration` / `actual_controller` / `enterprise_extended_profile` | 直接股东、对外投资、实控人/UBO |
| 尽调风险 | `enterprise_due_diligence_snapshot` | 26 维可选；`sections` 控制费用，`digest_only` 控制返回体积 |
| 供应商批量核验 | `batch_query_companies`（≤100）/ `compare_enterprises`（2–5） | 项目内多家投标人对比 |
| 经营合规 | `operational_compliance` / `data_badge_full` | 行政许可、抽查、资质证书、进出口信用 |
| 人员风险 | `person_risk_profile` | 董监高被执行/失信；同名需人工消歧 |
| 知识产权 | `intellectual_property_portfolio` | 专利/软著/商标/域名 |
| 商机/采购人情报 | `enterprise_business_detail`（含标讯）/ `search_enterprises` / 产业链工具 | 可作为 KeepRisk 商机模块的补充 |
| 成本控制 | `estimate_billing` / `quota_status` | 调用前估算、查看配额；本地不计费 |

## 3. 已实现的后端接口

| 方法/路径 | 入参 | 计费预估（次） | 说明 |
|---|---|---|---|
| `GET /api/v1/company-intel/resolve` | `keyword`, `region?` | 1 | 企业候选（名称/信用码/法人/地区） |
| `GET /api/v1/company-intel/profile` | `ent_name` | 1 | 工商照面 |
| `GET /api/v1/company-intel/credit` | `ent_name` | 6 | 信用评分与风险等级 |
| `GET /api/v1/company-intel/relations` | `ent_name`, `digest_only=true` | 4 | 关联图谱摘要；`false` 返回全图（大） |
| `POST /api/v1/company-intel/due-diligence` | `ent_name`, `sections?`, `include_judicial=true`, `digest_only=true` | 节数（默认 26） | 尽调快照 |
| `POST /api/v1/company-intel/batch` | `ent_names`（1–100） | 1 | 批量工商核验；未返回名称需单独提示 |
| `POST /api/v1/company-intel/compare` | `ent_names`（2–5） | 10/家 | 企业对比：ranking + 四维评分 + 风险摘要；角标缺失时 risk=unknown 需提示 |

权限：当前复用 `asset:read`，未新增能力位（避免角色迁移）；后续若要独立治理再引入 `intel:read`。

响应统一为：

```json
{
  "status": "ok|empty|unavailable|rate_limited|unauthorized|contract_error",
  "data": {},
  "source": "keeprisk-mcp",
  "order_no": null,
  "charged": false,
  "api_calls": 4,
  "billing_units": 0,
  "warnings": [],
  "error": null
}
```

`status` 原样透传，不把非 ok/empty 降级为空列表。

## 4. 代码位置

- MCP 客户端：`backend/app/modules/gateway/keeprisk_mcp.py`（会话、SSE、快照、错误映射、HTTP 400/404 重连一次）
- 适配器：`backend/app/modules/gateway/enterprise_adapter.py`（工具→`DataResult`、计费单位估算）
- 路由：`backend/app/modules/company_intel/intel_router.py`
- 前端面板：`frontend/src/components/enterprise-intel-panel.tsx`，挂在资产页「情报」页签（工商/信用/关联/尽调四个按钮，状态与计费口径原样展示）
- 依赖装配：`backend/app/modules/gateway/deps.py`
- 配置：`backend/app/config.py`（`KEEPRISK_MCP_*`）
- 单测：`backend/tests/unit/test_keeprisk_mcp.py`（8 条，覆盖会话复用、SSE、错误、状态映射、计费字段）与 `backend/tests/unit/test_intel_router.py`（3 条，覆盖配额回冲、未计费释放、失败状态台账）；全量单测 `457 passed / 2 skipped`。
- 集成测试：`backend/tests/integration/test_company_intel_mcp_api.py`（真实 PG + 依赖替换），验证接口返回、`cost_ledger` 与审计写入；本轮因本机 Docker/PostgreSQL 未启动未执行，需在 55432 可用时补跑。

## 5. 成本与配额

- 调用前按工具预估单位预占租户配额：`resolve=1`、`profile=1`、`credit=6`、`relations=4`、`due_diligence=sections 数（默认 26）`。
- 调用成功后按 `billing_units` 回冲差额；失败/空结果全额释放。
- 每次调用写 `cost_ledger`（`pcode=mcp:<tool>`、`rows=api_calls`、`charged`、`order_no`）与审计链。
- 实测缓存会返回 `billing_units=0`，此时按未计费处理并释放配额。

## 6. 安全与合规

- token 只从 `KEEPRISK_MCP_TOKEN` 读取；`deploy/.env` 为本地未跟踪文件，禁止提交。
- 原始响应经 `snapshots` 落对象存储，可按 `raw_ref` 追溯。
- 对外只输出“关联关系/风险提示 + 依据”，不做串标定性、不提供规避建议。
- 个人信息（人员风险）需最小化展示；后续接入 UI 时按字段策略脱敏。
- 多副本：每个 API 进程各自持有 MCP 会话；会话失效时客户端自动重建一次。

## 7. 情报入口按对象拆分

“企业资产”里的企业是租户自己，因此那里只做**工商自检**；对他者的情报按业务上下文放置：

| 位置 | 对象 | 用途 |
|---|---|---|
| 企业资产 → 工商自检 | 自己/集团主体 | 执照一致性、信用、集团关联；低频但高风险 |
| 商机详情 → 采购人企业情报 | 采购人/招标人 | 工商、信用、关联关系；公告名称不规范时先「主体匹配」 |
| 投标项目 → 投标主体核验 | 投标人/联合体/分包/竞对 | 批量核验（1 次调用/批）、未返回名称提示、企业对比（2–5 家）、点选后查信用与关联 |
| 合同台账 → 合同相对方尽调 | 合同相对方 | 默认核验甲方；如相对方为乙方可用「主体匹配」切换 |

## 8. 待办

1. ~~前端面板~~ 已完成：资产页改名「工商自检」，商机详情新增「采购人企业情报」。
2. ~~投标项目页：投标人/联合体/分包批量核验 + 企业对比（2–5 家）~~ 已完成。
3. ~~合同台账：相对方尽调~~ 已完成（合同详情内嵌「合同相对方尽调」）。
4. ~~客户级隔离：代理机构多客户场景下，情报查询结果按 `client_id` 隔离与授权。~~ → **已作废**：产品只面向企业投标部门（单一主体），情报查询按租户 + 部门/角色隔离。
5. 字段策略：人员风险、联系方式等按密级脱敏。
6. 能力位：如需要独立授权，新增 `intel:read` 并迁移角色。
7. 缓存策略：接入 `billing_units=0` 的缓存命中统计，避免重复计费。
8. 前端组件测试：主机 esbuild 崩溃无法跑 vitest；需在容器/Linux 环境补跑 `enterprise-intel-panel.test.tsx`（已新增主体匹配用例）。

## 9. 验收记录（2026-10-31）

| 项 | 命令/操作 | 结果 |
|---|---|---|
| 后端单测 | `pytest tests/unit -q` | 460 passed / 2 skipped |
| 集成测试 | `DATABASE_URL=…:55432/bidcopilot_test pytest tests/integration/test_company_intel_mcp_api.py tests/integration/test_client_scope_api.py tests/integration/test_intel_watchlist_api.py tests/integration/test_tenant_plan_api.py -q` | 9 passed（profile/batch/compare + 客户隔离 + 监控 + 套餐 + 交标回执 + 文件体检） |
| 镜像构建 | `docker compose -f deploy/allin1/docker-compose.yml build` | exit 0 |
| 单容器重建 | `bash scripts/run_local_site.sh` | `bidcopilot-app` 重建、`/healthz` 200；最终镜像 ID `705f1eb5a2a2` |
| 登录 | `POST /api/v1/auth/login`（demo） | 200 |
| 主体锚定 | `GET /company-intel/resolve?keyword=华为技术` | 200 / `ok`，返回候选 |
| 工商照面 | `GET /company-intel/profile?ent_name=华为技术有限公司` | 200 / `ok`，`entName=华为技术有限公司` |
| 关联图谱 | `GET /company-intel/relations?ent_name=华为技术有限公司` | 200 / `ok`，`api_calls=4`，4 个 section |
| 台账/审计 | 容器内 `psql` 查询 `cost_ledger` / `audit_events` | `mcp:*` 3 行、`company_intel.*` 3 行 |
| 浏览器 | `/assets` → 情报 → 工商照面 | `ok`；企业名称/信用码/法人/注册资本/地址/成立日期均渲染；演示企业无工商记录时返回 `empty` 且字段留空（不伪造） |
| 资产页 IA | `/assets` 页头/页签 | 页头「企业资产」、页签「工商自检」、面板「企业工商自检」；旧「企业资产与情报」「企业概况与投标历史」已移除 |
| 商机详情 IA | `/opportunities/{id}` → 采购人企业情报 | 采购人=合肥高新技术产业开发区科学技术局(数据资源局)；主体匹配 → 使用此主体 → 工商照面 `ok`（信用码 `11340100MB1C64808H`、法人、地址） |
| 批量核验 HTTP | `POST /company-intel/batch`（华为/中兴/不存在企业） | 200 / `ok`，`api_calls=1`，2 行命中、1 个未返回；缓存命中时 `billing_units=0`、`charged=false` |
| 项目页浏览器 | `/projects/{id}` → 投标主体核验 | 加入「华为技术有限公司」→ 批量核验 `ok`；表格渲染登记状态/信用码/法人/规模/地区；未返回名称单独提示 |
| 合同页浏览器 | `/contracts` → 业务测试合同2 | 「合同相对方尽调」面板渲染，默认主体=甲方；虚构甲方查询返回 `empty`、字段留空（不伪造） |
| 企业对比 HTTP | `POST /company-intel/compare`（华为/中兴） | 200 / `ok`，`api_calls=12`、`billing_units=12`；ranking 中兴 94.3 > 华为 93.7；上游 `risk_level=red` 并返回“建议深度排查” |
| 项目页对比 UI | 项目页 → 加入两家 → 企业对比 | ranking 与四维评分渲染；`red` 徽标 + “请执行尽调摘要”提示；上游建议逐条展示 |
| 客户隔离集成 | `test_client_scope_api.py` | 2 passed：跨客户列表过滤/详情 404、默认客户归属、无客户租户旧行为 |
| 监控订阅集成 | `test_intel_watchlist_api.py` | 1 passed：首次建基线、风险变化写 `intel_watchlist` 通知 |
| 客户页浏览器 | `/clients` → 新建客户 → 添加成员 | 客户列表/成员面板渲染，成员添加成功 |
| 资产页客户与监控 | `/assets` → 新建企业选客户 → 工商自检 → 监控当前主体/立即检查 | 企业按客户归属；监控列表与检查状态渲染 |
| 套餐额度 | `test_tenant_plan_api.py` | 平台 PUT 套餐 → 项目/客户/席位超限均 429；用量接口返回套餐 |
| 交标回执 | `test_submission_receipt_api.py` | 记录回执并冻结自查快照；白标交付摘要；跨租户 404 |
| 知识库冷启动 UI | `/knowledge` → 历史标书冷启动 | 2 个 txt 文件入库为案例/历史标书标签，解析成功 |
| 白标交付 UI | 项目 → 响应与自查 → 客户交付摘要 | 客户品牌、47 项自查、回执与打印按钮渲染 |
| 文件体检 | `test_file_check_api.py` | 招标文件命中 4 条规则；相同文件高相似、不同文件低相似 |

限制：本机无 `helm`，Helm 模板未渲染验证；主机 vitest 因 esbuild 崩溃未跑，镜像内 `pnpm build` 成功（构建 exit 0）；验证用的临时企业行已删除。

## 10. 部署形态

- **allin1**：`deploy/.env` 增加 `KEEPRISK_MCP_*`（本地已开启；该文件被 git 忽略）。
- **external**：`deploy/external/docker-compose.yml` 已透传 `KEEPRISK_MCP_*` 四个变量。
- **Helm**：非密配置进 ConfigMap（`KEEPRISK_MCP_ENABLED/URL/TIMEOUT`），token 进 Secret（`secrets.keepriskMcpToken` → `KEEPRISK_MCP_TOKEN`）。
