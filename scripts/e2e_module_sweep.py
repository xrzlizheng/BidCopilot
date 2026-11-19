"""业务模块真实数据巡检：覆盖全链路脚本没碰到的模块。

重点验「有数据才会走到的分支」：
- 知识库 RAG：上传真实制度文档 → 检索命中 → 制度问答**有据作答并带来源**（空库时只能拒答）
- 资产库：企业 / 资质 / 人员 / 业绩 / 信用 + CSV 导入 + 到期提醒
- 组织架构：部门 / 成员 / 树
- 商机：列表 / 详情 / 收藏 / 采购人情报 / 覆盖度 / 订阅
- 通知：未读计数 / 标记已读
- 合规：访问申请 → 审批/拒绝；个人资源
- 审计：哈希链校验
- 联网检索：真实调用外部搜索
- 制度引用核验、分析看板、模型配置读取

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/e2e_module_sweep.py --base-url http://127.0.0.1:18080

口令从 BIDCOPILOT_DEMO_PASSWORD 或 --password 读取，无内置默认值；缺失时启动即报错退出。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

TENDER = """某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
评标办法：综合评分法
评分标准：
技术方案（40分）：方案完整性、可行性、针对性。
项目团队（20分）：项目经理具备PMP证书得5分。
售后服务（20分）：响应时间不超过2小时。
投标报价（20分）：满足招标文件要求且最低价得满分。
"""

DG_POLICY = """# 投标保证金管理办法

## 第一条 提交时限
投标保证金须在投标截止时间前足额提交，逾期未提交的投标文件将被否决。

## 第二条 金额上限
投标保证金不得超过招标项目估算价的百分之二。

## 第三条 退还
未中标人的投标保证金应在中标通知书发出后五个工作日内退还。

## 第四条 履约保证金
履约保证金不得超过中标合同金额的百分之十。
"""

TECH_POLICY = """# 技术方案编制规范

## 一、总体要求
技术方案须逐条响应评分标准，包含方案完整性、可行性与针对性三个维度。

## 二、售后服务
售后服务响应时间不得超过 2 小时，须提供 7×24 小时值班安排。

