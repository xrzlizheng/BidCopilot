from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


async def _seed_admin(label: str) -> tuple[UUID, str]:
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
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"admin-{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name="Admin",
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=roles["tenant_admin"].id,
                scope_type="tenant",
                scope_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_file_check_tender_and_similarity() -> None:
    tenant_id, email = await _seed_admin("file-check")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            tender = (
                "本项目仅限采用华为品牌设备。投标人须在本市注册且注册资本不低于1000万元。"
                "评标委员会可酌情打分。"
            ).encode()
            checked = await client.post(
                "/api/v1/file-check/tender",
                files={"file": ("tender.txt", tender, "text/plain")},
                data={"province": "广东"},
                headers=headers,
            )
            assert checked.status_code == 200, checked.text
            body = checked.json()
            codes = {item["rule_id"] for item in body["findings"]}
            assert {"BUY-001", "BUY-002", "BUY-003", "BUY-008"} <= codes
            assert body["summary"]["high"] >= 3
            assert body["status"] == "warning"

            same = "智慧城市项目技术方案，包含系统架构、实施计划、运维服务与人员配置。".encode()
            different = "园林绿化养护方案，包含苗木清单、灌溉计划与季节性修剪安排。".encode()
            similarity = await client.post(
                "/api/v1/file-check/similarity",
                files=[
                    ("files", ("a.txt", same, "text/plain")),
                    ("files", ("b.txt", same, "text/plain")),
                    ("files", ("c.txt", different, "text/plain")),
                ],
                headers=headers,
            )
            assert similarity.status_code == 200, similarity.text
            pairs = similarity.json()["pairs"]
            assert pairs[0]["left"] == "a.txt" and pairs[0]["right"] == "b.txt"
            assert pairs[0]["level"] == "high"
            assert pairs[0]["score"] >= 0.75

            # 新增字段：语义分/等级/证据链，与既有 score/level/common_excerpt 并存
            assert pairs[0]["semantic_score"] >= 0.75
            assert pairs[0]["semantic_level"] == "high"
            evidence = pairs[0]["semantic_evidence"]
            assert set(evidence["contributions"]) == {"concept", "ngram", "term"}
            assert evidence["boundary"]
            assert "串标" in evidence["boundary"] and "不构成" in evidence["boundary"]
            assert 0.0 <= pairs[0]["combined_score"] <= 1.0

            low_pair = next(item for item in pairs if "c.txt" in {item["left"], item["right"]})
            assert low_pair["level"] == "low"
            assert low_pair["semantic_score"] < 0.5
            assert 0.0 <= low_pair["semantic_score"] <= 1.0

            # 语义相似度不应把无关文本抬成高分：语义等级也必须是 low
            assert low_pair["semantic_level"] == "low"

            # 排序：综合分（字面+语义）降序，且确定性
            combined = [item["combined_score"] for item in pairs]
            assert combined == sorted(combined, reverse=True)

            summary = similarity.json()["summary"]
            assert summary["semantic_high"] >= 1
            assert "chars" in summary
            assert similarity.json()["boundary"]

            # 性能护栏：单份超 20 万字正文 → 明确 400，不静默截断出结论
            oversized = ("技术方案包含系统架构与实施计划。" * 18_000).encode()
            too_big = await client.post(
                "/api/v1/file-check/similarity",
                files=[
                    ("files", ("x.txt", oversized, "text/plain")),
                    ("files", ("y.txt", same, "text/plain")),
                ],
                headers=headers,
            )
            assert too_big.status_code == 400, too_big.text
            assert too_big.json()["code"] == "file_check_limit_exceeded"
            assert "上限" in too_big.json()["message"]

            # 性能护栏：总正文超预算 → 同样 400（不是逐份跳过）。
            # 每份 18 万字 < 单份上限，但 4 份合计 72 万字 > 总预算 60 万字。
            chunk = ("技术方案包含系统架构与实施计划。" * 11_250).encode()  # 约 18 万字
            over_budget = await client.post(
                "/api/v1/file-check/similarity",
                files=[
                    ("files", ("p.txt", chunk, "text/plain")),
                    ("files", ("q.txt", chunk, "text/plain")),
                    ("files", ("r.txt", chunk, "text/plain")),
                    ("files", ("s.txt", chunk, "text/plain")),
                ],
                headers=headers,
            )
            assert over_budget.status_code == 400, over_budget.text
            assert over_budget.json()["code"] == "file_check_limit_exceeded"
            assert "合计" in over_budget.json()["message"]
    finally:
        await _cleanup(tenant_id)
