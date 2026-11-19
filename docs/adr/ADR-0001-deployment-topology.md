# ADR-0001：部署拓扑——单一构建产物，多部署形态

- 状态：已接受（Accepted）
- 日期：2026-10-25
- 修订对象：AGENTS.md 原硬约束「部署形态固定为单容器」（该约束未单独成文，随本 ADR 改写）

## 背景（Context）

产品策略明确为三条：开源版本独立可运行；以开源获客、转化授权版本；交付形态同时覆盖单机私有化、客户 K8s 平台与 SaaS。

原硬约束形成于演示站阶段，其真实意图有两条：本地测试与生产使用同一镜像，消除环境分叉；不容许多套拓扑并存导致支持面翻倍。该约束与「K8s 部署 / SaaS 交付」直接冲突，但其意图仍然成立，需要以不违背意图的方式放开。

现状盘点（2026-10-25）：

- 单容器 manifest 已全链路实测（`scripts/run_local_site.sh` → `http://127.0.0.1:18080`）。
- `deploy/helm/` 从未冒烟，且与应用的执行模型不匹配：worker deployment 运行 `celery -A app.workers.celery_app worker`，而 `celery_app.py` 只有一个 `ping` 占位任务；backend 副本默认 2 个、HPA 至 6 个，且 `SCHEDULER_ENABLED=true`，每个副本都会运行自己的 APScheduler tick。
- 应用数据层已可经 env 外置（`DATABASE_URL`、`REDIS_URL`、`MINIO_*`，见 `backend/app/config.py`），无需代码分支。
- `app/db/locks.py` 的 advisory lock 机制已在订阅同步中使用（`subscriptions.py` 按订阅粒度互斥），可复用于调度器级互斥。

## 决策（Decision）

1. **单一构建产物**：全仓只有一个镜像与一套 tag 规则；所有部署形态消费同一镜像，不在任何形态中二次构建或打入形态特定分支。
2. **多部署形态，统一治理**：允许存在多份部署 manifest——`deploy/allin1/docker-compose.yml`（单机：应用 + PostgreSQL + Redis + MinIO + nginx 同容器，默认交付形态与本地冒烟目标）、`deploy/helm/bidcopilot/`（客户 K8s 平台）、`deploy/external/docker-compose.yml`（数据层分离的 VM 部署，兼作 SaaS 数据层外置的证据形态）。每份形态必须在文档中标注冒烟证据；无冒烟证据的形态不得写进交付文档。
3. **本地冒烟固定走单容器**：本地测试与单机生产保持同一镜像、同一容器名（`bidcopilot-app`）、同一份 allin1 compose；禁止在宿主机旁路启动 uvicorn / pnpm dev；单机形态唯一启动入口为 `scripts/run_local_site.sh`。
4. **后台任务唯一执行模型**：APScheduler 进程内调度 + `app/db/locks.py` 的 advisory lock 做多实例互斥（每次执行竞争锁，仅一个实例落执行）。禁止引入第二个后台执行框架；`backend/app/workers/celery_app.py` 及其依赖随本 ADR 退役。
5. **数据层外置是配置不是分支**：应用经 env 指向外部 PostgreSQL / Redis / S3 兼容存储，无代码分支；外置路径必须有冒烟证据。

## 后果（Consequences）

- AGENTS.md 硬约束「部署形态固定为单容器」改写为「单一构建产物，多部署形态」，禁止旁路启动与无证据形态的条款保留。
- `deploy/helm/` 从未治理的第二真相变为受治理形态之一：删除幻影 worker deployment；backend 多副本的调度器互斥由 advisory lock 保证；随后补首次 helm 冒烟。
- SaaS 化不再是「第三种拓扑」，而是「同一镜像 + 外置托管数据层 + 租户开通/计费产品功能」；VM + compose 即可起步，规模需要时再上 K8s。
- 新增 `deploy/external/` 形态，其冒烟同时作为 SaaS 数据层外置的证据。

## 备选方案（Alternatives）

- **维持单拓扑禁令**：放弃 K8s 与 SaaS 交付需求，与产品策略冲突，否决。
- **为 K8s 引入 Celery worker**：形成 APScheduler + Celery 双后台执行模型（双 owner、双故障面），且现有 Celery 仅是 ping 占位；否决，改由 advisory lock 解决多副本互斥。
- **Helm 保留但不治理**：README 教客户使用一套未验证且与执行模型不符的拓扑，与硬约束矛盾；否决。

## 验证要求（Verification）

- 单容器全链路冒烟：既有验收快照维持有效（`scripts/run_local_site.sh` + `scripts/smoke_demo.py`）。
- 调度器互斥：多实例并发 tick 时仅一个实例执行（测试覆盖）。
- Helm：模板渲染校验 + 一次真实 install 冒烟，命令与输出记录在案。
- 外置数据层：`deploy/external` 启动 + `/healthz` + API 冒烟，命令与输出记录在案。