## 三、安全要求
涉及数据处理的方案须说明数据安全保密措施与等级保护合规情况。
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
        out = [f"{'检查项'.ljust(width)}  结果  说明", "-" * (width + 50)]
        for cid, status, detail in self.rows:
            out.append(f"{cid.ljust(width)}  {status}  {detail}")
        return "\n".join(out)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _csv(rows: list[dict[str, str]]) -> bytes:
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    lines += [",".join(str(row.get(h, "")) for h in headers) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def run(args: argparse.Namespace) -> int:
    rep = Report()
    stamp = time.strftime("%H%M%S")
    scratch_id = ""
    cleanup: dict[str, list[str]] = {}

    with httpx.Client(base_url=args.base_url, timeout=180.0) as c:
        rep.expect("L0  存活", c.get("/healthz").status_code, 200)
        login = c.post(
            "/api/v1/auth/login", json={"email": args.email, "password": args.password}
        )
        rep.expect("L1  登录", login.status_code, 200)
        if login.status_code != 200:
            print(rep.render())
            return 1
        token = login.json()["access_token"]
        h = _h(token)
        me = c.get("/api/v1/auth/me", headers=h).json()
        user_id = me["id"]

        # ---------- 知识库 RAG ----------
        kb = c.post(
            "/api/v1/knowledge/bases",
            json={
                "name": f"巡检制度库 {stamp}",
                "description": "真实制度文档检索验证",
                "scope": "tenant",
                "visibility": "enterprise",
                "classification": "internal",
            },
            headers=h,
        )
        rep.expect("K1  建知识库", kb.status_code, 201)
        kb_id = kb.json()["id"] if kb.status_code == 201 else None
        if kb_id:
            cleanup["kb"] = [kb_id]
            doc_ids = []
            for name, body, kind in (
                ("投标保证金管理办法.md", DG_POLICY, "policy"),
                ("技术方案编制规范.md", TECH_POLICY, "policy"),
            ):
                up = c.post(
                    "/api/v1/knowledge/documents",
                    files={"file": (name, body.encode(), "text/markdown")},
                    data={"kb_id": kb_id, "kind": kind, "title": name, "tags": "制度,巡检"},
                    headers=h,
                )
                rep.expect(f"K2  上传 {name}", up.status_code, 200, up.text[:80])
                if up.status_code == 200:
                    doc_ids.append(up.json()["id"])
            rep.expect("K2b 文档数", len(doc_ids), 2)

            found = c.get(
                "/api/v1/knowledge/search",
                params={"q": "投标保证金 提交时限", "limit": 4, "kb_id": kb_id},
                headers=h,
            )
            rep.expect("K3  检索接口", found.status_code, 200)
            chunks = found.json().get("chunks", []) if found.status_code == 200 else []
            rep.expect("K3b 检索命中", len(chunks) > 0, True, f"{len(chunks)} 个片段")
            if chunks:
                hit_text = str(chunks[0].get("chunk") or "")[:60]
                rep.ok("K3c 命中内容", hit_text.replace("\n", " "))

            # 文档级 ACL：个人库之外可授权
            acl = c.post(
                f"/api/v1/knowledge/bases/{kb_id}/acl",
                json={"subject_type": "user", "subject_id": user_id, "role": "viewer"},
                headers=h,
            )
            rep.expect("K4  知识库授权", acl.status_code, 201)
            if acl.status_code == 201:
                revoke = c.delete(
                    f"/api/v1/knowledge/bases/{kb_id}/acl/{acl.json()['id']}", headers=h
                )
                rep.expect("K4b 撤销授权", revoke.status_code, 204)

        # 制度问答：知识库有内容后应「有据作答」
        proj = c.get("/api/v1/projects", params={"page_size": 1}, headers=h)
        rows = proj.json() if proj.status_code == 200 else []
        rows = rows.get("items", []) if isinstance(rows, dict) else rows
        if rows:
            pid = rows[0]["id"]
            qa = c.post(
                f"/api/v1/projects/{pid}/policy-qa",
                json={"question": "投标保证金最迟应在什么时候提交？"},
                headers=h,
            )
            rep.expect("K5  制度问答", qa.status_code, 200)
            if qa.status_code == 200:
                body = qa.json()
                sources = body.get("sources") or []
                rep.expect(
                    "K5b 有据作答",
                    len(sources) > 0,
                    True,
                    f"来源 {len(sources)} 条：{[s.get('title') for s in sources][:2]}",
                )
                answer = str(body.get("answer") or "")
                rep.ok("K5c 答案引用", f"{len(answer)} 字，含[n]标注={'[1]' in answer}")

        # ---------- 资产库 ----------
        company = c.post(
            "/api/v1/companies",
            json={
                "name": f"巡检科技有限公司 {stamp}",
                "credit_code": f"91340100{stamp}X",
                "region": "安徽省合肥市",
                "industry": "软件和信息技术服务业",
                "contacts": {"phone": "0551-0000000", "email": "qa@example.com"},
            },
            headers=h,
        )
        rep.expect("A1  建企业", company.status_code, 201)
        company_id = company.json()["id"] if company.status_code == 201 else None
        if company_id:
            cleanup["company"] = [company_id]
            assets = (
                ("qualifications", {"name": "信息系统集成资质", "level": "乙级", "cert_no": "IT-2026-001", "valid_to": "2028-12-31"}),
                ("personnel", {"name": "张三", "cert_type": "PMP", "cert_no": "PMP-2026-777", "major": "项目管理"}),
                ("performances", {"project_name": "某市政务云运维", "amount": 1200000, "complete_date": "2026-06-30", "buyer": "某市大数据局"}),
                ("credits", {"check_item": "信用中国", "status": "正常"}),
            )
            kinds_ok = []
            for kind, payload in assets:
                body = {"company_id": company_id, **payload}
                res = c.post(f"/api/v1/assets/{kind}", json=body, headers=h)
                if res.status_code in (200, 201):
                    kinds_ok.append(kind)
                else:
                    rep.fail(f"A2  新增{kind}", f"{res.status_code} {res.text[:90]}")
            rep.expect("A2  四类资产", len(kinds_ok), 4, str(kinds_ok))

            for kind in ("qualifications", "personnel", "performances", "credits"):
                listed = c.get(f"/api/v1/assets/{kind}", params={"company_id": company_id}, headers=h)
                rep.expect(f"A3  查询{kind}", listed.status_code, 200)

            expiring = c.get("/api/v1/assets/expiring", params={"days": 1200}, headers=h)
            rep.expect("A4  到期提醒", expiring.status_code, 200)
            if expiring.status_code == 200:
                payload = expiring.json()
                total = payload.get("total") if isinstance(payload, dict) else len(payload)
                rep.ok("A4b 到期条数", str(total))

            company_name = company.json()["name"]
            csv_bytes = _csv(
                [
                    {
                        "company_name": company_name,
                        "name": "质量管理体系认证",
                        "cert_no": "ISO-9001-2026",
                        "level": "国家级",
                        "valid_to": "2029-01-31",
                    },
                    {
                        "company_name": company_name,
                        "name": "信息安全管理认证",
                        "cert_no": "ISO-27001-2026",
                        "level": "国家级",
                        "valid_to": "2029-01-31",
                    },
                ]
            )
            imported = c.post(
                "/api/v1/assets/import",
                files={"file": ("qualifications.csv", csv_bytes, "text/csv")},
                data={"kind": "qualifications", "company_id": company_id} if True else {},
                headers=h,
            )
            rep.expect("A5  CSV 导入", imported.status_code, 200, imported.text[:100])
            if imported.status_code == 200:
                body = imported.json()
                rep.ok("A5b 导入结果", f"成功={body.get('imported')} 失败={len(body.get('errors') or [])}")

        # ---------- 组织架构 ----------
        org = c.post(
            "/api/v1/org-units",
            json={"name": f"投标部 {stamp}", "kind": "department"},
            headers=h,
        )
        rep.expect("O1  建部门", org.status_code, 201)
        org_id = org.json()["id"] if org.status_code == 201 else None
        if org_id:
            cleanup["org"] = [org_id]
            member = c.post(
                f"/api/v1/org-units/{org_id}/members", json={"user_id": user_id}, headers=h
            )
            rep.expect("O2  加成员", member.status_code in (200, 201), True, str(member.status_code))
            members = c.get(f"/api/v1/org-units/{org_id}/members", headers=h)
            rep.expect("O3  成员列表", members.status_code, 200)
            tree = c.get("/api/v1/org-units/tree", headers=h)
            rep.expect("O4  组织树", tree.status_code, 200)

        # ---------- 商机 ----------
        opps = c.get("/api/v1/opportunities", params={"page_size": 3}, headers=h)
        rep.expect("M1  商机列表", opps.status_code, 200)
        opp_rows = []
        if opps.status_code == 200:
            data = opps.json()
            opp_rows = data.get("items", [])
            rep.expect("M1b 商机非空", len(opp_rows) > 0, True, f"total={data.get('total')}")
        if opp_rows:
            oid = opp_rows[0]["id"]
            detail = c.get(f"/api/v1/opportunities/{oid}", headers=h)
            rep.expect("M2  商机详情", detail.status_code, 200)
            fav = c.post(f"/api/v1/opportunities/{oid}/favorite", headers=h)
            rep.expect("M3  收藏商机", fav.status_code in (200, 201), True, str(fav.status_code))
            intel = c.get(f"/api/v1/opportunities/{oid}/buyer-intel", headers=h)
            rep.expect(
                "M4  采购人情报（无记录）",
                intel.status_code in (200, 404),
                True,
                f"{intel.status_code}（未同步过 404 属预期）",
            )
            intel_sync = c.post(f"/api/v1/opportunities/{oid}/buyer-intel/sync", headers=h)
            rep.ok(
                "M4b 情报同步",
                f"{intel_sync.status_code} "
                f"{str(intel_sync.json().get('status') if intel_sync.status_code == 200 else intel_sync.text)[:60]}",
            )
        coverage = c.get("/api/v1/opportunities/coverage", headers=h)
        rep.expect("M5  数据覆盖度", coverage.status_code, 200)

        sub = c.post(
            "/api/v1/opportunity-subscriptions",
            json={
                "name": f"巡检订阅 {stamp}",
                "interval_minutes": 60,
                "query": {"keywords": ["政务云"], "info_types": ["1"], "page_size": 5},
            },
            headers=h,
        )
        rep.expect("M6  建订阅", sub.status_code, 200, sub.text[:90])
        sub_id = sub.json()["id"] if sub.status_code == 200 else None
        if sub_id:
            run_res = c.post(f"/api/v1/opportunity-subscriptions/{sub_id}/run", headers=h)
            rep.expect("M7  订阅执行", run_res.status_code, 200)
            if run_res.status_code == 200:
                body = run_res.json()
                rep.ok(
                    "M7b 网关状态透传",
                    f"status={body.get('status')} code={body.get('error_code')} charged={body.get('charged')}",
                )
            listed = c.get("/api/v1/opportunity-subscriptions", headers=h)
            rep.expect("M8  订阅列表", listed.status_code, 200)
            c.delete(f"/api/v1/opportunity-subscriptions/{sub_id}", headers=h)

        # ---------- 通知 ----------
        unread = c.get("/api/v1/notifications/unread-count", headers=h)
        rep.expect("N1  未读计数", unread.status_code, 200)
        notes = c.get("/api/v1/notifications", headers=h)
        rep.expect("N2  通知列表", notes.status_code, 200)
        note_rows = []
        if notes.status_code == 200:
            payload = notes.json()
            note_rows = payload.get("items", []) if isinstance(payload, dict) else payload
        if note_rows:
            read = c.post(
                "/api/v1/notifications/read",
                json={"ids": [note_rows[0]["id"]], "all": False},
                headers=h,
            )
            rep.expect("N3  标记已读", read.status_code, 200)
        else:
            rep.ok("N3  标记已读", "无通知可标（跳过，非缺陷）")

        # ---------- 合规：break-glass 双人审批（调查性权限，普通投标员不具备） ----------
        if kb_id:
            denied = c.post(
                "/api/v1/compliance/access-requests",
                json={
                    "resource_type": "knowledge_base",
                    "resource_id": kb_id,
                    "reason": "巡检验证：租户级资源不需要 break-glass 调阅",
                    "requested_minutes": 30,
                },
                headers=h,
            )
            rep.expect(
                "C0  租户级拒绝调阅",
                (denied.status_code, (denied.json() if denied.content else {}).get("message")),
                (422, "只有个人资源需要 break-glass 调阅"),
            )

        owner_email = f"qa-owner-{stamp}@example.com"
        reviewer_email = f"qa-review-{stamp}@example.com"
        owner_token = reviewer_token = None
        owner_kb_id = None
        for email, role, label in (
            (owner_email, "bid_engineer", "巡检-资料所有者"),
            (reviewer_email, "compliance", "巡检-合规审批人"),
        ):
            made = c.post(
                "/api/v1/users",
                json={
                    "email": email,
                    "password": "QaPeer1234!",
                    "display_name": label,
                    "role_codes": [role],
                },
                headers=h,
            )
            if made.status_code in (200, 201):
                cleanup.setdefault("user", []).append(made.json()["id"])
                got = c.post(
                    "/api/v1/auth/login", json={"email": email, "password": "QaPeer1234!"}
                )
                if got.status_code == 200:
                    if role == "bid_engineer":
                        owner_token = got.json()["access_token"]
                    else:
                        reviewer_token = got.json()["access_token"]

        if owner_token:
            owner_kb = c.post(
                "/api/v1/knowledge/bases",
                json={
                    "name": f"巡检私有库 {stamp}",
                    "scope": "personal",
                    "visibility": "shared",
                    "classification": "internal",
                },
                headers=_h(owner_token),
            )
            rep.expect("C1  所有者建私有库", owner_kb.status_code, 201)
            if owner_kb.status_code == 201:
                owner_kb_id = owner_kb.json()["id"]
                cleanup.setdefault("kb", []).append(owner_kb_id)

        if owner_kb_id:
            # 审批前：管理员读不到他人私有库（按 ID 读取 404，不泄露存在性）
            before = c.get(f"/api/v1/knowledge/bases/{owner_kb_id}", headers=h)
            rep.expect("C2  审批前隔离", before.status_code, 404)

            # 所有者本人申请：应被拒绝（无需申请）
            owner_try = c.post(
                "/api/v1/compliance/access-requests",
                json={
                    "resource_type": "knowledge_base",
                    "resource_id": owner_kb_id,
                    "reason": "巡检验证：所有者本人申请调阅自己的资源",
                    "requested_minutes": 30,
                },
                headers=_h(owner_token),
            )
            rep.expect(
                "C3  所有者本人被拒",
                (owner_try.status_code, (owner_try.json() if owner_try.content else {}).get("message")),
                (403, "missing permission: compliance:break_glass"),
                "普通投标员无 break-glass 能力，属预期",
            )

            # 管理员发起调阅申请
            req = c.post(
                "/api/v1/compliance/access-requests",
                json={
                    "resource_type": "knowledge_base",
                    "resource_id": owner_kb_id,
                    "reason": "巡检验证：合规检查需要核对个人资料中的承诺条款",
                    "requested_minutes": 30,
                },
                headers=h,
            )
            rep.expect("C4  提调阅申请", req.status_code, 201, req.text[:90])
            req_id = req.json()["id"] if req.status_code == 201 else None

            if req_id:
                # 申请人不得自审
                self_approve = c.post(
                    f"/api/v1/compliance/access-requests/{req_id}/approve", headers=h
                )
                rep.expect("C5  自审被拒", self_approve.status_code, 403)
                # 合规岗审批通过
                if reviewer_token:
                    approved = c.post(
                        f"/api/v1/compliance/access-requests/{req_id}/approve",
                        headers=_h(reviewer_token),
                    )
                    rep.expect(
                        "C6  双人审批通过", approved.status_code, 200, approved.text[:90]
                    )
                    if approved.status_code == 200:
                        body = approved.json()
                        rep.expect("C6b 状态与有效期", (body.get("status"), bool(body.get("expires_at"))), ("approved", True))
                        # 审批后：管理员可读他人私有库（这才是 break-glass 的价值）
                        after = c.get(f"/api/v1/knowledge/bases/{owner_kb_id}", headers=h)
                        rep.expect("C7  审批后可读", after.status_code, 200)
                else:
                    rep.fail("C6  双人审批通过", "合规岗账号不可用")
            listed = c.get("/api/v1/compliance/access-requests", headers=h)
            rep.expect("C8  申请列表", listed.status_code, 200)

        personal = c.get("/api/v1/compliance/personal-resources", headers=h)
        rep.expect("C9  个人资源", personal.status_code, 200)

        # ---------- 审计链 ----------
        verify = c.get("/api/v1/audit/verify", headers=h)
        rep.expect("D1  审计链校验", verify.status_code, 200)
        if verify.status_code == 200:
            body = verify.json()
            rep.expect("D1b 链完整", body.get("valid"), True, f"events={body.get('events')}")
        events = c.get("/api/v1/audit/events", params={"limit": 200}, headers=h)
        rep.expect("D2  审计事件", events.status_code, 200)
        if events.status_code == 200:
            payload = events.json()
            rows_ = payload.get("items") if isinstance(payload, dict) else payload
            rep.ok("D2b 事件数", str(len(rows_ or [])))

        # ---------- 联网检索（真实外部调用） ----------
        search = c.post(
            "/api/v1/web-search",
            json={"query": "政务云运维服务 招标 评分标准", "count": 3},
            headers=h,
        )
        rep.expect("W1  联网检索", search.status_code, 200, search.text[:120])
        if search.status_code == 200:
            body = search.json()
            items = body.get("results") or body.get("items") or []
            rep.expect("W1b 检索结果", len(items) > 0, True, f"{len(items)} 条")
            if items:
                rep.ok("W1c 首条标题", str(items[0].get("title"))[:40])

        # ---------- 制度引用核验 ----------
        cite = c.post(
            "/api/v1/policy-sources/verify",
            json={"texts": ["投标保证金不得超过招标项目估算价的百分之二。", "根据《招标投标法》第五十条，中标无效。"]},
            headers=h,
        )
        rep.expect("X1  引用核验", cite.status_code, 200)
        if cite.status_code == 200:
            results = cite.json().get("results") or []
            rep.ok("X1b 核验结果", f"{len(results)} 条，状态={[r.get('status') for r in results]}")

        # ---------- 项目级高级流程（就绪校验 / 文档差异 / 导出 / 模板 / 知识包） ----------
        # 必须用自建项目：模板应用会覆盖章节，不能拿演示项目当靶子
        scratch = c.post(
            "/api/v1/projects",
            json={"name": f"巡检高级流程 {stamp}", "code": f"ADV-{stamp}"},
            headers=h,
        )
        rep.expect("R0  建巡检项目", scratch.status_code, 201)
        rows_ = [scratch.json()] if scratch.status_code == 201 else []
        if rows_:
            pid = rows_[0]["id"]
            scratch_id = pid
            templates = c.get(f"/api/v1/projects/{pid}/readiness/templates", headers=h)
            rep.expect("R1  就绪模板清单", templates.status_code, 200)
            if templates.status_code == 200:
                rep.ok("R1b 就绪模板", f"{len(templates.json())} 个")
            # 结构模板来自知识包（builtin.*），不是 readiness 模板 code
            pack_list = c.get("/api/v1/knowledge-packs", headers=h)
            structure_code = None
            if pack_list.status_code == 200:
                for item in pack_list.json():
                    if item.get("category") == "structure" and item.get("enabled", True):
                        structure_code = item["code"]
                        break
            rep.expect("R1c 结构模板可用", bool(structure_code), True, str(structure_code))

            ready = c.get(f"/api/v1/projects/{pid}/readiness", headers=h)
            rep.expect("R2  就绪校验", ready.status_code, 200)
            if ready.status_code == 200:
                body = ready.json()
                items = body.get("items") or []
                rep.ok("R2b 就绪项", f"{len(items)} 项，完成率={body.get('completion_rate')}")

            refreshed = c.post(f"/api/v1/projects/{pid}/readiness/refresh", headers=h)
            rep.expect("R3  就绪刷新", refreshed.status_code, 200)

            diff = c.get(f"/api/v1/projects/{pid}/document-diff", headers=h)
            rep.expect("R4  文档版本差异", diff.status_code, 200)
            if diff.status_code == 200:
                body = diff.json()
                rep.ok(
                    "R4b 差异摘要",
                    f"added={len(body.get('added') or [])} "
                    f"removed={len(body.get('removed') or [])} "
                    f"changed={len(body.get('changed') or [])}",
                )

            materials = c.get(f"/api/v1/projects/{pid}/materials", headers=h)
            rep.expect("R5  项目素材", materials.status_code, 200)

            responses_export = c.get(f"/api/v1/projects/{pid}/responses/export", headers=h)
            rep.expect(
                "R6  响应表导出",
                responses_export.status_code,
                200,
                f"{len(responses_export.content)} bytes",
            )

            defense_export = c.get(
                f"/api/v1/projects/{pid}/defense-outline/export", headers=h
            )
            rep.expect(
                "R7  答辩提纲导出",
                defense_export.status_code,
                200,
                f"{len(defense_export.content)} bytes",
            )

            # 应用模板前必须先有已解析的招标文件（门禁正确，补上这一步）
            gate = c.put(
                f"/api/v1/projects/{pid}/template",
                json={"template_code": structure_code or "builtin.gov_it"},
                headers=h,
            )
            rep.expect(
                "R7b 无招标文件时拦截",
                (gate.status_code, (gate.json() if gate.content else {}).get("message")),
                (422, "请先上传并解析招标文件，再生成大纲"),
            )
            uploaded = c.post(
                f"/api/v1/projects/{pid}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=h,
            )
            rep.expect("R7c 上传招标文件", uploaded.status_code, 200)

            if structure_code:
                applied = c.put(
                    f"/api/v1/projects/{pid}/template",
                    json={"template_code": structure_code},
                    headers=h,
                )
                rep.expect("R8  应用结构模板", applied.status_code, 200, applied.text[:90])
                if applied.status_code == 200:
                    body = applied.json()
                    rep.ok(
                        "R8b 按模板生成章节",
                        f"sections={len(body.get('sections') or [])}",
                    )

        pack = c.post(
            "/api/v1/knowledge-packs",
            json={
                "name": f"巡检模板包 {stamp}",
                "category": "structure",
                "description": "巡检验证用的结构模板",
                "content_json": {"sections": [{"title": "投标函"}, {"title": "技术方案"}]},
                "scope": "tenant",
                "classification": "internal",
            },
            headers=h,
        )
        rep.expect("R9  建知识包", pack.status_code, 201, pack.text[:90])
        if pack.status_code == 201:
            pack_id = pack.json()["id"]
            packs = c.get("/api/v1/knowledge-packs", headers=h)
            rep.expect("R9b 知识包列表", packs.status_code, 200)
            patched = c.patch(
                f"/api/v1/knowledge-packs/{pack_id}",
                json={"description": "巡检验证后更新"},
                headers=h,
            )
            rep.expect("R9c 更新知识包", patched.status_code, 200)

        # ---------- 分析看板 / 配置读取 ----------
        for path, cid in (
            ("/api/v1/analytics/bid-results", "Y1  中标结果看板"),
            ("/api/v1/analytics/data-ops", "Y2  数据运营看板"),
            ("/api/v1/system/model-configs", "Y3  模型配置"),
            ("/api/v1/system/model-policy", "Y4  模型策略"),
            ("/api/v1/skills", "Y5  技能清单"),
            ("/api/v1/policies", "Y6  规则库"),
        ):
            res = c.get(path, headers=h)
            rep.expect(cid, res.status_code, 200, res.text[:60] if res.status_code != 200 else "")

    # 清理：撤销授权不影响，删除业务对象
    with httpx.Client(base_url=args.base_url, timeout=60.0) as c:
        try:
            token = c.post(
                "/api/v1/auth/login", json={"email": args.email, "password": args.password}
            ).json()["access_token"]
            h = _h(token)
            for org_id in cleanup.get("org", []):
                c.delete(f"/api/v1/org-units/{org_id}", headers=h)
            for kb_id in cleanup.get("kb", []):
                c.delete(f"/api/v1/knowledge/bases/{kb_id}", headers=h)
            for company_id in cleanup.get("company", []):
                c.delete(f"/api/v1/companies/{company_id}", headers=h)
            for user_id in cleanup.get("user", []):
                c.patch(
                    f"/api/v1/users/{user_id}", json={"status": "disabled"}, headers=h
                )
        except Exception:  # noqa: BLE001
            pass

    print(rep.render())
    if scratch_id:
        print(f"\n巡检项目 id（可删）：{scratch_id}")
    print(f"\n结果：{len(rep.rows) - rep.failed} PASS / {rep.failed} FAIL")
    if args.json:
        payload = [dict(zip(("id", "status", "detail"), r, strict=True)) for r in rep.rows]
        print(json.dumps(payload, ensure_ascii=False))
    return 1 if rep.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="业务模块真实数据巡检")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("BIDCOPILOT_DEMO_PASSWORD"),
        help="demo tenant password (or set BIDCOPILOT_DEMO_PASSWORD; no built-in default)",
    )
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
