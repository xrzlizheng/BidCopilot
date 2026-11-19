# BidCopilot 开发交接手册

> 适用对象：接手项目的开发/测试同事。目标：**从零到能改代码、能跑测试、能交付**。
> 前置阅读：[详细设计文档](2026-10-24-bidcopilot-detailed-design.md)（实现级设计，本文档的"为什么"在那边）、AGENTS.md（硬约束，与任何文档冲突以它为准）。
> 编写日期：2026-10-24。

## 1. 环境搭建（30 分钟）

```bash
# 1. 前置：Docker（含 compose）、Python 3.12、Node 24（容器构建会自动处理，本地单测/静态检查用 uv）
# 2. 克隆后安装后端依赖（含本地 OCR 全家桶）
cd backend
uv sync --extra ocr-local          # MinerU + torch-cpu；首次约数分钟
# 3. 起站点（唯一入口；容器内含 PG/Redis/MinIO/nginx，无需本地装数据库）
cd ..
BIDCOPILOT_DEMO_PASSWORD='<本地演示口令>' bash scripts/run_local_site.sh   # http://127.0.0.1:18080/login（demo@example.com，口令取该环境变量；不内置默认口令，缺失即报错退出）

# 4. 跑测试（集成测试默认打容器 PG 25432 口，容器起着就能跑；Windows 宿主 venv 启动器损坏，用基础 Python + PYTHONPATH）
cd backend
PYTHONPATH=".venv/Lib/site-packages" <基础Python> -m pytest tests/unit -q         # 单测+集成全量 986 passed / 2 skipped（2026-11-17 复验）
MSYS_NO_PATHCONV=1 DATABASE_URL="postgresql+asyncpg://bidcopilot:bidcopilot@localhost:25432/bidcopilot_test" REDIS_URL="redis://127.0.0.1:6390/0" PYTHONPATH=".venv/Lib/site-packages" <基础Python> -m pytest -q   # 全量含集成（75 个集成文件，2026-11-17 实跑通过）
PYTHONPATH=".venv/Lib/site-packages" <基础Python> -m ruff check . && PYTHONPATH=".venv/Lib/site-packages" <基础Python> -m mypy app   # 静态检查（CI 同款）
```

- **禁止**：宿主机旁路起 uvicorn / pnpm dev、第二套 compose、额外常驻容器（AGENTS.md 硬约束）。
- 本地 `.env`（backend/）被 gitignore；集成测试的 `conftest.py` 会强制清空真实 provider 环境变量，保证确定性。

## 2. 代码导航

```
backend/app/
├── main.py                  应用装配：社区版 30 个 router 挂载（企业版 platform 经 try-import 挂载）、lifespan（scheduler）、/healthz /readyz
├── config.py                全部配置（Settings）；OCR/MinerU/LLM/JWT/网关参数
├── core/                    jwt / permissions（能力匹配）/ errors（AppError 家族）/ time（CST）/ security
├── db/                      base（mixin）/ session（engine+expire_on_commit=False）/ repository（TenantRepository）/ locks（advisory lock）
├── workers/                 scheduler.py（APScheduler 8 job：订阅/履约/截止/到期/parse-job 恢复/ai-run 恢复/webhook outbox 重试/情报监控）
├── cli.py                   seed-demo / repair-audit-chain（审计链修复）；企业版另经 cli_extra 注册 init-platform
├── gateway/                 KeepRisk 客户端/适配器/快照/DataResult 契约/web_search/企业数据 MCP
└── modules/
    ├── tender/              解析域：parser（pdfplumber+规则）/ ocr（MinerU+MiniMax 双供应商）/ fallback（UIE→LLM 双层兜底）/ packages
    ├── projects/            投标主链：service（编排）/ matching（矩阵+资格+决策）/ results / responses_export / analytics
    ├── generation/          LLM 客户端、大纲/章节/事实核查/审批/导出/匿名化
    ├── evaluation/          judge 评标
    ├── compliance/          29 规则引擎（HANDLERS 分发）/ 策略 / ACL / break-glass / 个人资源
    ├── contracts/           合同审查/深审/履约/补充协议/租户规则
    ├── opportunities/       同步（sync）/ 订阅（subscriptions）/ 匹配（matching）/ buyer_intel
    ├── knowledge/           kb（分块检索）/ access（四层过滤）/ template_access
    ├── assets/              四类资产 / field_policies（字段脱敏）/ access（部门可见性）
    ├── identity/            JWT/用户/角色（roles.py 系统角色即真相）
    ├── access/              能力词表 + AuthzService + resource_acl 模型
    ├── org/                 组织单元（path 物化路径）
    ├── audit/               哈希链
    ├── system/              secrets（信封加密）/ service（effective_model 解析）
    ├── costs/ notifications/ company_intel/ platform/ web_search pricing/ file_check/ quote/（报价单）
```

