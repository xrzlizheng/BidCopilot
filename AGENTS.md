# BidCopilot Agent 指令

本文件是 BidCopilot 的项目级硬约束。实现与本文冲突时，以本文为准。

## 硬约束

- 生产代码禁止 Mock。外部数据只允许经 `app/modules/gateway` 获取；测试替身只能存在于 `backend/tests/`。
- 所有租户业务查询必须经 `TenantRepository`；跨租户按 ID 读取返回 404，不得泄露存在性。
- `DataResult` 的 `status` 必须原样上传到 API 与前端；非 `ok`/`empty` 不得降级为空列表或静默重试。
- KeepRisk 只允许发送官方文档 + 真实冒烟确认过的参数；`uid` 不是列表/详情过滤参数，`infoType` 不是请求参数。
- 外部公告 `content` 是 HTML，必须白名单清洗、限制 URL scheme、限制正文大小；禁止直接渲染原始 HTML。
- `bid_deadline` 不由 KeepRisk 提供；P0 置空，P1 从正文解析，不得用发布时间或猜测值填充。
- 配置统一从 `app/config.py` 读取；不得散落 `os.getenv`；禁止弱默认密钥。
- 迁移只前进；共享环境禁止 destructive downgrade。
- 只吸收 Apache-2.0 兼容代码；AGPL / CC BY-NC 代码禁止复制进本仓。
- 所有验收数字必须随命令与选择器记录，不得转抄他人报的“实测值”。
- **单一构建产物，多部署形态**：全仓只有一个镜像与一套 tag 规则。允许存在多份部署 manifest：`deploy/allin1/docker-compose.yml`（单机：应用 + PostgreSQL + Redis + MinIO + nginx 同容器，默认交付形态与本地冒烟目标）、`deploy/helm/bidcopilot/`（客户 K8s 平台）、`deploy/external/docker-compose.yml`（数据层分离部署）。本地测试与单机生产使用同一镜像、同一容器名（`bidcopilot-app`）、同一份 allin1 compose；禁止在宿主机旁路启动 uvicorn / pnpm dev；单机形态唯一启动入口是 `scripts/run_local_site.sh`。任何部署形态必须有冒烟证据才能写进文档。后台任务只允许 APScheduler + `app/db/locks.py` advisory lock 一种执行模型，多实例互斥由执行锁保证；禁止引入第二个后台执行框架。决策依据见 `docs/adr/ADR-0001-deployment-topology.md`。
- 脚本必须为 LF 换行：Windows 上以文本模式写 `.sh` 会注入 `\r`，容器会报 `exec ... no such file or directory`；提交前须校验 `b"\r" not in data`。

## 目录

- `backend/app`：FastAPI 模块化单体。
- `backend/tests`：pytest；集成测试使用真实 PostgreSQL。
- `frontend`：Next.js 14 App Router。
- `deploy`：Docker Compose（单机 allin1 / 数据层分离 external）、Helm Chart 与 nginx。
- `docs/product`：产品说明与宣贯材料。
- `docs/adr`：架构决策记录（ADR）。
- `docs/architecture`：架构模型与证据（system-model 系列）。
