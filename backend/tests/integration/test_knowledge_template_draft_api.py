"""结构模板自动生成（P0-4）与模板可见性/归属语义的 API 集成测试。

使用真实 PostgreSQL（conftest 已配 DATABASE_URL），不使用 Mock。

产品只面向企业投标部门（单一投标主体），多客户能力已整体下线：
`client_id` 不再是模板/知识库的归属维度，模板隔离只保留 **租户边界 + personal(owner) +
tenant 发布态** 三层。因此这里覆盖：

1. 跨租户模板 code 不能被套用到项目（`get_pack` 租户校验），不泄露存在性；
2. personal 模板只对 owner 可见/可改，他人按不存在处理（404）；
3. tenant 未发布（draft）模板对普通成员不可见，也不能被项目应用；
4. PATCH 的字段级语义：只改名不动 scope/owner，审计不出现 client_id 残留。
"""

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.knowledge.models import KnowledgePack
from app.modules.projects.models import ScoringItem, TenderConstraint, TenderDocument


def _history_docx(headings: list[str]) -> bytes:
    document = Document()
    for index, heading in enumerate(headings, start=1):
        document.add_heading(f"第{index}章 {heading}", level=1)
        document.add_paragraph(
            f"{heading}部分：须提供营业执照与信息系统集成资质，并附业绩合同。"
        )
        document.add_paragraph(
            f"{heading}的技术要点包括双活架构、运维响应与实施计划。"
        )
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _seed_tenant(label: str) -> tuple[UUID, str, str, UUID]:
    """建租户 + 管理员 + 成员；返回 (tenant_id, admin_email, member_email, member_id)。"""
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code in ("tenant_admin", "bid_engineer"):
            role = (
                await session.execute(
                    select(Role).where(Role.tenant_id.is_(None), Role.code == code)
                )
            ).scalar_one_or_none()
            if role is None:
                spec = SYSTEM_ROLES[code]
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
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-{slug}@example.com",
            password_hash=hash_password("Password123!"),
            display_name="管理员",
        )
        member = User(
            tenant_id=tenant.id,
            email=f"member-{slug}@example.com",
            password_hash=hash_password("Password123!"),
            display_name="成员",
        )
        session.add_all([admin, member])
        await session.flush()
        session.add_all(
            [
                UserRole(
                    tenant_id=tenant.id,
                    user_id=admin.id,
                    role_id=roles["tenant_admin"].id,
                    scope_type="tenant",
                    scope_id=tenant.id,
                ),
                UserRole(
                    tenant_id=tenant.id,
                    user_id=member.id,
                    role_id=roles["bid_engineer"].id,
                    scope_type="tenant",
                    scope_id=tenant.id,
                ),
            ]
        )
        await session.commit()
        return tenant.id, admin.email, member.email, member.id


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


STRUCTURE = {
    "sections": [
        {
            "title": "运维方案",
            "purpose": "响应运维要求",
            "guidance": "量化 SLA",
            "required_materials": ["服务承诺"],
        }
    ]
}


