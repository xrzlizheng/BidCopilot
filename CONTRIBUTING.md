# 贡献指南

感谢关注 BidCopilot。开始贡献前请先读完本文与 [AGENTS.md](AGENTS.md)（项目硬约束）。

## 开发环境

见 [README.md](README.md) 的「快速启动」。要点：站点唯一启动入口是 `bash scripts/run_local_site.sh`（单容器），不要在宿主机旁路启动 uvicorn / pnpm dev 作为站点。

## 提交前自查（全部必须通过）

```bash
cd backend
uv run pytest -q                                   # 单元 + 集成（集成需真实 PostgreSQL）
uv run python ../scripts/check_architecture.py
uv run python ../scripts/check_secrets.py

cd ../frontend
pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

## 硬约束（摘自 AGENTS.md，冲突时以 AGENTS.md 为准）

- 生产代码禁止 Mock；外部数据只经 `app/modules/gateway` 进来。
- `DataResult.status` 原样透传到 API 与前端，非 `ok`/`empty` 不降级、不静默重试。
- 迁移只前进（additive），不做 destructive downgrade。
- 所有验收数字必须随命令与选择器记录，不转抄他人报的"实测值"。
- 脚本必须 LF 换行（Windows 上注意 git 的 autocrlf 行为）。
- 凭据红线见 [SECURITY.md](SECURITY.md)。

## 提交规范

- Conventional commits，中文主题：`feat(opportunities): …`、`fix(export): …`、`docs: …`。
- 一次提交一个主题；验收数字写进提交说明或 README 快照。
