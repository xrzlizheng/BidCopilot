"""AI 计量接入集成测试：真实生成/评标链路落库、失败状态原样记账、跨租户 404。

用真实 PostgreSQL（容器内 bidcopilot_test）+ 真实 FastAPI 路由。
LLM 用 `backend/tests/` 内的测试替身注入 `get_llm_client` / `get_judge_client`，
生产代码不含任何 Mock。

产品只面向企业投标部门（单一投标主体）：多客户分摊（`/clients/{id}/ai-usage`、
`/costs/ai-usage/by-client`）已下线，因此计量只按项目维度查询，隔离靠租户边界。
"""

import json
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.costs.ai_usage_router import router as ai_usage_router
from app.modules.costs.metering import record_ai_usage
from app.modules.costs.metering_models import AiUsageLedger
from app.modules.generation.deps import get_judge_client, get_llm_client
from app.modules.generation.llm_client import LLMTransportError
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from tests.support import sample_tender_docx


def _collect_route_paths(routes: object, acc: set[str]) -> set[str]:
    """递归收集已注册路由路径。

    FastAPI 0.15x 把 include_router 的结果包成 `_IncludedRouter`（`path` 为 None），
    真实路径藏在 `original_router.routes` 里，所以必须递归而不是只看顶层。
    """
    for route in routes or []:  # type: ignore[union-attr]
        path = getattr(route, "path", None)
        if path:
            acc.add(path)
        original = getattr(route, "original_router", None)
        if original is not None:
            _collect_route_paths(original.routes, acc)
        nested = getattr(route, "routes", None)
        if nested is not None and nested is not original:
            _collect_route_paths(nested, acc)
    return acc


# Lead 负责在 app/main.py 注册 ai_usage_router；这里做幂等保护，避免重复挂载。
if not {route.path for route in ai_usage_router.routes} <= _collect_route_paths(
    app.routes, set()
):
    app.include_router(ai_usage_router)


class MeteringFakeLLM:
    """返回合法章节 JSON 的 LLM 替身，model 用于断言台账记录的是实际模型名。"""

    model = "metering-fake-main"

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        return json.dumps(
            {
                "blocks": [
                    {"type": "paragraph", "text": "本节由测试替身模型生成的技术方案响应正文。"},
                    {"type": "paragraph", "text": "compliance and commitment content."},
                ]
            },
            ensure_ascii=False,
        )


class FailingLLM:
    """首节成功、后续失败：用于验证 partial 状态原样落账（0 篇、状态保留）。"""

    model = "metering-fake-flaky"

    def __init__(self) -> None:
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        if self.calls > 1:
            raise LLMTransportError("simulated transport failure")
        return json.dumps(
            {"blocks": [{"type": "paragraph", "text": "第一节成功产出。"}]},
            ensure_ascii=False,
        )


class AlwaysFailingLLM:
    model = "metering-fake-down"

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        raise LLMTransportError("simulated transport failure")


class MeteringJudgeLLM:
    model = "metering-fake-judge"

    def __init__(self, first_id: str, second_id: str) -> None:
        self.first_id = first_id
        self.second_id = second_id

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        return json.dumps(
            {
                "items": [
                    {
                        "scoring_item_id": self.first_id,
                        "score": 30,
                        "rationale": "技术方案覆盖了全部评分要点。",
                        "evidence": ["技术方案响应"],
                        "gaps": [],
                        "confidence": "high",
                        "needs_review": False,
                    },
                    {
                        "scoring_item_id": self.second_id,
                        "score": 30,
                        "rationale": "报价策略合理。",
                        "evidence": ["投标报价响应"],
                        "gaps": ["缺少分项报价明细"],
                        "confidence": "medium",
                        "needs_review": True,
                    },
                ],
                "overall_summary": "整体具备竞争力。",
                "risks": ["分项报价需补齐"],
                "improvements": ["补充分项报价明细"],
            },
            ensure_ascii=False,
        )