async def _create_pack(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    *,
    scope: str = "tenant",
) -> dict:
    payload: dict = {
        "name": name,
        "category": "structure",
        "content_json": STRUCTURE,
        "scope": scope,
    }
    response = await client.post("/api/v1/knowledge-packs", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_constraint(tenant_id: UUID, project_id: UUID) -> None:
    """项目必须先有已解析的招标文件与约束集，才能生成大纲。

    不 seed 的话 `apply_template` 会在模板校验之前就以「请先上传并解析招标文件」
    失败，模板隔离断言就会因为错误原因通过（假绿）。
    """
    async with SessionLocal() as session:
        document = TenderDocument(
            tenant_id=tenant_id,
            project_id=project_id,
            filename="template-scope-tender.docx",
            size_bytes=1024,
            file_hash=uuid4().hex + uuid4().hex,
            object_ref="local/template-scope-tender.docx",
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
        session.add(
            ScoringItem(
                tenant_id=tenant_id,
                project_id=project_id,
                constraint_id=constraint.id,
                category="技术",
                item="技术方案",
                score=Decimal("30"),
                criteria="完整可行",
                source_span={},
                confidence="high",
            )
        )
        await session.commit()


@pytest.mark.integration
async def test_template_tenant_isolation_and_update_semantics() -> None:
    """模板隔离：跨租户不可见/不可应用；personal 仅 owner；PATCH 只改传入字段。"""
    tenant_id, admin_email, member_email, _ = await _seed_tenant("tpl-scope")
    other_tenant_id, other_admin_email, _, _ = await _seed_tenant("tpl-scope-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await _login(client, admin_email)
            member = await _login(client, member_email)
            other_admin = await _login(client, other_admin_email)

            pack_tenant = await _create_pack(client, admin, "租户级通用模板")
            pack_personal = await _create_pack(client, admin, "管理员个人模板", scope="personal")
            # 模板不再有客户归属字段（多客户能力已下线）
            assert "client_id" not in pack_tenant
            assert "client_id" not in pack_personal
            assert pack_tenant["scope"] == "tenant"
            assert pack_personal["scope"] == "personal"

            # ---- 列表：tenant 级对全租户可见，personal 仅 owner 可见 ----
            member_packs = await client.get("/api/v1/knowledge-packs", headers=member)
            assert member_packs.status_code == 200, member_packs.text
            member_codes = {pack["code"] for pack in member_packs.json()}
            assert pack_tenant["code"] in member_codes
            assert pack_personal["code"] not in member_codes

            other_packs = await client.get("/api/v1/knowledge-packs", headers=other_admin)
            assert other_packs.status_code == 200, other_packs.text
            other_codes = {pack["code"] for pack in other_packs.json()}
            # 跨租户模板不出现在对方列表里（不泄露存在性）
            assert pack_tenant["code"] not in other_codes
            assert pack_personal["code"] not in other_codes

            # 知识库列表接口不再支持 client_id 过滤参数（能力已下线）
            unsupported_filter = await client.get(
                "/api/v1/knowledge-packs",
                params={"client_id": str(uuid4())},
                headers=member,
            )
            # 未知 query 参数被忽略而非 404/500，返回列表正常
            assert unsupported_filter.status_code == 200, unsupported_filter.text

            # ---- 跨租户 PATCH / 应用：一律按不存在处理 ----
            cross_patch = await client.patch(
                f"/api/v1/knowledge-packs/{pack_tenant['id']}",
                json={"name": "别家想改名"},
                headers=other_admin,
            )
            assert cross_patch.status_code == 404, cross_patch.text

            # ---- PATCH 字段级语义：只改名不动 scope/owner ----
            rename_only = await client.patch(
                f"/api/v1/knowledge-packs/{pack_tenant['id']}",
                json={"name": "租户级模板（改名）"},
                headers=admin,
            )
            assert rename_only.status_code == 200, rename_only.text
            assert rename_only.json()["name"] == "租户级模板（改名）"
            assert rename_only.json()["scope"] == "tenant"
            # 未传的字段原样保留：enabled 不变、仍是 published
            assert rename_only.json()["enabled"] is True
            assert rename_only.json()["status"] == "published"

            # 非 owner 改 personal 模板：按不存在处理（404）
            member_patch_personal = await client.patch(
                f"/api/v1/knowledge-packs/{pack_personal['id']}",
                json={"name": "成员改别人的个人模板"},
                headers=member,
            )
            assert member_patch_personal.status_code == 404, member_patch_personal.text

            # ---- 跨租户模板 code 不能套用到项目（get_pack 租户校验） ----
            other_project = await client.post(
                "/api/v1/projects", json={"name": "别家项目"}, headers=other_admin
            )
            assert other_project.status_code == 201, other_project.text
            other_project_id = other_project.json()["id"]
            await _seed_constraint(other_tenant_id, UUID(other_project_id))
            cross_apply = await client.put(
                f"/api/v1/projects/{other_project_id}/template",
                json={"template_code": pack_tenant["code"]},
                headers=other_admin,
            )
            # 该 code 在 get_pack 处按不可用处理 → 「结构模板不可用」，不泄露存在性
            assert cross_apply.status_code == 422, cross_apply.text
            assert "结构模板不可用" in cross_apply.json()["message"]

            # 同租户成员可以用租户级模板
            project = await client.post(
                "/api/v1/projects", json={"name": "模板归属项目"}, headers=admin
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            await _seed_constraint(tenant_id, UUID(project_id))
            member_apply = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": pack_tenant["code"]},
                headers=member,
            )
            assert member_apply.status_code == 200, member_apply.text

            # 审计记录里不得再出现已下线的 client_id 维度
            async with SessionLocal() as session:
                events = list(
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant_id,
                                AuditEvent.action == "knowledge_pack.update",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert events, "模板更新应留下审计"
            for event in events:
                assert "client_id" not in (event.data or {})
    finally:
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_template_draft_api_generates_draft_from_history_documents() -> None:
    tenant_id, admin_email, member_email, _ = await _seed_tenant("tpl-draft")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await _login(client, admin_email)
            member = await _login(client, member_email)

            base = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "冷启动知识库", "scope": "tenant"},
                headers=admin,
            )
            assert base.status_code == 201, base.text
            kb_id = base.json()["id"]

            # ---- 输入不足：知识库为空 → 422 明确错误，不生成空模板 ----
            empty = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/template-draft", headers=admin
            )
            assert empty.status_code == 422, empty.text
            assert "历史标书不足" in empty.json()["message"]

            # 只有一份历史标书也不够（MIN_SOURCE_DOCUMENTS=2）
            one = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "history-1.docx",
                        _history_docx(["投标函", "资格证明文件", "技术方案"]),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"kb_id": kb_id, "kind": "case", "tags": "历史标书"},
                headers=admin,
            )
            assert one.status_code == 200, one.text
            still_short = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/template-draft", headers=admin
            )
            assert still_short.status_code == 422, still_short.text
            assert "历史标书不足" in still_short.json()["message"]
            async with SessionLocal() as session:
                count_before = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(KnowledgePack)
                            .where(KnowledgePack.tenant_id == tenant_id)
                        )
                    ).scalar_one()
                )

            # 第二份历史标书后证据足够
            two = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "history-2.docx",
                        _history_docx(["投标函", "资格证明文件", "技术方案", "售后服务"]),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"kb_id": kb_id, "kind": "case", "tags": "历史标书"},
                headers=admin,
            )
            assert two.status_code == 200, two.text

            generated = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/template-draft", headers=admin
            )
            assert generated.status_code == 201, generated.text
            pack = generated.json()
            assert pack["category"] == "structure"
            assert pack["status"] == "draft"
            assert pack["is_builtin"] is False
            assert pack["scope"] == "tenant"
            # 单一投标主体：草稿模板不再有 client_id 归属
            assert "client_id" not in pack
            assert pack["enabled"] is True

            sections = pack["content_json"]["sections"]
            assert len(sections) >= 3
            titles = [section["title"] for section in sections]
            assert "投标函" in titles
            assert "技术方案" in titles
            for section in sections:
                assert section["title"].strip()
                assert section["purpose"].strip()
                assert section["guidance"].strip()
                assert isinstance(section["required_materials"], list)
                assert "。" not in "".join(section["required_materials"])
                assert "待补充" not in section["purpose"]
            qualification = next(
                section for section in sections if section["title"] == "资格证明文件"
            )
            assert "营业执照" in qualification["required_materials"]

            async with SessionLocal() as session:
                count_after = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(KnowledgePack)
                            .where(KnowledgePack.tenant_id == tenant_id)
                        )
                    ).scalar_one()
                )
            assert count_after == count_before + 1

            # 草稿不能被项目直接应用（需人工确认发布）
            project = await client.post(
                "/api/v1/projects", json={"name": "草稿模板项目"}, headers=admin
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            await _seed_constraint(tenant_id, UUID(project_id))
            draft_apply = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": pack["code"]},
                headers=admin,
            )
            assert draft_apply.status_code == 422, draft_apply.text
            assert "尚未发布" in draft_apply.json()["message"]

            # 草稿出现在模板列表里（管理可见），状态为 draft
            listed = await client.get("/api/v1/knowledge-packs", headers=admin)
            listed_packs = {item["code"]: item for item in listed.json()}
            assert listed_packs[pack["code"]]["status"] == "draft"

            # 人工确认并发布后可应用
            published = await client.patch(
                f"/api/v1/knowledge-packs/{pack['id']}",
                json={"status": "published"},
                headers=admin,
            )
            assert published.status_code == 200, published.text
            assert published.json()["status"] == "published"
            applied = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": pack["code"]},
                headers=admin,
            )
            assert applied.status_code == 200, applied.text

            # ---- 审计落库 ----
            async with SessionLocal() as session:
                events = list(
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant_id,
                                AuditEvent.action == "knowledge_pack.generate_draft",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(events) == 1
            assert events[0].resource_id == pack["id"]
            assert events[0].data["sections"] == len(sections)
            # 生成草稿的审计不记录已下线的 client 维度
            assert "client_id" not in (events[0].data or {})

            # ---- 权限：bid_engineer 有 template:write，可用自己有写权限的知识库 ----
            member_generate = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/template-draft", headers=member
            )
            # 知识库为 tenant 级 enterprise 可见 → 成员有写权限则允许生成
            assert member_generate.status_code in {201, 404}

            # 跨租户未知 kb_id → 404，不泄露存在性
            unknown = await client.post(
                f"/api/v1/knowledge/bases/{uuid4()}/template-draft", headers=admin
            )
            assert unknown.status_code == 404
    finally:
        await _cleanup([tenant_id])
