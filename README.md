# BidCopilot

数据驱动的投标决策与交付平台。私有化优先，首版服务 IT/信息化/系统集成投标方。

> English one-liner: BidCopilot is a self-hosted, AI-assisted bid decision & delivery platform for tender teams — opportunity monitoring, tender parsing, proposal generation, compliance pre-check, contract review, export and post-bid review, multi-tenant by design. Docs are in Chinese; an English README may follow.

<p align="center">
  <a href="#支持项目"><img src="docs/images/wechat-sponsor-qr.png" width="150" alt="微信付款码（请作者喝咖啡）" /></a>
  <br/><sub><b>支持与联系</b>：微信付款码见左图（或页尾「支持项目」）· 反馈与交流请提 <a href="https://github.com/xrzlizheng/BidCopilot/issues">GitHub Issue</a> · 私事联系看仓库 Owner 的 GitHub 主页邮箱 · 安全漏洞请走 <a href="https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-reviewing/privately-reporting-a-security-vulnerability">私密漏洞报告</a>，勿开公开 Issue</sub>
</p>

## 文档体系（docs/product/）

| 文档 | 读者 |
|---|---|
| [产品指南](docs/product/2026-10-22-bidcopilot-product-guide.md) | 业务/售前/培训 |
| [交付文档](docs/product/2026-10-24-bidcopilot-delivery-report.md)（含文档导航） | 双方项目经理 |
| [运维手册](docs/product/2026-10-24-bidcopilot-ops-runbook.md) | 运维/值班 |
| [API 参考](docs/product/2026-10-24-bidcopilot-api-reference.md) | 集成/二开 |
| [数据库参考](docs/product/2026-10-24-bidcopilot-database-reference.md) | DBA/接手开发 |
| [开发交接手册](docs/product/2026-10-24-bidcopilot-dev-handbook.md) | 接手开发/测试 |
| [详细设计文档](docs/product/2026-10-24-bidcopilot-detailed-design.md) | 核心开发 |
| [竞品调研与升级建议](docs/product/2026-10-31-competitive-research.md) | 产品/战略 |
| [企业数据 MCP 接入说明](docs/product/2026-10-31-keeprisk-mcp-integration.md) | 集成/运维/产品 |

产品内使用帮助：登录后从侧边栏「帮助 → 使用帮助」进入，或直接访问 `/help`；内容按当前页面与后端校验维护。

## 功能矩阵

| 模块 | 能力 | 状态 |
|---|---|---|
| 商机发现 | KeepRisk 机会同步/详情、订阅定时同步、消息中心、采购人竞争情报（既有供应商识别）；自动匹配：匹配结果持久化、企业资质自检与缺口清单、阈值甄别（推荐/复核/跳过）、高匹配幂等自动立项、订阅同步后自动触发 | ✅ |
| 企业资产 | 企业/人员/资质库、知识库（多格式/加权召回）、AI 模型租户级配置；企业详情含「工商自检」 | ✅ |
| 版本形态 | 社区版（BSL 1.1）：单企业自部署，不限量；企业版（商业授权）：多租户运营/套餐计量/平台治理 | 社区版 ✅ / 企业版闭源 |
| 交标回执 | 人工记录递交时间/平台/回执号，冻结当时交标自查快照；不自动确认平台已递交 | ✅ |
| 知识库冷启动 | 多份历史标书批量入库（案例+历史标书标签）并自动切片，供生成召回；不自动生成模板 | ✅ |
| 交付品牌 | 导出 docx/xlsx/PDF 内嵌**投标企业自身名称**（取项目关联企业 `Company.name`，无企业时回退 BidCopilot）；暗标导出自动剔除品牌；项目「交付摘要」可打印/另存 PDF | ✅ |
| 文件体检 | 招标文件买方侧确定性规则体检（指定品牌/地域/注册资本/主观评分等 10 类）；跨投标人雷同自查（4-gram Jaccard + 共同片段）；只做合规提示 | ✅ |
| 企业情报 | 采购人/投标主体/合同相对方的工商、信用、股权关联与尽调；批量核验（≤100 家）、企业对比（2–5 家）、风险监控订阅（被执行/失信/处罚/经营异常变化通知）；按次计费，缓存命中不计费 | ✅ |
| 投标项目 | 招标解析（docx/pdf/txt/md + 扫描件 OCR 双供应商）、约束集、评分矩阵、Bid-No-Bid | ✅ |
| 生成 | 章节生成、事实核查、审批、docx/暗标导出（暗标元数据自检+闸门拦截） | ✅ |
| 合规 | 29 条确定性预检 + 租户策略、逐条响应/偏离表（确认人流）、标书查重（跨章节+跨知识库） | ✅ |
| 评标 | AI 评标模拟、findings 报告（PDF/Excel） | ✅ |
| 使用帮助 | 产品内 `/help`：9 步主线、20 个模块章节、状态速查、FAQ、权限与边界说明；侧边栏按角色显示入口 | ✅ |
| 报价策略 | 成本拆解（人力/其他直接/间接/税）、历史价格带 P25/P50/P75、赢率—毛利权衡、报价敏感性（±3/5/8%）、三档建议价（按历史价格带锚定，成本底线与预算钳制，成本加成价作对照）、LLM 策略叙述（未配置则降级模板） | ✅ |
| 报价成本参数 | 租户级成本参数配置卡（直接成本率/间接费率/税率、人力岗位日单价、三档目标净利率）+ `GET/PUT /api/v1/system/pricing-settings`；配置后三档建议价对新租户可达 | ✅ |
| 报价单 | 按策略档位价或显式总价生成不可变快照（明细行编辑、合计必须等于总额、供应商/有效期/备注、人民币大写）、`POST /api/v1/projects/{id}/pricing/quote`、`GET .../quote/latest`、导出正式报价单 DOCX | ✅ |
| 审计链修复 | `python -m app.cli repair-audit-chain [--tenant-id <uuid>] [--dry-run]`：历史清理删行导致断链时受控重算 prev_hash/hash（事件内容不动），修复事件 `audit.chain.repair` 留痕并自检 verify | ✅ |
| 合同审查 | 条款切分、10+ 风险规则、三方一致性对照（招标文件↔投标承诺↔合同）、LLM 深审、履约节点提醒、补充协议对照、租户自定义规则 | ✅ |
| 合同管理 | 合同主数据台账、从投标承诺起草（每条带来源，标注需法务复核）、审批状态机与留痕、履约台账（开票/收款/保证金/质保金，金额 Decimal）、变更与续签留痕、到期提醒、台账 xlsx 导出（含合计与公式注入防护） | ✅ |
| 复盘 | 中标/失标记录、赢率分析 | ✅ |
| 运营 | 成本对账、审计哈希链、多租户隔离、RBAC、用户管理 | ✅ |
| 平台 | 长文档异步解析、文档版本链（Work 身份）、制度口径问答、定时调度器 | ✅ |