**读码入口建议**：先 `modules/tender/parser.py`（约束集抽取，业务核心）→ `modules/projects/service.py`（编排）→ `modules/gateway/contract.py`（外部数据契约）→ `db/repository.py`（租户隔离）；企业情报从 `gateway/keeprisk_mcp.py` → `gateway/enterprise_adapter.py` → `company_intel/intel_router.py` 读。

## 3. 硬约束速查（AGENTS.md 摘要，违者 CI 拦截）

1. 生产代码禁 Mock；外部数据只经 `modules/gateway`；测试替身只在 `backend/tests/`。
2. 租户查询经 `TenantRepository`；跨租户按 ID 读 404。
3. `DataResult.status` 原样上传；非 ok/empty 不降级为空列表。
4. 外部公告 content 是 HTML：白名单清洗、限 URL scheme、限大小。
5. `bid_deadline` 不从发布时间/猜测值填充。
6. 配置统一 `app/config.py`；禁散落 `os.getenv`；禁弱默认密钥。
7. 迁移只前进；共享环境禁 destructive downgrade。
8. 只吸收 Apache-2.0 兼容代码（AGPL/CC BY-NC 不进仓）；`scripts/check_licenses.py` 门禁。
9. 验收数字必须随命令与选择器记录，不转抄。
10. 单容器部署形态固定；`.sh` 必须 LF（提交前 `b"\r" not in data` 校验）。

## 4. 常见开发任务

### 4.1 加一个 API 端点

1. 在模块 `router.py` 加路由（依赖 `require_permission("x")` 或 `require_permission_or_org`）；
2. 业务逻辑进 `service.py`（经 `TenantRepository.scoped()`；写操作注意审计 `AuditService.append` + 计费 `record_gateway_call`（如涉付费））；
3. schema 进 `schemas.py`（pydantic）；
4. 单测（service 级，respx/替身）+ 集成测试（`tests/integration/`，dependency_overrides 注入替身，参照 `test_projects_api.py`）。

### 4.2 加一张表

1. `modules/<m>/models.py` 加模型（混入 `TenantMixin` 除 tenants 本体）；
2. **`alembic/env.py:11-18` 登记模型模块**（漏登 = autogenerate 看不到表）；
3. `uv run alembic revision -m "00XX_pY_slug"` 生成迁移，命名 `NNNN_<phase>_<slug>`，down_revision 线性接链；
4. 在[数据库参考](2026-10-24-bidcopilot-database-reference.md)登记（表用途/约束/枚举）。

### 4.3 加一条合规规则

1. `app/data/rules/compliance_rules.json` 加规则（rule_id 全局唯一、type ∈ HARD/SOFT）；
2. `compliance/engine.py` 的 `HANDLERS` **必须**加对应 handler——缺 handler 会在扫描时 ValueError 快速失败（防规则 JSON 与代码漂移，刻意设计）；
3. 单测补 handler 用例；产品指南/README 的规则计数同步更新。

### 4.4 调整解析规则（约束集抽取）

- 规则集中在 `tender/parser.py` 各 `_extract_*` 函数；改完必须：
  1. 补/改金标语料（`backend/tests/gold_corpus.py`，当前 19 份）；
  2. `uv run python scripts/eval_parser.py --corpus <dir> --min-f1 1.0` 验四维度；
  3. 升 `PARSER_VERSION`（rules-vX.Y），界面约束集 `parse_meta.parser_version` 会显示。

### 4.5 换/加 OCR 供应商

- 实现 `tender/ocr.py` 的 `OcrProvider` 协议（`name` + `async recognize(...) -> OcrResult`）+ `configured()`；
- 工厂 `build_ocr_provider` 加分支（auto 兜底次序在此决定）；
- `config.py` 的 `OCR_PROVIDER` validator 加合法值；
- 测试参照 `tests/unit/test_ocr_providers.py`（`parser_factory`/transport 注入替身，不依赖真实模型）。

