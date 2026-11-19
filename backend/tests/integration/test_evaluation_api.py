import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.generation.deps import get_judge_client, get_llm_client
from app.modules.generation.llm_client import LLMClient, LLMTransportError
from app.modules.generation.models import BidSection
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.projects.models import ScoringItem, TenderConstraint, TenderDocument


class FakeLLM:
    model = "fake-eval"

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
                        "score": 18,
                        "rationale": "技术方案覆盖评分点。",
                        "evidence": ["技术方案响应：提供实施计划"],
                        "gaps": [],
                        "confidence": "high",
                        "needs_review": False,
                    },
                    {
                        "scoring_item_id": self.second_id,
                        "score": 6,
                        "rationale": "业绩证明不足。",
                        "evidence": [],
                        "gaps": ["缺少合同扫描件"],
                        "confidence": "medium",
                        "needs_review": True,
                    },
                ],
                "overall_summary": "整体具备竞争力，业绩材料需补齐。",
                "risks": ["业绩证明不足"],
                "improvements": ["补充合同扫描件"],
            },
            ensure_ascii=False,
        )


class InvalidLLM:
    model = "fake-invalid"

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        return "not-json"


class TimeoutJudge:
    model = "judge-timeout"

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        raise LLMTransportError("ReadTimeout: simulated")


class FallbackLLM(FakeLLM):
    model = "fallback-flash"


async def _seed_tenant(label: str) -> tuple[UUID, str]:
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
                role.name = spec["name"]
                role.permissions = list(spec["permissions"])
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
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
            UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id)
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


async def _seed_evaluation_inputs(tenant_id: UUID, project_id: UUID) -> tuple[str, str]:
    async with SessionLocal() as session:
        document = TenderDocument(
            tenant_id=tenant_id,
            project_id=project_id,
            filename="tender.docx",
            size_bytes=1024,
            file_hash="0" * 64,
            object_ref="local/tender.docx",
            parse_status="parsed",
            parsed_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()
        constraint = TenderConstraint(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document.id,
            version=1,
            payload={},
            parse_meta={},
        )
        session.add(constraint)
        await session.flush()
        first = ScoringItem(
            tenant_id=tenant_id,
            project_id=project_id,
            constraint_id=constraint.id,
            category="技术",
            item="技术方案",
            score=Decimal("20"),
            criteria="方案完整可行",
            source_span={},
            confidence="high",
        )
        second = ScoringItem(
            tenant_id=tenant_id,
            project_id=project_id,
            constraint_id=constraint.id,
            category="商务",
            item="项目业绩",
            score=Decimal("10"),
            criteria="提供同类合同",
            source_span={},
            confidence="high",
        )
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                BidSection(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    title="技术方案响应",
                    section_type="scoring",
                    order_index=1,
                    scoring_item_id=first.id,
                    content_text="技术方案响应：提供实施计划、人员安排与保障措施。",
                    status="draft",
                ),
                BidSection(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    title="项目业绩响应",
                    section_type="scoring",
                    order_index=2,
                    scoring_item_id=second.id,
                    content_text="项目业绩响应：提供过往项目简介。",
                    status="draft",
                ),
            ]
        )
        await session.commit()
        return str(first.id), str(second.id)


@pytest.mark.integration
async def test_ai_evaluation_api_provider_override_and_failures() -> None:
    tenant_id, email = await _seed_tenant("evaluation-a")
    other_tenant_id, other_email = await _seed_tenant("evaluation-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            created = await client.post(
                "/api/v1/projects", json={"name": "AI 评标验收项目"}, headers=headers
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            first_id, second_id = await _seed_evaluation_inputs(tenant_id, UUID(project_id))

            app.dependency_overrides[get_judge_client] = lambda: FakeLLM(first_id, second_id)
            evaluated = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert evaluated.status_code == 201, evaluated.text
            body = evaluated.json()
            assert body["status"] == "completed"
            summary = body["result_json"]["summary"]
            assert summary["total_score"] == 24.0
            assert summary["max_score"] == 30.0
            assert summary["percentage"] == 80.0
            assert summary["band"] == "strong"
            assert summary["needs_review_count"] == 1
            assert len(body["result_json"]["items"]) == 2

            latest = await client.get(
                f"/api/v1/projects/{project_id}/evaluations/latest", headers=headers
            )
            assert latest.status_code == 200, latest.text
            assert latest.json()["id"] == body["id"]

            listing = await client.get(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert listing.status_code == 200, listing.text
            assert listing.json()[0]["summary"]["percentage"] == 80.0
            assert "result_json" not in listing.json()[0]

            app.dependency_overrides[get_judge_client] = lambda: TimeoutJudge()
            app.dependency_overrides[get_llm_client] = lambda: FallbackLLM(first_id, second_id)
            fallback = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert fallback.status_code == 201, fallback.text
            fallback_body = fallback.json()
            assert fallback_body["model"] == "fallback-flash"
            assert fallback_body["result_json"]["meta"]["judge_fallback"] is True
            assert any("回退主模型" in warning for warning in fallback_body["result_json"]["warnings"])

            app.dependency_overrides[get_judge_client] = lambda: LLMClient(
                base_url=None, api_key=None, model=None
            )
            app.dependency_overrides.pop(get_llm_client, None)
            unconfigured = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert unconfigured.status_code == 503, unconfigured.text
            assert unconfigured.json()["code"] == "llm_not_configured"

            app.dependency_overrides[get_judge_client] = lambda: InvalidLLM()
            failed = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert failed.status_code == 502, failed.text
            assert failed.json()["code"] == "evaluation_failed"
            listing = await client.get(
                f"/api/v1/projects/{project_id}/evaluations", headers=headers
            )
            assert listing.json()[0]["status"] == "failed"
            assert listing.json()[0]["error_code"] == "evaluation_failed"

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden_detail = await client.get(
                f"/api/v1/evaluations/{body['id']}", headers=other_headers
            )
            assert forbidden_detail.status_code == 404
            forbidden_post = await client.post(
                f"/api/v1/projects/{project_id}/evaluations", headers=other_headers
            )
            assert forbidden_post.status_code == 404

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
            assert "project.evaluate" in actions
    finally:
        app.dependency_overrides.pop(get_judge_client, None)
        await _cleanup([tenant_id, other_tenant_id])