## 系统架构

```mermaid
flowchart LR
    subgraph container["bidcopilot-app（单容器）"]
        nginx["nginx"] --> app["FastAPI 模块化单体"]
        app --> pg[("PostgreSQL")]
        app --> redis[("Redis")]
        app --> minio[("MinIO")]
        sched["APScheduler<br/>订阅同步 · 里程碑提醒"]
    end
    browser["Next.js 工作台"] --> nginx
    app -. KeepRisk 付费网关 .-> keeprisk["KeepRisk 标讯"]
    app -. 企业数据 MCP .-> krmcp["KeepRisk 企业数据 MCP<br/>工商 / 股权 / 尽调"]
    app -. OpenAI 兼容 .-> llm["LLM / Judge"]
    app -. OCR 双供应商 .-> ocr["MiniMax 云端<br/>MinerU 4 本地(ONNX/CPU·GPU 自适应)"]
```

## 验收快照（2026-10-31 企业数据 MCP）

- 后端全量 `pytest tests\unit tests\integration -q` → **782 passed / 2 skipped**；`scripts/e2e_release_check.py` → **14 PASS / 0 FAIL**；`scripts/e2e_functional_sweep.py` → **42 PASS / 0 FAIL**；`scripts/e2e_module_sweep.py` → 86 PASS / 1 FAIL（唯一失败为演示库历史数据被删导致审计链缺口，链算法本身经单测双向验证）。
- 镜像 `bidcopilot:single 705f1eb5a2a2` 重建运行；`/healthz` 200。
- 真实 HTTP：`resolve` / `profile` / `relations` / `batch` / `compare` 均 `ok`；批量核验一次网关调用、企业对比两家 12 次调用/12 计费单位；缓存命中 `billing_units=0`。
- 浏览器验收：资产页「工商自检」、商机详情「采购人企业情报」、项目页「投标主体核验/企业对比」、合同详情「合同相对方尽调」均渲染；`red/unknown` 风险原样展示并提示人工尽调，不用绿色摘要掩盖。
- 单一投标主体：产品面向企业投标部门，**不提供多客户/代理机构模式**；租户内隔离用部门（`org_units`）+ 角色 + 资源 ACL + 字段策略。多客户能力已于迁移 `0068_retire_client_scope` 整体下线。
- 监控订阅：watchlist 周期检查被执行/失信/处罚/经营异常；首次建基线，风险变化写站内通知；无记录显示 unknown，不冒充安全。
- 套餐额度（企业版能力）：平台页配置项目/席位/AI 篇数/AI 字数上限；超限返回 429；社区版不限量。
- 交标回执：项目详情记录递交时间/平台/回执号，并冻结当时交标自查快照；只做人工留痕，不代表平台已受理。
- 知识库冷启动：知识库页批量导入历史标书（案例+历史标书标签），自动切片供生成召回；模板仍需人工整理。
- 交付品牌：导出件内嵌投标企业自身名称（`Company.name`），不做弱化工具品牌的白标；项目「交付摘要」可打印/另存 PDF，包含交标自查与回执摘要。
- 文件体检：`/file-check` 招标文件买方侧体检（10 类确定性规则）与跨投标人雷同自查（相似度+共同片段）；只做提示，不提供规避检测。

## 验收快照（2026-10-30 产品内使用帮助）

- 单容器镜像 `bidcopilot:single 40f39e51ebfe` 重建并部署，`bidcopilot-app` 运行中；`/healthz=200`、`/help=200`。
- 浏览器验收（管理员与 `bid_engineer`）：`/help` 标题、20 个章节、40 个目录锚点全部命中；普通角色仍可见帮助入口，`用户管理/权限治理` 不显示。
- 自动化：容器 `frontend` 构建阶段 `pnpm exec vitest run 'src/app/(app)/help/page.test.tsx'` → **1 passed**；`tsc --noEmit`、`next lint`、`next build` 通过。
- 全量前端测试 27/30；3 个失败集中在既有 `pricing-strategy-panel` 与 `contract-lifecycle-panel` 测试，与帮助页无关。
- 已知缺口：移动端目录未单独做视觉验收；无权限角色直接访问 `/users`、`/access` 的路由外壳仍渲染（后端 403、无数据泄露，属既有纵深防御缺口）。

## 验收快照（2026-10-29 新能力补齐）

本轮补齐三项能力并通过真实部署验收：**自动匹配投标机会**（原仅列表页排序）、**智能计算报价策略**（原完全缺失）、**合同管理**（原仅合同审查）。

### 自动化测试（真实 PostgreSQL）

```powershell
# 本机安全策略禁止项目内 venv trampoline spawn 解释器（os error 5），统一用封装脚本
scripts\py.cmd -m pytest tests\unit -q          # 792 passed, 2 skipped（2026-11-17）
# 集成测试需真实 PG/Redis（容器映射 25432/6390）：先在容器内建 bidcopilot_test 库并 alembic upgrade head
$env:DATABASE_URL="postgresql+asyncpg://bidcopilot:bidcopilot@localhost:25432/bidcopilot_test"
$env:REDIS_URL="redis://127.0.0.1:6390/0"
scripts\py.cmd -m pytest tests\integration -q   # 194 passed（2026-11-17）
scripts\py.cmd scripts\smoke_demo.py --base-url http://127.0.0.1:18080 --password '<演示口令>'   # SMOKE OK
scripts\py.cmd scripts\check_architecture.py    # architecture ok: 239 files
```

单容器镜像 `bidcopilot:single` 重建后，运行库 `alembic_version = 0054_contract_lifecycle`，业务表 66 张（新增 `price_strategies`、`opportunity_matches`、`contract_records`、`contract_approvals`、`contract_ledger_entries`、`contract_changes`、`contract_draft_clauses`）。
### 业务实测（真实 HTTP，脚本见 `scripts/biz_test_*.py`）

| 场景 | 结果 |
|---|---|
| 报价策略·租户未配置 | 显式 `insufficient_data` + 逐条 `missing` 原因，不编造样本 |
| 报价策略·配置后 | 成本对账 `612000+180000+576000+288000=1656000`；价格带 P25/P50/P75；三档锚定 P75/P50/P25 = 4,758,000 / 4,628,000 / 4,459,000；敏感性 7 行；叙述 `source=llm` |
| 自动匹配·批量 | `scanned/matched/recommend/review/skip` 计数正确，`errors=0` |
| 自动匹配·推荐→自动立项 | 正文解析资质 → `qualified=true` → `recommend` → 自动建项目（active），重复调用走 `existing` 分支不重复建 |
| 合同管理 | 建记录 → 起草 9 条（带 `source_json`）→ 提交 → 审批 active → 台账 `0.1+0.2=0.30` 精度正确 → 续签留痕 → xlsx 6.4KB → 非法跳转 409 |
| 租户隔离 | 越租户/不存在资源一律 404 |
| 页面可达 | `/login` `/contracts` `/projects` `/opportunities` 均 200 |
### 本轮修复的缺陷