async def _seed_tenant(
    label: str, *, kind: str = "customer", role_code: str = "tenant_admin"
) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(
                    select(Role).where(Role.tenant_id.is_(None), Role.code == code)
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    permissions=list(spec["permissions"]),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
            else:
                role.permissions = list(spec["permissions"])
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind=kind)
        session.add(tenant)
        await session.flush()
        email = f"{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name=label,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=roles[role_code].id,
                scope_type="tenant",
                scope_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _prepare_project(
    client: AsyncClient, headers: dict[str, str], *, name: str
) -> tuple[str, list[dict]]:
    created = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    uploaded = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={
            "file": (
                "tender.docx",
                sample_tender_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    outline = await client.post(f"/api/v1/projects/{project_id}/outline", headers=headers)
    assert outline.status_code == 200, outline.text
    return project_id, outline.json()


async def _ledger_rows(tenant_id: UUID) -> list[AiUsageLedger]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(AiUsageLedger)
                    .where(AiUsageLedger.tenant_id == tenant_id)
                    .order_by(AiUsageLedger.created_at)
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.integration
async def test_generation_metering_persists_and_aggregates() -> None:
    tenant_id, email = await _seed_tenant("metering-gen")
    other_tenant_id, other_email = await _seed_tenant("metering-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id, sections = await _prepare_project(client, headers, name="计量生成项目")

            # 1) rules 模式是确定性渲染，不应产生任何计量（防双倍计数）
            rules = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "rules"},
                headers=headers,
            )
            assert rules.status_code == 200, rules.text
            assert await _ledger_rows(tenant_id) == [], "rules 模式不应写入 AI 用量台账"

            usage_before = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=headers
            )
            assert usage_before.status_code == 200, usage_before.text
            assert usage_before.json() == {"pieces": 0, "words": 0}

            # 2) llm 模式走真实生成链路，逐节计量并落库
            app.dependency_overrides[get_llm_client] = lambda: MeteringFakeLLM()
            generated = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "llm"},
                headers=headers,
            )
            assert generated.status_code == 200, generated.text
            assert generated.json()["status"] == "succeeded"
            assert generated.json()["sections_generated"] == len(sections)

            rows = await _ledger_rows(tenant_id)
            assert len(rows) == len(sections), "一节应记一条台账"
            assert all(row.operation == "generation" for row in rows)
            assert all(row.status == "ok" for row in rows)
            assert all(row.piece_count == 1 for row in rows)
            assert all(row.model == "metering-fake-main" for row in rows)
            assert all(row.word_count > 0 for row in rows)
            # 单一投标主体：台账只归属租户与项目，不再记 client_id
            assert all(row.tenant_id == tenant_id for row in rows)
            assert all(row.project_id == UUID(project_id) for row in rows)

            # 3) 项目维度查询 = 台账聚合
            project_usage = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=headers
            )
            assert project_usage.status_code == 200, project_usage.text
            expected_words = sum(row.word_count for row in rows)
            assert project_usage.json() == {
                "pieces": len(sections),
                "words": expected_words,
            }

            # 4) 跨租户按 ID 读取必须 404，不泄露存在性
            other_headers = await _login(client, other_email)
            other_project = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=other_headers
            )
            assert other_project.status_code == 404, other_project.text

            # 5) 不存在的项目 ID 同样 404（与跨租户回复不可区分）
            missing_project = await client.get(
                f"/api/v1/projects/{uuid4()}/ai-usage", headers=headers
            )
            assert missing_project.status_code == 404

            # 6) 已下线的按客户分摊接口不得再被注册（防止能力回潮）
            removed = await client.get("/api/v1/costs/ai-usage/by-client", headers=headers)
            assert removed.status_code == 404, removed.text

        actions = set()
        async with SessionLocal() as session:
            actions = set(
                (
                    await session.execute(
                        select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
        # 计量写入本身不写审计（避免逐篇噪声）
        assert "project.generate" in actions
    finally:
        app.dependency_overrides.pop(get_llm_client, None)
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_failed_generation_records_failed_status_not_zero_pieces() -> None:
    """失败/部分失败：计量状态原样保留 partial/failed，且不把失败折算成篇数。"""
    tenant_id, email = await _seed_tenant("metering-fail")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id, sections = await _prepare_project(client, headers, name="失败计量项目")
            assert len(sections) >= 2

            flaky = FailingLLM()
            app.dependency_overrides[get_llm_client] = lambda: flaky
            partial = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "llm"},
                headers=headers,
            )
            assert partial.status_code == 502, partial.text
            assert partial.json()["code"] == "generation_failed"

            rows = await _ledger_rows(tenant_id)
            ok_rows = [row for row in rows if row.status == "ok"]
            flagged = [row for row in rows if row.status != "ok"]
            assert len(ok_rows) == 1, "只有成功那一节计篇"
            assert len(flagged) == 1, "失败必须留一条记录，不得静默丢弃"
            assert flagged[0].status == "partial", "已生成 1 节后失败应记 partial"
            assert flagged[0].piece_count == 0
            assert flagged[0].word_count == 0

            # 全部失败：状态记 failed
            app.dependency_overrides[get_llm_client] = lambda: AlwaysFailingLLM()
            failed = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "llm", "section_ids": [sections[0]["id"]]},
                headers=headers,
            )
            assert failed.status_code == 502, failed.text
            rows = await _ledger_rows(tenant_id)
            assert rows[-1].status == "failed"
            assert rows[-1].piece_count == 0

            # 台账里失败记录不贡献篇数：成功的那 1 节仍是唯一消耗
            usage = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=headers
            )
            assert usage.status_code == 200, usage.text
            assert usage.json()["pieces"] == 1
    finally:
        app.dependency_overrides.pop(get_llm_client, None)
        await _cleanup([tenant_id])


