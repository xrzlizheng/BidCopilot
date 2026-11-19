# 当前态系统模型摘要

- 日期：2026-10-30
- 场景技能：`architecture-visualization:system-modeler`
- 基础技能：`architecture-visualization:c4model`（Structurizr DSL）、`architecture-visualization:graphviz`（DOT 容器图）
- 范围：当前态；不含目标态与改造建议

## 产物

| 文件 | 回答的问题 |
|---|---|
| `docs/architecture/bidcopilot-system.structurizr.dsl` | C4 语义源：系统上下文、容器、两条组件主线（投标交付内核 / 商机链路） |
| `docs/architecture/bidcopilot-containers.dot` | 容器与外部依赖的可视图；虚线为条件路径 |
| `docs/architecture/architecture-understanding.md` | 业务语言层面的架构理解：角色、能力地图、端到端主线、数据与状态、外部契约、未知项 |
| `docs/architecture/system-model.evidence.md` | 节点与关系的逐条证据、置信度与横切机制 |
| `docs/architecture/system-model.summary.md` | 本摘要与后续路由 |

## 模型要点

- 系统边界：自托管多租户投标决策与交付平台；单一镜像 `bidcopilot:single`，三种 manifest（allin1 单容器、external 数据层分离、Helm 单 Deployment 多副本）。
- 容器：nginx、Next.js 工作台、FastAPI API（31 router、进程内 APScheduler 8 job）、PostgreSQL、Redis、MinIO/本地存储。
- 组件：22 个 `app/modules` 归为投标交付内核（项目/解析/知识/生成/合规/评标/报价/合同/资产）、商机与外部数据（商机中心/企业画像/网关/搜索）、平台与治理（身份授权/租户开通/运行时设置/成本台账/审计/站内消息）。
- 外部系统：KeepRisk 标讯网关、KeepRisk 企业数据 MCP（工商/信用/股权/尽调）、LLM/Judge、MiniMax VLM OCR、联网搜索 API；MinerU 为镜像内置进程内组件。
- 租户内隔离：部门（`org_units`）+ 角色 + 资源 ACL + 字段策略；**不提供多客户/代理模式**（`clients` / `client_members` 与各表 `client_id` 已由迁移 `0068_retire_client_scope` 删除）。
- 情报监控：`intel_watchlist` + APScheduler 周期检查；风险变化写站内通知，成本入 `cost_ledger`。
- 执行模型：调度互斥与付费调用互斥均走 PostgreSQL advisory lock，多副本可安全运行。
- 数据契约：`DataResult.status` 原样透传；外部公告正文按不可信 HTML 处理；`bid_deadline` 不由源提供。

## 证据强度

- 结构类断言（容器、组件、路由、外部集成、存储、执行模型）为 high：来自代码、配置、迁移与 ADR。
- 运行态断言为 medium/文档证据：README 验收快照与冒烟脚本存在，本轮未复测。
- 图与文本口径一致：条件路径与未知项在两处都显式标注，未把推断升级为确认。

## 主要未知

1. Helm 首次 install 冒烟尚未做（ADR 验证要求未闭合）。
2. 运行态端到端未复测；需 `scripts/run_local_site.sh` + `scripts/smoke_demo.py` 记录命令与输出。
3. 未发现项目级 ACL/成员表；知识库与资产有资源级 ACL。
4. SaaS 计费与租户生命周期只有“建租户 + 建管理员”，无计费/订阅。
5. 无 CORS 中间件，部署形态按同源设计。
6. 通知已含外推渠道（企业微信 Webhook + SMTP 邮件），租户级配置、best-effort 广播。

## 后续路由

- 想看清一条业务链路（商机 → 立项 → 解析 → 生成 → 合规 → 评标 → 报价 → 合同 → 复盘）的调用时序与失败分支：`architecture-visualization:flow-visualizer`。
- 想对比 allin1 / external / Helm 的进程、网络与端口落位：`architecture-visualization:deployment-topology-analyzer`。
- 想评估改动影响面与回归路径：`architecture-visualization:dependency-impact-analyzer`。
- 想把模型变成可长期校验的活架构资产：`architecture-visualization:architecture-health`。

## 查看方式

- Structurizr DSL：在 Qoder 中打开 `.structurizr.dsl` 用 Structurizr DSL 查看器预览；也可粘贴到 Structurizr Playground。
- DOT：在 Qoder 中打开 `.dot` 用 DOT 查看器预览；若本机有 Graphviz，可用 `dot -Tsvg bidcopilot-containers.dot` 导出。
- 源码即事实来源；渲染结果只是派生视图，修改请改 DSL/DOT 与 Markdown，不要手改导出图。