1. `GET /projects/{id}/pricing/price-band` 返回 HTTP 500：`pricing/service.py` 调用了未定义的 `build_price_band_payload`（应为已导入的 `price_band_payload`）。集成测试本就覆盖该接口，但此前从未执行。
2. `tests/integration/test_pricing_api.py` 成本断言写成 38000，正确值为 33000（24000 人力 + 6000 间接 + 3000 税费），已改正并把算式写入断言注释。
3. 三档建议价原为纯成本加成，会给出远低于市场中枢的价格（实测 1.07M vs 市场 P50 4.63M）。已改为按历史价格带锚定，并以成本底线/预算钳制，同时保留成本加成价作对照。

### 已知限制

- 前端 `vitest` 在本机因 `esbuild` 启动 panic 无法执行（对既有测试同样失败，属环境问题）；前端以 `pnpm typecheck` + `pnpm build` 为准。
- 报价策略在租户未配置 `pricing_cost_model` / `pricing_target_margins`、或同类历史样本 <5 条时，会如实返回 `insufficient_data`，需先完成租户校准。

## 验收快照（2026-10-24）
### 单容器全链路实测（2026-10-24，`bash scripts/run_local_site.sh` → `http://127.0.0.1:18080`）

```bash
cd backend && uv run python ../scripts/smoke_demo.py --base-url http://127.0.0.1:18080   --project "某市政务云系统集成项目"
# login ok: demo@example.com / 演示投标企业
# approval ok: 5 sections
# responses ok: 5 items, 1 unresolved
# batch ok: tainted 404 without half-apply, 2 marked then restored
# compliance ok: report 6ee5761b, rules hit 4
# export ok: gate blocked then forced, xlsx 7404 bytes, responses xlsx 6695 bytes, docx 37210 bytes
# analytics ok: total=3 win_rate=66.7% · data ops ok: calls=49 failed=4 · SMOKE OK

docker exec bidcopilot-app psql -U bidcopilot -d bidcopilot -tAc   "select version_num from alembic_version;"
# 0040_review_compliance_export（head）
docker exec bidcopilot-app psql -U bidcopilot -d bidcopilot -tAc   "select count(*) from pg_tables where schemaname='public' and tablename not like 'alembic%';"
# 53
```

### 单容器回归 + 部署三形态（2026-10-25，ADR-0001 落地）

```bash
cd backend && uv run python ../scripts/smoke_demo.py --base-url http://127.0.0.1:18080 --project "某市政务云系统集成项目"
# login ok · approval ok: 5 sections · responses ok: 5 items, 1 unresolved
# batch ok: tainted 404 without half-apply, 2 marked then restored
# compliance ok: report 655c819b, rules hit 4
# export ok: gate blocked then forced, xlsx 7402 bytes, responses xlsx 6697 bytes, docx 37210 bytes
# analytics ok: total=3 win_rate=66.7% · data ops ok: calls=77 failed=4 · SMOKE OK

docker exec bidcopilot-app psql -U bidcopilot -d bidcopilot -tAc "select version_num from alembic_version;"
# 0041_paid_call_quotas（head）
docker exec bidcopilot-app psql -U bidcopilot -d bidcopilot -tAc "select count(*) from pg_tables where schemaname='public' and tablename not like 'alembic%';"
# 54
```

- 调度器多实例互斥（K8s/SaaS 多副本正确性地基）：`tests/unit/test_scheduler_lock.py` 5 passed（未获锁即跳过执行）；Celery 退役后全量 `uv run pytest -q` **280 passed / 3 skipped**。
- 外置数据层形态：`docker compose -f deploy/external/docker-compose.yml up -d` → `/healthz` `{"status":"ok","version":"0.1.0"}`（应用连外部 PostgreSQL/Redis）；迁移在外部库执行至 `0041_paid_call_quotas`（54 表）；外部 MinIO put/get ok；演示种子默认不灌（users=0）。
- K8s Chart（重构为单体器件拓扑）：`helm lint` 0 failed；`helm template` 渲染 Secret/ConfigMap/Service/Deployment/Ingress/Job 共 6 个资源且 YAML 合法；缺 `secrets.databaseUrl` 时 required 守卫生效。首次 install 冒烟待 K8s 环境（ADR-0001 验证要求）。

OCR 供应商实测（MinerU 4）：镜像内 `mineru-kit models download --tier basic --small-backend onnx` bake 成功（模型 819MB，`models verify` ok，`MINERU_MODEL_SOURCE=local` 离线可读）；上传 290KB 无文字层扫描模拟 PDF 首扫 16.7s（含 ONNX 模型加载）、热模型二次 3.8s，`parse_meta.ocr_provider="mineru"`、`ocr_structure_used=true`，资格 4 条/预算 480 万元文本与原图一字不差。

### 验收记录精选（实测，非转抄）

