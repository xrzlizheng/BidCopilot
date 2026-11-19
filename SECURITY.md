# 安全策略

## 报告漏洞

请勿通过公开 Issue 报告安全漏洞。通过私有渠道联系维护者（仓库 Owner 的邮箱见 GitHub 个人主页），或在 GitHub 上使用 [私密漏洞报告](https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-reviewing/privately-reporting-a-security-vulnerability)（开启后入口在本仓库 Security 标签页）。

我们会在 7 天内确认收到报告，修复与披露节奏与报告者协商。

## 凭据红线

- 任何 `.env`、AppCode、API Key 不得进入版本库；CI 与密钥扫描（`scripts/check_secrets.py`）会拦截常见格式。
- 提交前自查：`git grep -nE "(sk-|ghp_|glpat-|AKIA)[A-Za-z0-9_-]{16,}"` 以及 `python scripts/check_secrets.py`。
- 误提交过的密钥应视为已泄露：先在供应商侧吊销，再清理历史，仅删除文件是不够的。

## 安全设计要点（供审阅）

- 多租户隔离：所有业务查询强制 `tenant_id` 过滤，跨租户按 ID 读取一律 404，不泄露存在性（详见 `AGENTS.md`）。
- 外部公告正文按不可信 HTML 处理：服务端白名单清洗、URL scheme 校验、正文大小上限。
- 改密 / 停用 / 角色变更递增用户 token 版本，旧 access/refresh token 立即失效。
- 对外付费网关（KeepRisk / LLM / OCR / 联网搜索）调用全部落成本台账与审计链，异常不静默重试。
