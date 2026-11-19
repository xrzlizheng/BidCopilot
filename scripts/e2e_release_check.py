"""对运行中的单机部署做发布验收：用真实 HTTP 请求覆盖 Review-1 的修复项。

与 pytest 集成测试的区别：这里打的是已部署实例（nginx + app + PG + Redis 同容器），
验证的是「镜像里跑的代码」而不是「工作区里的代码」。

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/e2e_release_check.py --base-url http://127.0.0.1:18080

口令从 BIDCOPILOT_DEMO_PASSWORD 或 --password 读取，无内置默认值；缺失时启动即报错退出。

退出码：全部 PASS 为 0，任一 FAIL 为 1。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

TENDER = """
某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
"""

CONTRACT = """
合同总价：人民币 900000 元。
工期：120 天。
质保期：1 年。
付款方式：验收后 30 日内支付。
"""

CONTRACT_ZERO = """
合同总价：人民币 950000 元。
工期：120 天。
质保期：1 年。
验收标准：按招标文件执行。
"""

UNSAFE_CODE = 'QA"code; 2026/01'
EXPECTED_CODE = "QA_code__2026_01"


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    failed: int = 0

    def ok(self, check_id: str, detail: str) -> None:
        self.rows.append((check_id, "PASS", detail))

    def fail(self, check_id: str, detail: str) -> None:
        self.rows.append((check_id, "FAIL", detail))
        self.failed += 1

    def expect(self, check_id: str, actual: Any, expected: Any, detail: str = "") -> bool:
        if actual == expected:
            self.ok(check_id, detail or f"{actual}")
            return True
        self.fail(check_id, f"期望 {expected}，实际 {actual} {detail}".strip())
        return False

    def render(self) -> str:
        width = max(len(row[0]) for row in self.rows) if self.rows else 4
        lines = [f"{'检查项'.ljust(width)}  结果  说明", "-" * (width + 40)]
        for check_id, status, detail in self.rows:
            lines.append(f"{check_id.ljust(width)}  {status}  {detail}")
        return "\n".join(lines)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: httpx.Client, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


def _create_project(client: httpx.Client, token: str, name: str) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "code": UNSAFE_CODE},
        headers=_headers(token),
    )
    response.raise_for_status()
    return response.json()["id"]


def _upload(client: httpx.Client, token: str, project_id: str) -> None:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
        headers=_headers(token),
    )
    response.raise_for_status()


def _review(client: httpx.Client, token: str, project_id: str, content: str, name: str):
    return client.post(
        f"/api/v1/projects/{project_id}/contract-reviews",
        files={"file": (name, content.encode(), "text/plain")},
        headers=_headers(token),
    )


def run(args: argparse.Namespace) -> int:
    report = Report()
    stamp = time.strftime("%H%M%S")
    qa_email = f"qa-engineer-{stamp}@example.com"
    qa_password = "QaPassw0rd!"
    project_id = ""
    qa_user_id = ""
    watch_id = ""

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        # L0 存活
        report.expect("L0  /healthz", client.get("/healthz").status_code, 200)

        # L1 登录
        try:
            token = _login(client, args.email, args.password)
        except Exception as exc:  # noqa: BLE001
            report.fail("L1  登录", f"{type(exc).__name__}: {exc}")
            print(report.render())
            return 1
        report.ok("L1  登录", args.email)

        # [P2-1] 能力已下发 + 决策设置可写（修复前为 403）
        caps = client.get("/api/v1/auth/capabilities", headers=_headers(token)).json()
        report.expect(
            "P2-1a 能力下发",
            "tenant:settings:manage" in caps.get("permissions", []),
            True,
            "tenant_admin 持有 tenant:settings:manage",
        )
        settings = client.get("/api/v1/system/decision-settings", headers=_headers(token))
        report.expect("P2-1b 读取决策设置", settings.status_code, 200)
        if settings.status_code == 200:
            current = settings.json()
            payload = {
                "weights": current["weights"],
                "thresholds": current["thresholds"],
                "cost_model": current["cost_model"],
                "reminders": current["reminders"],
            }
            saved = client.put(
                "/api/v1/system/decision-settings", json=payload, headers=_headers(token)
            )
            report.expect(
                "P2-1c 写入决策设置",
                saved.status_code,
                200,
                "修复前此处为 403 no_capability" if saved.status_code != 200 else "",
            )

        # [P1-4] 只读角色不得增删关注采购人
        created = client.post(
            "/api/v1/users",
            json={
                "email": qa_email,
                "password": qa_password,
                "display_name": "验收-标书工程师",
                "role_codes": ["bid_engineer"],
            },
            headers=_headers(token),
        )
        if created.status_code in (200, 201):
            qa_user_id = created.json()["id"]
            try:
                qa_token = _login(client, qa_email, qa_password)
                denied = client.post(
                    "/api/v1/opportunities/buyer-watchlist",
                    json={"buyer_name": f"验收采购人-{stamp}", "note": "e2e"},
                    headers=_headers(qa_token),
                )
                report.expect(
                    "P1-4a 只读角色增删",
                    denied.status_code,
                    403,
                    "bid_engineer 无 opportunity:write",
                )
            except Exception as exc:  # noqa: BLE001
                report.fail("P1-4a 只读角色增删", f"{type(exc).__name__}: {exc}")
        else:
            report.fail("P1-4a 只读角色增删", f"创建验收用户失败：{created.status_code}")

        allowed = client.post(
            "/api/v1/opportunities/buyer-watchlist",
            json={"buyer_name": f"验收采购人-{stamp}", "note": "e2e"},
            headers=_headers(token),
        )
        report.expect("P1-4b 管理员增删", allowed.status_code, 201)
        if allowed.status_code == 201:
            watch_id = allowed.json()["id"]
            removed = client.delete(
                f"/api/v1/opportunities/buyer-watchlist/{watch_id}", headers=_headers(token)
            )
            report.expect("P1-4c 删除关注", removed.status_code, 204)
            watch_id = ""

        # [P1-1] 0 元报价不触发除零
        try:
            project_id = _create_project(client, token, f"E2E 验收 {stamp}")
            _upload(client, token, project_id)
            snapshot = client.post(
                f"/api/v1/projects/{project_id}/commitments/snapshot",
                json={"quote_amount": 0},
                headers=_headers(token),
            )
            report.expect("P1-1a 0 元承诺快照", snapshot.status_code, 200)
            reviewed = _review(client, token, project_id, CONTRACT, "contract.txt")
            report.expect(
                "P1-1b 0 元报价审查",
                reviewed.status_code,
                200,
                "" if reviewed.status_code == 200 else reviewed.text[:120],
            )
            if reviewed.status_code == 200:
                rule_ids = {f["rule_id"] for f in reviewed.json()["findings"]}
                report.expect(
                    "P1-1c 不产出价格偏离",
                    "commitment_price_mismatch" in rule_ids,
                    False,
                )

            # 重复上传同一文件：唯一约束命中应为 409，而不是 503
            duplicate = _review(client, token, project_id, CONTRACT, "contract.txt")
            body = duplicate.json() if duplicate.content else {}
            report.expect(
                "DUP  重复上传合同",
                (duplicate.status_code, body.get("code")),
                (409, "conflict"),
                "" if duplicate.status_code == 409 else duplicate.text[:120],
            )

            # [P1-3] 证据包文件名白名单
            pack = client.get(
                f"/api/v1/projects/{project_id}/evidence-pack", headers=_headers(token)
            )
            disposition = pack.headers.get("content-disposition", "")
            report.expect(
                "P1-3 导出文件名",
                f"evidence-pack-{EXPECTED_CODE}.zip" in disposition,
                True,
                disposition,
            )
        except Exception as exc:  # noqa: BLE001
            report.fail("P1-1 项目链路", f"{type(exc).__name__}: {exc}")

        # 前端可达性
        page = client.get("/login", follow_redirects=True)
        report.expect("FE   /login 页面", page.status_code, 200)

    # 清理：禁用验收用户
    if qa_user_id:
        with (
            httpx.Client(base_url=args.base_url, timeout=30.0) as client,
            contextlib.suppress(Exception),
        ):
            client.patch(
                f"/api/v1/users/{qa_user_id}",
                json={"status": "disabled"},
                headers=_headers(_login(client, args.email, args.password)),
            )

    print(report.render())
    if project_id:
        print(f"\n验收项目 id（可删）：{project_id}")
    print(f"\n结果：{len(report.rows) - report.failed} PASS / {report.failed} FAIL")
    if args.json:
        payload = [
            dict(zip(("id", "status", "detail"), row, strict=True)) for row in report.rows
        ]
        print(json.dumps(payload, ensure_ascii=False))
    return 1 if report.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="单机部署发布验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("BIDCOPILOT_DEMO_PASSWORD"),
        help="demo tenant password (or set BIDCOPILOT_DEMO_PASSWORD; no built-in default)",
    )
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 结果")
    args = parser.parse_args()
    if not args.password:
        print(
            "demo password required: 请设置 BIDCOPILOT_DEMO_PASSWORD 或传 --password"
            "（脚本不内置默认口令）",
            file=sys.stderr,
        )
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