- **金标解析基线**：19 份对抗语料（docx/pdf/txt/md 四版式，含 PDF 表格/合并表头/双附表），四维度 micro P/R 全 1.000——评分 54/54、资格 36/36（覆盖 33/33）、废标 22/22，预算/暗标 19 case 全对。
- **OCR 双供应商**：`OCR_PROVIDER ∈ auto/minimax/mineru/none`；MinerU 4 本地解析（ONNX 小模型纯 CPU，MINERU_TIER=auto 按显存自适应升 standard/VLM 档），支持 PDF/图片/docx/pptx/xlsx/epub/html/ofd 等 MinerU 全集类型，表格产出 table_cell 块走表格优先抽取；模型构建期预热进镜像（离线可用）。
- **MinerU 容器全链路实测（2026-10-24）**：`docker build`（uv sync `mineru>=4.0,<5`，torch 钉 CPU 轮子 187MB；模型 bake 41.9s）→ `scripts/run_local_site.sh` → `/system/ai-status` `ocr.provider=mineru` → 上传扫描 PDF：首扫 16.7s / 热模型 3.8s，约束集评分项、实质要求 4 条、预算 480 万（`source_span` 指向 OCR 段落）全链路落库；镜像 5.14GB（含 819MB 模型 + torch-cpu）。
- **企业级权限 P0-P3e（2026-10-24）**：迁移 0028-0041（head `0041_paid_call_quotas`，54 张业务表）；后端 `pytest -q` **289 passed / 3 skipped**；前端 **12 passed** + `tsc --noEmit` + `next build`；运行态登录 demo 后 capabilities、知识库 ACL 授予/撤销、ACL 主体列表、个人资源元数据、部门树、角色、字段策略、AI 状态均 200，`/access` `/knowledge` `/assets` `/settings` `/templates` 页面 200；浏览器实测知识库「共享与访问控制」面板授予/撤销「整个企业·只读」成功，`/access` 个人资源交接卡与 `/assets` 归属列正常渲染；容器内真实 MinerU OCR 输出 `text=BIDCOPILOT OCR TEST 2026`。镜像 `bidcopilot:single 9d9b6573a53c`，回滚标签 `bidcopilot:pre-hardening`（2b20161d8f8c）、`bidcopilot:pre-final2`（4aeb82d38a37）、`bidcopilot:pre-final`（bc5c8d3f2ba9）、`bidcopilot:pre-review`（e0af7f749ea5）与 `bidcopilot:pre-ui-governance`（36275adf5175）。
- **深度 Review 修复（2026-10-25）**：修复路径穿越、跨资源 ACL 撤销、角色分配提权、文档 ACL 写绕过/过期锁死、部门资产越权进入生成、导出权限缺失、xlsx 公式注入、搜索 URL scheme、上传体积上限等；新增 12 项集成回归 + 多项安全/认证/配额单测。详见 `docs/aegis/reviews/2026-10-25-deep-review.md`（本地文档，不提交 git）。
- **七项加固实测（2026-10-25）**：登出后旧 access token 立即 401；连续 5 次错误密码后第 6 次 429；私网 `base_url` 保存返回 422；审计链 `/audit/verify` 685 事件 valid；组织 ACL `include_descendants` 与批量判权（8 库查询数 ≤6）有集成测试；付费配额表 `paid_call_quotas` 已建；ParseJob 超时任务恢复有集成测试。迁移 head `0041_paid_call_quotas`，业务表 54 张。
- **订阅闭环**：30 分钟订阅触发后 49 秒完成首轮真实同步（`抓取 20 / 入库 20 / 新增 20`、`charged=true`）；并发触发 409；崩溃恢复标 `sync_interrupted` 不二次计费。
- **合同审查**：上传→切分→10 条风险规则+三方一致性对照→确认流→xlsx 导出；LLM 深审显式计费（`contract.deep_review | charged=t`）；履约节点每日扫描进消息中心。
- **付费调用防护**：三条付费路径（手动同步/订阅调度/buyer-intel）统一 advisory lock + 记账容错——记账失败不 500、重试不二次计费。

<details>
<summary>历史验收明细（2026-10-24 评审与修复记录）</summary>

界面选择器：项目工作区「逐条响应 / 偏离表」→ 状态筛选「只看未处理」显示 `筛选 2 / 5 条`，「选中筛选结果」+「批量标记为偏离」后徽标为 `待确认 0 / 已响应 3 / 偏离 2 / 不响应 0`；「导出 docx」被拦时出现横幅「导出已被合规闸门拦下」，点「已确认风险，强制导出」得到 `已导出 bid_<id>_normal.docx。`。

```bash
cd frontend && pnpm test        # 12 passed (4 files) —— 含逐条响应的筛选/批量/关联契约与消息中心/订阅面板
pnpm typecheck && pnpm lint     # tsc --noEmit 无输出；next lint 0 warnings/errors
```

3 个跳过项全部是 WebP 转码断言：本机 Windows 的 Pillow `_webp` 扩展被系统应用控制策略阻止加载（`WinError 4551`，11.3.0/12.0.0/13.0.0 同样失败）。同一组断言已在生产 Linux 镜像 `deploy-backend:latest`（Pillow 13.0.0，`features.check("webp") == True`）上对与测试完全相同的 WebP 字节 fixture 跑通。

前端 `pnpm tsc --noEmit`、`pnpm lint`、`pnpm build` 通过；机会中心统一承载「数据接口商机」与「联网情报」两个来源页签（`/opportunities?source=web`），旧 `/research` 入口 307 跳转到该页签。联网搜索提供方徽标读取自 `GET /api/v1/system/ai-status` 的 `web_search` 字段，配置 `DEEPSEEK_SEARCH_API_KEY` 后实测为 `{"configured": true, "provider": "deepseek", "host": "api.deepseek.com"}`，`POST /api/v1/web-search` 真实返回 5 条政府站点结果并计费（`cost_ledger` 记 `web_search:deepseek | ok | charged=t | rows=5`）。

商机订阅闭环实测（单容器，`SCHEDULER_ENABLED=true` 已写入镜像 ENV，测试环境默认 false）：`POST /api/v1/opportunity-subscriptions` 建 30 分钟间隔订阅后，容器内调度器在 49 秒内完成首轮触发，真实 KeepRisk 同步返回 `抓取 20 / 入库 20 / 新增 20`、`charged=true`、`gateway_order_no=<19 位网关单号，略>`；`GET /api/v1/notifications/unread-count` 返回 `{"unread": 1}`，顶栏铃铛下拉显示「订阅「政务云监控」新增 20 条商机」，点「全部已读」后归零；界面选择器「只看新商机」在 `/opportunities?new=1` 下显示 `共 20 条 · 仅最近 24 小时新入库`（同租户全量 40 条）。

筛选与排序实测：机会中心已暴露后端全部筛选（地区/行业/预算区间/发布时间），地区填「广东」+「筛选」后计数从 `共 40 条` 变为 `共 3 条`；`order_by` 支持 `publish_at | budget | match`，匹配度为订阅偏好启发式加权（关键词/地区/行业各 2 分、预算区间 1 分），界面显示 `命中 N 项` 与「启发式粗排 · 命中项来自启用订阅」角标，实测 40 条按命中数非增序排列（首屏 1、末屏 0）；无启用订阅或订阅无条件时 `order_by=match` 返回 422，不静默降级。

评审修复（2026-10-24）：订阅执行先推进 `last_run_at` 水位再写台账/审计，下游异常不会让下一轮 tick 重复抓取已计费请求；手动 `/run` 与调度共用 `pg_try_advisory_lock`，手动并发返回 409；订阅写操作收紧到创建者本人（列表与写同口径）；订阅快照补上当前筛选的行业/预算区间/发布时间；匹配度改为逐订阅求值（预算不再用 min/max 包络），Python 命中判定与 SQL 一样忽略大小写；采购人情报上游失败时保留上次成功聚合，界面标注「以下为上次成功同步的结果」；既有供应商严格按近 12 个月 ≥2 次。新增用例覆盖：重名 409、跨用户 PATCH/DELETE 404、锁冲突 409、通知跨用户隔离、情报 sync 403、失败保留旧榜单、匹配度多订阅预算与大小写。