@pytest.mark.integration
async def test_evaluation_metering_records_evaluation_operation() -> None:
    """AI 评标产出同样计量，operation 区分 evaluation，篇数=实际评出的评分项条数。"""
    tenant_id, email = await _seed_tenant("metering-eval")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id, _ = await _prepare_project(client, headers, name="评标计量项目")

            # 用 rules 生成章节正文（不计量），再跑评标
            generated = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "rules"},
                headers=headers,
            )
            assert generated.status_code == 200, generated.text
            assert await _ledger_rows(tenant_id) == []

            matrix = await client.post(f"/api/v1/projects/{project_id}/matrix", headers=headers)
            assert matrix.status_code == 200, matrix.text
            ids = [row["scoring_item_id"] for row in matrix.json()]
            assert len(ids) >= 2

            app.dependency_overrides[get_judge_client] = lambda: MeteringJudgeLLM(ids[0], ids[1])
            evaluated = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert evaluated.status_code == 201, evaluated.text
            assert evaluated.json()["status"] == "completed"
            items = evaluated.json()["result_json"]["items"]
            # 实现口径：只有 evaluated=True 的评分项算「实际评出」，客观项/漏评项不计篇。
            produced = sum(
                1 for item in items if isinstance(item, dict) and item.get("evaluated", True)
            )

            rows = await _ledger_rows(tenant_id)
            assert len(rows) == 1
            assert rows[0].operation == "evaluation"
            assert rows[0].status == "ok"
            assert rows[0].piece_count == produced
            assert rows[0].word_count > 0
            assert rows[0].model == "metering-fake-judge"

            usage = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=headers
            )
            assert usage.json()["pieces"] == produced
            assert usage.json()["words"] == rows[0].word_count

            # 评标失败也要留 failed 记录（0 篇），不得静默记 0 条或记成功

            class BrokenJudge:
                model = "metering-fake-judge-broken"

                def configured(self) -> bool:
                    return True

                async def complete(self, *, system: str, user: str) -> str:
                    raise LLMTransportError("simulated judge outage")

            app.dependency_overrides[get_judge_client] = lambda: BrokenJudge()
            failed = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert failed.status_code == 502, failed.text
            rows = await _ledger_rows(tenant_id)
            assert rows[-1].operation == "evaluation"
            assert rows[-1].status == "failed"
            assert rows[-1].piece_count == 0
            # 失败不增加篇数
            usage = await client.get(
                f"/api/v1/projects/{project_id}/ai-usage", headers=headers
            )
            assert usage.json()["pieces"] == produced
    finally:
        app.dependency_overrides.pop(get_judge_client, None)
        await _cleanup([tenant_id])