## 5. 测试指南

| 层 | 位置 | 手法 | 注意 |
|---|---|---|---|
| 单测 | `tests/unit/` | respx 打桩 httpx；monkeypatch（find_spec/factory）；替身类 | 生产禁 Mock 的替代是替身在 tests/ |
| 集成 | `tests/integration/` | 真 PG（容器 25432）+ ASGITransport；`app.dependency_overrides` 注入 FakeLLM/FakeProvider/FakeAdapter | 建表靠 `alembic upgrade head`；`tests/support.py` 提供 seed/login/cleanup |
| 前端 | `frontend/src/**/*.test.tsx` | Vitest + Testing Library | 宿主 esbuild 被系统策略拒绝，须在 `docker build -f deploy/allin1/Dockerfile --target frontend` 镜像内 `pnpm test`（20 文件 / 69 用例） |
| 金标 | `tests/gold_corpus.py` + `scripts/eval_parser.py` | 语料级 P/R | 改解析器必跑 |
| 网关计费 | `tests/gateway_live/` | 真实调用 | `GATEWAY_LIVE=1` 才跑，默认 skip |

CI 四门禁（`.gitlab-ci.yml`）：backend-lint（ruff+mypy）→ backend-test（全量）→ frontend（lint/typecheck/vitest/build）→ security（版本/架构/许可证/密钥扫描）。**提交前本地全跑一遍**。

## 6. 发布流程

1. 更新 `backend/pyproject.toml` version（如需）+ README 验收快照（数字随命令记录）；
2. 四门禁本地通过；
3. 提交信息用约定式（`feat/fix/docs/chore(scope): 描述`）；
4. 打离线包（如需现场部署）：`build_offline_bundle.sh` + `verify_offline_bundle.sh`；
5. 交付物 checklist：README、产品指南、交付文档、详细设计、API 参考、数据库参考、运维手册、本文档、`.env.example`、Helm/offline 介质。

## 7. 上手 10 步（第一周）

1. 起站点 + 用 demo 账号走一遍产品指南 §6.2 的"首次上手 10 步"；
2. 读 DD §4（解析链路）+ 对照 `tender/parser.py` 看约束集怎么抽出来；
3. 跑 `pytest tests/unit -q` + 金标评测，理解质量基线；
4. 读 `db/repository.py` + `modules/access/capabilities.py`，理解租户与权限两个横切面；
5. 挑一个"good first issue"：补一条合规规则的 handler+测试（照抄现有模式，风险低）；
6. 走一遍容器构建（`docker build -f deploy/allin1/Dockerfile .`），理解 MinerU bake 与镜像构成；
7. 读 `modules/gateway/contract.py` + `opportunities/sync.py`，理解付费三重保护（lock/水位/记账容错）；
8. 改一处小功能（如排障表加一行），完整走"改码→lint→test→容器验收"；
9. 读运维手册，在测试环境演练一次备份/恢复；
10. 对照 API 参考，用 curl 复现一遍核心链路（登录→上传→矩阵→生成→导出）。

## 8. 容易踩的坑

- **`expire_on_commit=False` 下 rollback 仍过期实例**：失败路径必须预捕获主键后 `session.get` 重取（参照 `subscriptions.py:263,299-301`），直接访问属性会抛 `MissingGreenlet`。
- **advisory lock 必须专用连接**（session 级），事务级用 `pg_advisory_xact_lock`——用错场景锁会静默失效（`db/locks.py` docstring 有详细说明）。
- **审计 `append` 只 flush**：忘记 commit = 漏审计。
- **torch  sources 只对直接依赖生效**：改依赖时 torch/torchvision 必须显式声明在 `ocr-local` extra 才能钉 CPU 轮子（`pyproject.toml` 注释有说明）。
- **MinerU 首次解析慢**（模型加载）：联调用 `MINERU_TIMEOUT_SECONDS`（默认 300s），排查时看 `backend.log` 而非前端超时。
- **Windows 写 `.sh`**：用编辑器保 LF，或提交前校验 `b"\r" not in data`。
- **改配置后页面无变化**：后端无热重载，重启容器；`.env` 变更一律重启生效。