深度评审修复（2026-10-24）：会话级 advisory lock 改为 `engine.connect()` 专用连接贯穿付费调用（ORM commit 会归还连接，原写法锁与连接解绑）；获锁后复查到期，未完成的 running run 不再发起第二次付费请求而是标记 `sync_interrupted`；网关异常先 rollback 重载再落失败态；订阅上限用租户事务级锁；通知按 run_id 幂等。匹配度 ilike 关键词按字面量转义（`%/_` 不再造成"排序命中但徽标 0"）；采购人情报 `DataResult.status` 原样透传（ok/empty/unauthorized…），`synced_at` 记录最近成功时间，失败只改状态与错误、保留上次成功聚合与溯源；tenderer 检索显式 `InfoTypeList=["3"]`；厂商按 NFKC+casefold+空白归一合并；`0021` 同时清掉 0020 的冗余复合索引并归一历史状态。

同步告警可追溯（2026-10-24）：`source_sync_runs` 新增 `warnings_json`，把每条告警的 `code/message/field` 一并落库；同步 API 返回 `warnings` 数组，界面在同步结果里列出告警条数与代码。迁移 `0023` 为存量运行补 `[]` 默认值。实测缺 uid 用例：`warnings_count=1` 且 `warnings[0] = {code: missing_uid, field: uid}`。

商机身份兜底硬化（2026-10-24）：`uid` 在位时 `dedup_key` 仍是 uid（存量身份不变）；缺失时不再只用 title+buyer+publish_at，而是加入 `info_type` 与 `project_code` 并把时间统一到 UTC ISO，招标/中标或不同包件不会再互相覆盖；缺 uid 会生成 `missing_uid` warning 计入 `source_sync_runs.warnings_count`，但数据照常入库。迁移 `0022` 回填存量兜底行（仅 `source_id IS NULL`）并在改键前做冲突检测，任一冲突即中止并报出冲突 id。实测：合成一条旧兜底键行 → 迁移后键与运行时 `opportunity_fallback_key()` 完全一致。

采购人竞争情报实测：机会详情「竞争情报」卡点「查询采购人历史中标」→ `POST /api/v1/opportunities/{id}/buyer-intel/sync`，真实 KeepRisk 检索返回 `已同步 50 条`，聚合出 `近 12 个月中标 12 次`、`既有供应商 2 家`（工业和信息化部电子第五研究所 3 次、广东数字政府研究院 2 次，界面标「既有供应商」，命中企业资产已建档的标「已建档」）；`opportunity_buyer_intel` 落库 1 行，`cost_ledger` 记 `opportunity.buyer_intel | succeeded | charged=t | rows=50`，审计记 `opportunity.buyer_intel_sync`。无采购人的机会返回 422，上游鉴权失败时状态原样落库为 `failed` 并在界面显示错误码，不伪造聚合结果。


</details>


### 2026-10-25 分支 A 增强实测

- 能力：合规响应跨版本携带/最新版本口径、资产计算与展示脱敏分离、知识 ACL 相似度、文档类型（招标/澄清/补遗/最终标书）+ 显式 apply、报价/保证金/投标有效期/工作量成本确定性检查、交标就绪清单（IT/政采/工程模板）、截止提醒、五维决策卡与 legacy run、项目证据包、policy_sources 自动 factcheck、决策/提醒参数设置、采购人关注与可解释商机排序、投标承诺→合同对照、答辩/PPT 提纲、有限自我检查。
- 后端：`APP_ENV=test ... pytest -q` → **322 passed / 2 skipped**（Linux 容器 + 真实 PostgreSQL）。
- 前端：`pnpm typecheck`、`pnpm test` → **12 passed / 4 files**；`pnpm build` 成功。
- 架构：`python ../scripts/check_architecture.py` → `architecture ok: 174 files`。
- 单容器：镜像 `bidcopilot:single` `eaadcf273995`，容器 `bidcopilot-app` 健康，运行库 head `0049_bid_commitments`（59 张业务表），`scripts/smoke_demo.py` **SMOKE OK**。
- 浏览器：CDP 登录 demo，项目页渲染「交标自查 / 投标决策卡 / 合同履约衔接 / 答辩提纲 / 有限自我检查」；机会中心渲染「仅看关注采购人 / 覆盖诊断」；设置页渲染「决策与提醒参数 / 人天单价」。

## 快速启动

前置：Python 3.12（uv 可自动安装）、Node 22+、pnpm 11、PostgreSQL 16。

### 本地站点（推荐用于测试）

在 `backend/.env` 配置 OpenAI 兼容网关后（`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`，可选 `JUDGE_*`）：

```bash
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' bash scripts/run_local_site.sh
```

脚本会拉起单个容器 `bidcopilot-app`（应用 + PostgreSQL + Redis + MinIO + nginx），打开 `http://127.0.0.1:18080/login`。本地测试与将来部署使用**同一个容器、同一个镜像、同一个容器名**，端口差异只在 compose 的端口映射。测试直连容器暴露的 `127.0.0.1:25432`（PostgreSQL）与 `127.0.0.1:6390`（Redis）；`55432` 落在 Windows Hyper-V 保留端口段、`6380` 曾被 Docker 端口分配器泄漏占用，故改用上述端口（compose 内有注释说明）。

本地冒烟默认灌入演示租户（账号 `demo@example.com`，口令取上面的 `BIDCOPILOT_DEMO_PASSWORD`）。仓库不内置默认演示口令：缺口令时脚本会直接报错退出，容器 `entrypoint.sh` 同样拒绝启动。不需要演示数据时用 `BIDCOPILOT_SEED_DEMO=0 bash scripts/run_local_site.sh`。

### 库级开发（非站点运行）

以下命令只用于后端库/前端组件的开发与单测，**不是站点启动方式**。站点（含界面验收、演示、将来部署）统一走上面的单容器脚本，不要在宿主机旁路启动 uvicorn / pnpm dev。

```bash
# 后端
cd backend
uv sync --all-extras
export DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@localhost:5432/bidcopilot'
export JWT_SECRET_KEY='replace-with-at-least-32-random-characters'
uv run alembic upgrade head
uv run python -m app.cli seed-demo --email demo@example.com --password 'Demo12345678!'
uv run uvicorn app.main:app --reload --port 8000

# 前端（另一个终端）
cd frontend
pnpm install
BACKEND_URL=http://127.0.0.1:8000 pnpm dev
```

打开 `http://localhost:3000/login`，用演示企业管理员登录后可同步机会、查看详情、维护资产。（多租户运营形态为企业版能力，见「版本形态」行。）

可选：播种可重复执行的演示租户（企业资产、2 个项目、约束集/评分项/章节、复盘结果）：

```bash
cd backend
uv run python -m app.cli seed-demo --slug demo --email demo@example.com
```

登录后可在“投标项目”查看中标/失标与赢率，在“数据运营”查看调用台账。若邮箱已属于其他租户，更换 `--email` 即可。

