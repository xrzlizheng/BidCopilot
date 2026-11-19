"""对已部署实例做全链路功能巡检（真实依赖，无 mock）。

覆盖：登录 → 建项目 → 上传招标文件 → 约束/评分项 → 大纲 → LLM 生成 → 事实核查
→ 合规矩阵 → 逐条响应 → 决策卡 → 资格审查 → 承诺快照 → 合同审查（含重复上传）
→ 证据包 → 自检 → 答辩提纲 → 标书导出 → 制度问答 → 跨租户隔离 → 审计。

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/e2e_functional_sweep.py --base-url http://127.0.0.1:18080

口令从 BIDCOPILOT_DEMO_PASSWORD 或 --password 读取，无内置默认值；缺失时启动即报错退出。

退出码：全部 PASS 为 0，任一 FAIL 为 1。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

TENDER = """某市政务云运维服务项目招标文件
项目编号：ZB-2026-1027
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质，且近三年无重大违法记录。
评标办法：综合评分法
评分标准：
技术方案（40分）：方案完整性、可行性、针对性。
项目团队（20分）：项目经理具备PMP证书得5分。
售后服务（20分）：响应时间不超过2小时。
投标报价（20分）：满足招标文件要求且最低价得满分。
"""

CONTRACT = """
合同总价：人民币 900000 元。
工期：120 天。
质保期：1 年。
付款方式：验收后 30 日内支付。
"""


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    failed: int = 0
    started: float = field(default_factory=time.time)

    def _emit(self, cid: str, status: str, detail: str) -> None:
        print(f"[{time.time() - self.started:6.1f}s] {cid}  {status}  {detail}", flush=True)

    def ok(self, cid: str, detail: str = "") -> None:
        self.rows.append((cid, "PASS", detail))
        self._emit(cid, "PASS", detail)

    def fail(self, cid: str, detail: str) -> None:
        self.rows.append((cid, "FAIL", detail))
        self.failed += 1
        self._emit(cid, "FAIL", detail)

    def expect(self, cid: str, actual: Any, expected: Any, detail: str = "") -> bool:
        if actual == expected:
            self.ok(cid, detail or str(actual))
            return True
        self.fail(cid, f"期望 {expected}，实际 {actual} {detail}".strip())
        return False

    def render(self) -> str:
        width = max((len(r[0]) for r in self.rows), default=6)
        out = [f"{'检查项'.ljust(width)}  结果  说明", "-" * (width + 46)]
        for cid, status, detail in self.rows:
            out.append(f"{cid.ljust(width)}  {status}  {detail}")
        return "\n".join(out)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_docx(payload: bytes) -> tuple[bool, str]:
    if not payload.startswith(b"PK"):
        return False, "非 zip 容器"
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return False, "zip 解析失败"
    has_doc = "word/document.xml" in names
    return has_doc, f"{len(names)} parts, document.xml={'yes' if has_doc else 'no'}"


def _tender_payload(args: argparse.Namespace) -> tuple[str, bytes, str]:
    """返回 (文件名, 内容, content-type)；未指定 --tender-file 时用内置样例文本。"""
    if not args.tender_file:
        return "tender.txt", TENDER.encode(), "text/plain"
    path = Path(args.tender_file)
    data = path.read_bytes()
    suffix = path.suffix.lower()
    content_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")
    return path.name, data, content_type


def run(args: argparse.Namespace) -> int:
    rep = Report()
    stamp = time.strftime("%H%M%S")
    project_id = ""

    with httpx.Client(base_url=args.base_url, timeout=300.0) as c:
        rep.expect("L0  存活", c.get("/healthz").status_code, 200)

        login = c.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        rep.expect("L1  登录", login.status_code, 200)
        if login.status_code != 200:
            print(rep.render())
            return 1
        token = login.json()["access_token"]
        h = _h(token)

        created = c.post(
            "/api/v1/projects",
            json={"name": f"全链路巡检 {stamp}", "code": f"SWEEP-{stamp}"},
            headers=h,
        )
        rep.expect("P1  建项目", created.status_code, 201)
        if created.status_code != 201:
            print(rep.render())
            return 1
        project_id = created.json()["id"]

        tender_name, tender_bytes, tender_type = _tender_payload(args)
        up = c.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"file": (tender_name, tender_bytes, tender_type)},
            headers=h,
        )
        rep.expect("P2  上传招标文件", up.status_code, 200, f"{tender_name} {len(tender_bytes)}B")

        constraint = c.get(f"/api/v1/projects/{project_id}/constraints", headers=h)
        budget = ""
        if constraint.status_code == 200:
            payload = constraint.json()
            rows = payload if isinstance(payload, list) else [payload]
            body = (rows[0] or {}).get("payload", {}) if rows else {}
            budget = str((body.get("budget") or {}).get("amount") or "")
        rep.expect("P3  约束抽取", bool(budget), True, f"预算={budget}")

        scoring = c.get(f"/api/v1/projects/{project_id}/scoring-items", headers=h)
        rep.expect("P4  评分项", scoring.status_code, 200)
        scoring_count = len(scoring.json()) if scoring.status_code == 200 else 0
        rep.expect("P4b 评分项非空", scoring_count > 0, True, f"{scoring_count} 条")

        outline = c.post(f"/api/v1/projects/{project_id}/outline", headers=h)
        sections = outline.json() if outline.status_code == 200 else []
        rep.expect("P5  大纲生成", len(sections) > 0, True, f"{len(sections)} 章节")

        target = next((s for s in sections if s["section_type"] == "scoring"), None) or (
            sections[0] if sections else None
        )
        if target is None:
            rep.fail("P6  LLM 生成", "无可用章节")
        else:
            gen = c.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "llm", "section_ids": [target["id"]]},
                headers=h,
            )
            rep.expect("P6  LLM 生成", gen.status_code, 200)
            after = next(
                (
                    s
                    for s in c.get(f"/api/v1/projects/{project_id}/sections", headers=h).json()
                    if s["id"] == target["id"]
                ),
                {},
            )
            meta = after.get("ai_meta") or {}
            text = after.get("content_text") or ""
            rep.expect(
                "P6b 真实模型产出",
                (meta.get("mode"), bool(meta.get("model"))),
                ("llm", True),
                f"model={meta.get('model')} len={len(text)}",
            )

            fact = c.post(f"/api/v1/sections/{target['id']}/factcheck", headers=h)
            rep.expect("P7  事实核查", fact.status_code, 200)
            if fact.status_code == 200:
                status = (fact.json().get("section") or {}).get("factcheck_status")
                rep.expect(
                    "P7b 核查状态", status in {"passed", "needs_review"}, True, str(status)
                )

        matrix = c.post(f"/api/v1/projects/{project_id}/matrix", headers=h)
        rep.expect("P8  合规矩阵", matrix.status_code, 200)
        if matrix.status_code == 200:
            rep.expect("P8b 矩阵非空", len(matrix.json()) > 0, True, f"{len(matrix.json())} 行")

        build = c.post(f"/api/v1/projects/{project_id}/responses/build", headers=h)
        rep.expect("P9  逐条响应生成", build.status_code, 200)
        if build.status_code == 200:
            total = build.json().get("total")
            rep.expect("P9b 响应条数", isinstance(total, int) and total > 0, True, f"total={total}")

        qual = c.post(f"/api/v1/projects/{project_id}/qualification-check", headers=h)
        rep.expect("P10 资格审查", qual.status_code, 200)

        card = c.post(f"/api/v1/projects/{project_id}/decision-card", json={}, headers=h)
        rep.expect("P11 决策卡计算", card.status_code, 200)
        if card.status_code == 200:
            body = card.json()
            rep.expect(
                "P11b 决策结论",
                bool(body.get("computed_status") or body.get("status")),
                True,
                f"{body.get('computed_status') or body.get('status')}",
            )

        snap = c.post(
            f"/api/v1/projects/{project_id}/commitments/snapshot",
            json={
                "quote_amount": 1000000,
                "duration_days": 90,
                "warranty_days": 365,
                "personnel": ["张三"],
            },
            headers=h,
        )
        rep.expect("P12 承诺快照", snap.status_code, 200)
        if snap.status_code == 200:
            kinds = {row["kind"] for row in snap.json()}
            rep.expect("P12b 承诺类型", {"price", "schedule", "warranty"} <= kinds, True, str(sorted(kinds)))

        review = c.post(
            f"/api/v1/projects/{project_id}/contract-reviews",
            files={"file": ("contract.txt", CONTRACT.encode(), "text/plain")},
            headers=h,
        )
        rep.expect("P13 合同审查", review.status_code, 200)
        if review.status_code == 200:
            findings = review.json().get("findings") or []
            rep.ok("P13b 审查产出", f"{len(findings)} 条 findings")

        dup = c.post(
            f"/api/v1/projects/{project_id}/contract-reviews",
            files={"file": ("contract.txt", CONTRACT.encode(), "text/plain")},
            headers=h,
        )
        rep.expect(
            "P14 重复上传拦截",
            (dup.status_code, (dup.json() if dup.content else {}).get("code")),
            (409, "conflict"),
        )

        pack = c.get(f"/api/v1/projects/{project_id}/evidence-pack", headers=h)
        rep.expect("P15 证据包导出", pack.status_code, 200)
        if pack.status_code == 200:
            rep.expect("P15b 证据包内容", pack.content[:2], b"PK", f"{len(pack.content)} bytes")

        check = c.get(f"/api/v1/projects/{project_id}/self-check", headers=h)
        rep.expect("P16 投标自检", check.status_code, 200)
        if check.status_code == 200:
            items = check.json().get("items") or []
            rep.ok("P16b 自检项", f"{len(items)} 项")

        defense = c.get(
            f"/api/v1/projects/{project_id}/defense-outline", params={"minutes": 20}, headers=h
        )
        rep.expect("P17 答辩提纲", defense.status_code, 200)
        if defense.status_code == 200:
            slides = defense.json().get("slides") or []
            rep.expect("P17b 提纲页数", len(slides) > 0, True, f"{len(slides)} 页")

        # 导出闸门：章节未审批时必须拒绝，且给出可读原因
        gate = c.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False, "acknowledge_unresolved": True},
            headers=h,
        )
        gate_msg = ""
        if gate.headers.get("content-type", "").startswith("application/json"):
            gate_msg = str(gate.json().get("message", ""))[:40]
        rep.expect("P18 导出闸门", gate.status_code, 422, gate_msg)

        approved = 0
        for section in sections:
            # 审批有前置门禁：必须先完成事实核查
            c.post(f"/api/v1/sections/{section['id']}/factcheck", headers=h)
            res = c.post(
                f"/api/v1/sections/{section['id']}/approve",
                json={"acknowledge_unsourced": True},
                headers=h,
            )
            if res.status_code == 200:
                approved += 1
        rep.expect("P19 章节审批", approved, len(sections), f"{approved}/{len(sections)}")

        # AI 评标：走 Judge 模型（LLM-as-judge），与生成模型分开配置
        evaluation = c.post(f"/api/v1/projects/{project_id}/evaluations", headers=h)
        rep.expect("P20 AI 评标", evaluation.status_code, 201)
        if evaluation.status_code == 201:
            ev = evaluation.json()
            result = ev.get("result_json") or {}
            items_scored = result.get("items") or result.get("scores") or []
            rep.expect("P20b 评标状态", ev.get("status"), "completed", str(ev.get("error_message") or ""))
            rep.ok(
                "P20c 判别模型",
                f"model={ev.get('model')} 评分项={len(items_scored)}",
            )

        export = c.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False, "acknowledge_unresolved": True},
            headers=h,
        )
        rep.expect("P21 标书导出", export.status_code, 200)
        if export.status_code == 200:
            good, detail = _is_docx(export.content)
            rep.expect("P21b docx 结构", good, True, f"{len(export.content)} bytes / {detail}")

        qa = c.post(
            f"/api/v1/projects/{project_id}/policy-qa",
            json={"question": "投标保证金最迟应在什么时候提交？"},
            headers=h,
        )
        rep.expect("P22 制度问答", qa.status_code, 200)
        if qa.status_code == 200:
            body = qa.json()
            answered = bool(body.get("answer"))
            has_sources = bool(body.get("sources"))
            rep.ok(
                "P22b 问答守则",
                f"answer={answered} sources={len(body.get('sources') or [])} "
                f"{'有据作答' if has_sources else '无据拒答'}",
            )

        ghost = c.get(f"/api/v1/projects/{uuid.uuid4()}", headers=h)
        rep.expect("P23 跨租户/不存在隔离", ghost.status_code, 404)

        audit = c.get("/api/v1/audit/events", params={"limit": 5}, headers=h)
        rep.expect("P24 审计可查", audit.status_code, 200)
        if audit.status_code == 200:
            data = audit.json()
            rows = data.get("items") if isinstance(data, dict) else data
            rep.ok("P24b 审计记录", f"{len(rows or [])} 条")

    print(rep.render())
    if project_id:
        print(f"\n巡检项目 id（可删）：{project_id}")
    print(f"\n结果：{len(rep.rows) - rep.failed} PASS / {rep.failed} FAIL")
    if args.json:
        payload = [dict(zip(("id", "status", "detail"), r, strict=True)) for r in rep.rows]
        print(json.dumps(payload, ensure_ascii=False))
    return 1 if rep.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="部署实例全链路功能巡检")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("BIDCOPILOT_DEMO_PASSWORD"),
        help="demo tenant password (or set BIDCOPILOT_DEMO_PASSWORD; no built-in default)",
    )
    parser.add_argument("--tender-file", default="", help="用真实招标文件跑全流程")
    parser.add_argument("--json", action="store_true")
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