部署后可用冒烟脚本验证登录、审批、合规、导出与指标（演示口令经 `BIDCOPILOT_DEMO_PASSWORD` 环境变量或 `--password` 传入，不再内置默认值）：

```bash
export BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>'
cd backend
uv run python ../scripts/smoke_demo.py --base-url http://127.0.0.1:8000
# 真实生成 + judge 评标（会产生模型调用费用）
uv run python ../scripts/smoke_ai.py --base-url http://127.0.0.1:8000
# 全新空库部署验收（自动建库/迁移/播种/独立 API 冒烟/删库）
cd ..
uv run --project backend python scripts/fresh_db_smoke.py
# 试点预检（--probe-ocr 会真实调用 MiniMax，可能计费/失败）
uv run --project backend python scripts/preflight.py --probe-ocr
# PostgreSQL 非破坏性恢复校验（源库只读，恢复到临时库后删除）
bash scripts/verify_restore.sh
```

## 验收命令

```bash
# 后端单元（Windows 宿主同上方说明：基础 Python + PYTHONPATH）
cd backend
PYTHONPATH=".venv/Lib/site-packages" <基础Python解释器绝对路径> -m pytest tests/unit -q

# 后端集成（需要真实 PostgreSQL；Windows 宿主用基础 Python 跑，backend/.venv 的 uv trampoline 启动器已损坏）
export DATABASE_URL='postgresql+asyncpg://bidcopilot:bidcopilot@localhost:25432/bidcopilot_test'
export REDIS_URL='redis://127.0.0.1:6390/0'
export JWT_SECRET_KEY='test-secret-key-at-least-32-characters-long'
PYTHONPATH=".venv/Lib/site-packages" <基础Python解释器绝对路径> -m alembic upgrade head
PYTHONPATH=".venv/Lib/site-packages" <基础Python解释器绝对路径> -m pytest -m integration -q

# 真实 KeepRisk 冒烟（会产生计费调用）
GATEWAY_LIVE=1 ENABLE_MOCK_MODE=false \
KEEPRISK_API_BASE_URL='https://api.keeprisk.com:9080/api/data' \
KEEPRISK_APP_CODE='<your-app-code>' \
uv run pytest -m gateway_live -q

# 解析金标基线（3 份内置语料）
uv run pytest tests/unit/test_gold_corpus.py -q
# 客户语料：目录含 labels.json 与标书文件，格式见 scripts/eval_parser.py 的 docstring
uv run python ../scripts/eval_parser.py --corpus <客户语料目录> --min-f1 0.8

# 前端（宿主 esbuild 被系统策略拒绝，Vitest 必须在前端构建阶段镜像内跑）
docker build -f deploy/allin1/Dockerfile --target frontend -t bidcopilot-frontend-test:local .
docker run --rm bidcopilot-frontend-test:local sh -lc 'cd /app && pnpm test'
# 宿主可跑类型检查：cd frontend && pnpm exec tsc --noEmit

# 架构与许可证
cd ..
python scripts/check_architecture.py
python scripts/check_licenses.py
python scripts/check_secrets.py
```

## 数据契约要点

- 列表：`E_007_1271`，已确认参数包括 `keywords`、`searchKey`、`InfoTypeList`、`publishTime`、`regionList`、`industryList`、`budgetAmountList` 等。
- 详情：`ids + contentFlag=1`；`uid` 不是过滤参数。
- 中标：`InfoTypeList=["3"]`；`E_007_1341` 是企业标讯概况，不是投标明细。
- 正文：`content` 为 HTML，服务端白名单清洗、URL scheme 校验、200KB 上限；前端不直接渲染网关原文。
- 截止时间：源不提供 `bid_deadline`，P0 显示“源未提供”，P1 从正文解析。

## 部署

三种交付形态消费**同一个镜像**（`bidcopilot:single`，单一 Dockerfile 构建），差异只在部署 manifest 与数据层内置/外置。决策依据见 [docs/adr/ADR-0001-deployment-topology.md](docs/adr/ADR-0001-deployment-topology.md)。

| 形态 | manifest | 数据层 | 冒烟状态 |
|---|---|---|---|
| 单机（默认） | `deploy/allin1/docker-compose.yml` | 容器内置 PostgreSQL / Redis / MinIO | 全链路实测（`scripts/run_local_site.sh` + `scripts/smoke_demo.py`） |
| 数据层分离（VM / SaaS 数据层用法） | `deploy/external/docker-compose.yml` | 独立或托管 PostgreSQL / Redis / MinIO，经 env 外置 | 启动 + `/healthz` + 迁移至 head + 外部 MinIO 读写实测 |
| 客户 K8s | `deploy/helm/bidcopilot/` | 集群内或托管实例，`BIDCOPILOT_EXTERNAL_SERVICES=true` | `helm lint` / `helm template` 通过；首次 install 冒烟待 K8s 环境 |

### 单机（默认）

```bash
cp deploy/.env.example deploy/.env   # 填入 JWT/KeepRisk/LLM/MinIO 凭据
# 本地冒烟需显式提供演示口令（不内置默认值；生产形态 BIDCOPILOT_SEED_DEMO=0 可不设）
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' bash scripts/run_local_site.sh   # 唯一启动入口；http://127.0.0.1:18080
```

容器 `bidcopilot-app` 内含应用、PostgreSQL、Redis、MinIO 与 nginx，暴露
`http://localhost:18080`；`/api` 由同一容器内的 nginx 同源转发。本地测试与生产
使用同一镜像、同一容器名。

若 Docker Hub 在本机不可达，先为 Docker Desktop 配置可用的 registry mirror；`docker compose config` 不需要拉取镜像，仍可用于校验配置。

### 数据层分离（VM / SaaS 数据层用法）

```bash
docker compose -f deploy/external/docker-compose.yml up -d   # 默认 127.0.0.1:18080
# 与单机形态同时运行时改端口：
BIDCOPILOT_WEB_PORT=18081 docker compose -f deploy/external/docker-compose.yml up -d
```

应用容器不启动镜像内置服务（`BIDCOPILOT_EXTERNAL_SERVICES=true`），经环境变量连接
独立的 PostgreSQL / Redis / MinIO；把 compose 中的三个数据服务换成托管实例，即为
SaaS 的数据层用法。外置形态默认不灌演示种子数据（避免生产环境留下默认演示账号），
确需演示数据时设 `BIDCOPILOT_SEED_DEMO=1` 并显式提供 `BIDCOPILOT_DEMO_PASSWORD`
（缺失时容器拒绝启动，不再内置默认口令）。数据库 / MinIO 口令用
`BIDCOPILOT_DB_PASSWORD` / `BIDCOPILOT_MINIO_PASSWORD` 等环境变量覆盖，不要提交明文。

### Helm（客户 K8s）

```bash
helm upgrade --install bidcopilot deploy/helm/bidcopilot \
  --namespace bidcopilot --create-namespace \
  --set secrets.databaseUrl='postgresql+asyncpg://...' \
  --set secrets.jwtSecretKey='<32+ chars>' \
  --set secrets.minioAccessKey='...' \
  --set secrets.minioSecretKey='...' \
  --set ingress.enabled=true --set ingress.host=bidcopilot.example.com
```

chart 为单体器件拓扑：单一 Deployment 消费 `bidcopilot:single`；多副本下调度器互斥由
PostgreSQL advisory lock 保证（`app/db/locks.py`），副本可安全大于 1；数据库迁移由
pre-install/pre-upgrade Job 执行；生产建议使用 `secrets.existingSecret`。
投产前请在有 K8s 的环境补首次 install 冒烟并记录输出（ADR-0001 的验证要求）。

### 备份、恢复与离线包

```bash
bash scripts/backup.sh
bash scripts/restore.sh --backup-dir backups/<timestamp>   # 需输入 RESTORE 确认
bash scripts/build_offline_bundle.sh
bash scripts/verify_offline_bundle.sh <bundle-dir>
```

离线包说明见 [deploy/offline/README.md](deploy/offline/README.md)。

## AI 与 OCR 配置

- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：OpenAI 兼容网关；也接受 `OPENAI_API_BASE` / `OPENAI_API_KEY` / `OPENAI_MODEL` 别名。未配置时 `mode=llm` 明确返回 503。
- `JUDGE_BASE_URL` / `JUDGE_API_KEY` / `JUDGE_MODEL`：AI 评标判别模型；未配置时回退主模型并在结果中记录实际模型。
- **OCR 双供应商**（`OCR_PROVIDER ∈ auto/minimax/mineru/none`，默认 `auto`：MiniMax 已配优先，否则回落本地 MinerU）：MiniMax 走 Token Plan（`MINIMAX_API_KEY`/`MINIMAX_API_HOST`，与官方 MCP `understand_image` 同端点）；MinerU 4（镜像预装运行时与 basic 档模型——构建期 `mineru-kit models download` baked，`MINERU_MODEL_SOURCE=local` 离线运行）。`MINERU_TIER=auto`（默认）按显存自适应：CUDA 可用且 ≥ `MINERU_GPU_MIN_VRAM_GB`（默认 8GB）选 standard（VLM 档），否则 basic（ONNX 纯 CPU）；显式 `flash/basic/standard/advanced` 指定档位。本地 OCR 通道支持 MinerU 全集输入（PDF/图片/doc/docx/ppt/pptx/xls/xlsx/csv/rtf/odt/ods/odp/epub/html/mhtml/ofd），本地解析器不认识的类型自动回落 OCR。`MINIMAX_OCR_MAX_PAGES` 同时控制 MiniMax 渲染页数与 MinerU 的 PDF 页数预算；`MINERU_TIMEOUT_SECONDS`（默认 300）约束单次解析。本镜像为 CPU 构建（torch 钉 CPU 轮子），auto 恒收敛 basic；standard 档需 CUDA 构建变体。
- 规则抽取兜底层：`TENDER_UIE_FALLBACK_ENABLED`（本地 PP-UIE）/ `TENDER_LLM_FALLBACK_ENABLED`（云端 LLM）默认关闭；开启后对空缺/低置信维度补抽，产出 `confidence="uie"/"llm"` 标记并强制证据引用。
- 未配置或余额不足时文档解析明确失败（`ocr_not_configured` / `ocr_failed` / `ocr_model_missing`），不会伪造解析结果；Key 不得写入仓库。

## 标讯数据网关（KeepRisk）

**社区版边界**：招标文件解析、章节生成、合规预检、AI 评标、合同审查、复盘与运营后台——全部功能本地可用，无需任何外部数据服务。唯一需要外部数据的是**商机发现**（机会同步/详情、订阅定时同步、采购人竞争情报），它经 KeepRisk 标讯网关获取数据。

**网关提供**：全国招标公告与中标结果检索（列表/详情/正文）、采购人历史中标聚合（既有供应商识别）。数据契约与限制见上方「数据契约要点」——`bid_deadline` 源不提供、截止时间 P1 从正文解析，不拿发布时间填充。

**计费透明**：每次网关调用写入成本台账（`cost_ledger` 保留 orderNo/charge），失败原样落库不降级为空列表；租户级按次配额可配（`PAID_CALL_DAILY_LIMIT` / `PAID_CALL_MONTHLY_LIMIT`）。

**配置**：`KEEPRISK_APP_CODE` + `KEEPRISK_API_BASE_URL` 写入 `.env`（见 `deploy/.env.example`）；未配置时商机接口明确报错，不影响其余功能。

**开通网关**：优先加微信（下方二维码，备注「BidCopilot」）；也可在 GitHub 联系 [@xrzlizheng](https://github.com/xrzlizheng)。演示环境已接入，可先到演示站体验商机发现全流程再决定。

<p align="center">
  <img src="docs/images/wechat-friend-qr.png" width="240" alt="微信好友码：AI仙人掌#李正" />
</p>

## 企业数据 MCP（工商 / 股权 / 尽调）

- 用途：采购人、投标主体/联合体/分包、合同相对方的工商、信用、股权关联与尽调；入口按对象放置（资产=工商自检，商机=采购人，项目=投标主体，合同=相对方）。
- 开关与凭据：`KEEPRISK_MCP_ENABLED`、`KEEPRISK_MCP_URL`、`KEEPRISK_MCP_TOKEN`（只放环境变量/Secret，不入库）；未配置时接口返回 `unavailable`，不伪造数据。
- 计费：批量核验 1 次/批；企业对比约 10 次/家；尽调按 section 计费；缓存命中 `billing_units=0`；成本写 `cost_ledger`，查询写审计链。
- 接口：`/api/v1/company-intel/{resolve,profile,credit,relations,due-diligence,batch,compare}`；详见接入说明。
- 合规边界：只输出“关联关系/风险提示 + 依据”，不做串标定性、不提供规避建议；`red/unknown` 原样展示并提示人工尽调。

## 安全

- 不提交 `.env`、AppCode、API Key；凭据通过环境变量或密钥管理注入。
- 改密、停用或角色变更会递增用户 token 版本，旧 access/refresh token 立即失效；登出现在同样递增 token 版本，所有设备立即下线。
- 登录失败按「账号哈希 + IP」双维度限流（`LOGIN_MAX_ATTEMPTS` 默认 5、`LOGIN_IP_MAX_ATTEMPTS` 默认 20、窗口 `LOGIN_WINDOW_SECONDS` 默认 300s），超限 429；Redis 不可用时 fail-open。
- 模型网关地址保存与调用时校验：默认拒绝私网/环回/链路本地地址；自建 LLM 需设置 `MODEL_ALLOW_PRIVATE_BASE_URL=true` 或用 `MODEL_ALLOWED_HOSTS` 精确放行。
- 按次计费接口有租户级日/月配额（`PAID_CALL_DAILY_LIMIT` 默认 1000、`PAID_CALL_MONTHLY_LIMIT` 默认 20000，0 表示关闭）；配额原子预占，未计费调用自动释放。
- 组织 ACL 支持 `include_descendants`（含子部门）；知识库/文档判权批量预载 ACL，避免逐条查询。
- 大文档 ParseJob 由调度器每 5 分钟回收超时任务（`PARSE_JOB_STALE_MINUTES` 默认 15），任务级 advisory lock 防重复解析。
- 所有外部公告正文按不可信 HTML 处理。
- 所有租户业务查询强制 `tenant_id` 过滤；跨租户按 ID 读取返回 404。
- 详细约束见 [AGENTS.md](AGENTS.md)；漏洞报告渠道见 [SECURITY.md](SECURITY.md)。

## 政策对齐

对标发改法规〔2026〕195 号《关于加快招标投标领域人工智能推广应用的实施意见》（八部门，2026-02）：本项目覆盖「招标文件检测」场景（解析+约束集+29 条合规预检），AI 评标模拟提供投标侧预演；私有化单容器、审计哈希链与人工确认流响应「规范部署实施」导向。围串标痕迹方向的供应商侧能力（暗标元数据自检、标书查重）已内建。

## 鸣谢

以下开源项目与文章为本项目提供了思路借鉴（仅思路与文档形态参考，无任何代码复制；AGPL-3.0 项目代码依 AGENTS.md 约束不进本仓）：

- [cjy13/bid-master](https://gitee.com/cjy13/bid-master)——README 文档骨架与 OCR 自部署思路（本仓先后以 PaddleOCR、MinerU 内嵌落地）
- [FB208/OpenBidKit_Yibiao](https://atomgit.com/FB208/OpenBidKit_Yibiao)——标书查重与规则化废标检查的产品形态
- [qzp001/TenderLoom-Private](https://atomgit.com/qzp001/TenderLoom-Private)（投标版公开仓 [AIenderloom](https://atomgit.com/qzp001/AIenderloom)）——投标/业主双产品线的架构参考
- 邓松高《把"招标投标智能体"做成产品，会遇到什么？》——「AI 判断是待确认确认事实、承诺权限与生成能力分离」原则贯穿本项目确认流设计
- 智鹰AI《中石油的招标智能体，几分钟清完资质》——供应商侧四件套（资质结构化/逐条响应/关联排查/口径问答）的产品校准

## 许可证

本仓库（社区版）以 [BSL 1.1](LICENSE)（Business Source License 1.1）发布：

- **免费**：单企业自部署生产使用、内部修改、评估与二开（源码可见，Permitted Uses 内不受限制）。
- **限制（Excluded Use）**：基于本项目对外提供多租户 / SaaS / 托管服务（含租户开通、套餐计量、平台运营能力）须取得商业授权；多租户运营模块本身不在本仓库中。
- **Change Date 2030-11-19**：届时自动转为 Apache-2.0（Additional Use Grant 并入继续生效）。
- 商业授权联系：仓库 Owner 的 GitHub 主页邮箱，或提 Issue 标注 "commercial licensing"。

本项目只吸收 Apache-2.0 / BSL 1.1 兼容的第三方代码（AGPL / CC BY-NC 禁止复制进本仓）。

### 依赖署名

- **MinerU**（文档解析 OCR，MinerU Open Source License = Apache-2.0 + 附加条款）：依其附加条款第 2 条，基于 MinerU 向第三方提供在线服务须在界面或公开文档中显著标注——本仓库及基于本项目的 SaaS 服务在此声明使用 MinerU。附加条款第 1 条的单独商业许可门槛（MAU 超 1 亿或月收入超 2000 万美元，合并口径）与本项目规模无关。

## 验收快照（2026-11-18 报价单闭环、角色菜单核验与截图重采）

本轮在运行站点（`http://127.0.0.1:18080`，镜像 `bidcopilot:single` = `5e621053831e`，迁移 head `0077_price_quotes`，业务表 78 张）完成交付与验收：

- **报价单闭环**：成本参数配置卡 → 报价策略三档（均衡 75,675.68 元）→ 档位价/显式总价生成报价单 → 明细行编辑与合计校验（不符即阻断）→ 导出正式报价单 DOCX（含人民币大写）；不可变快照 + 审计留痕，跨租户 404。
- **配置能力**：`GET/PUT /api/v1/system/pricing-settings`（三率、人力岗位日单价、三档目标净利率），权限 `tenant:read` / `tenant:settings:manage`。
- **审计链修复**：`repair-audit-chain` CLI（`--dry-run` 优先）；演示租户历史断链（seq 3374 起重算 1156 行）修复后 `/audit/verify` = `valid: true`，修复事件 `audit.chain.repair` 上链留痕。
- **角色菜单核验（2026-11-18，企业版树实测）**：各系统角色逐一真实登录实测侧栏菜单，与 `SYSTEM_ROLES` 权限推导完全一致——企业管理员（全部业务+治理）、投标负责人（业务全量+合规策略，无运营审计成员）、标书工程师（业务读写，无合规策略/运营审计成员）、合规/法务（只读业务+运营审计+合规策略，无成员权限）；平台管理员角色与菜单仅存在于企业版；help 角色矩阵按实测口径校准。
- **测试**：社区版后端 `982 passed / 2 skipped`（单元+集成，`localhost:25432/bidcopilot_test` + Redis `6390`）；前端 `20 文件 / 69 用例`（`docker build --target frontend` 镜像内）；`tsc --noEmit` 0；`smoke_demo.py` 对最终镜像 **SMOKE OK**；浏览器真实登录态走通「配置卡→策略→报价→明细校验→导出」全链路。
- **文档一致性**：README/product-guide/api-reference/database-reference/detailed-design/dev-handbook/ops-runbook/delivery-report 增补、architecture 模型与 evidence 锚点刷新、`help-content.ts` 口径修正、产品指南 14 张界面截图按当前站点重采（详见 `docs/aegis/acceptance/2026-11-17/README.md`，本地留存）。

## 支持项目

如果 BidCopilot 帮到了你的投标团队，欢迎请作者喝杯咖啡：

<p align="center">
  <img src="docs/images/wechat-sponsor-qr.png" width="260" alt="微信收款码" />
</p>

点 Star、提 Issue、转发给需要的投标团队，同样是对项目的支持。

## 非目标

自动投标/CA 签章、报价优化、建筑工程首版、公开爬虫、保证中标、无人值守自动提交。
