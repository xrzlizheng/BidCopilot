import hashlib
import itertools
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity import api_tokens
from app.modules.identity.api_token_models import ApiToken
from app.modules.identity.api_tokens_router import router as api_tokens_router
from app.modules.identity.models import Tenant, User
from tests.support import cleanup_tenants, seed_tenant_user

# 登录限流按 IP 计数（LOGIN_IP_MAX_ATTEMPTS），并行跑集成套件时所有进程共用
# 同一个测试客户端 IP，会被别人的失败尝试挤爆成 429。给每次登录分配独立
# X-Real-IP（nginx 语义下该头由网关覆盖，测试里由测试自己给），不改共享配置。
_IP_COUNTER = itertools.count(1)


def _login_ip() -> dict[str, str]:
    value = next(_IP_COUNTER)
    return {"X-Real-IP": f"10.97.{value // 250}.{value % 250 + 1}"}


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
        headers=_login_ip(),
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _register_router() -> None:
    """幂等注册：已被 Lead 接进 main.py 时不再重复注册。"""
    prefix = "/api/v1/api-tokens"
    if not any(getattr(route, "path", None) == prefix for route in app.routes):
        app.include_router(api_tokens_router)


_register_router()


async def _create_token(
    client: AsyncClient, headers: dict[str, str], **payload
) -> dict:
    body = {"name": "集成商 token", "scopes": ["project:read"], **payload}
    response = await client.post("/api/v1/api-tokens", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.integration
async def test_api_token_create_lists_prefix_only_and_revoke() -> None:
    tenant_id, user_id, email = await seed_tenant_user("api-token-crud")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            created = await _create_token(
                client,
                headers,
                name="代理机构集成",
                scopes=["project:read", "compliance:review"],
                expires_in_days=30,
            )
            plaintext = created["token"]
            assert plaintext.startswith("btc_")
            assert created["token_prefix"] == plaintext[:12]
            assert created["scopes"] == ["project:read", "compliance:review"]
            assert created["expires_at"]
            assert created["last_used_at"] is None
            assert created["revoked_at"] is None

            # 列表只返回前缀，绝不返回明文。
            listed = await client.get("/api/v1/api-tokens", headers=headers)
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            assert len(rows) == 1
            assert rows[0]["token_prefix"] == created["token_prefix"]
            assert "token" not in rows[0]
            assert plaintext not in listed.text

            # 库内只存哈希：明文不出现在任何列里。
            async with SessionLocal() as session:
                stored = (
                    await session.execute(
                        select(ApiToken).where(ApiToken.tenant_id == tenant_id)
                    )
                ).scalar_one()
                assert stored.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
                assert plaintext not in (
                    stored.token_hash,
                    stored.token_prefix,
                    stored.name,
                )

            # 审计只记前缀，不记明文。
            async with SessionLocal() as session:
                events = list(
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant_id,
                                AuditEvent.action == "api_token.create",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert events, "创建 token 必须写审计"
            assert plaintext not in str(events[0].data)
            assert events[0].data["prefix"] == created["token_prefix"]
            assert events[0].resource_type == "api_token"

            # 吊销后列表为空、旧 token 立即失效。
            revoked = await client.delete(
                f"/api/v1/api-tokens/{created['id']}", headers=headers
            )
            assert revoked.status_code == 204, revoked.text
            after = await client.get("/api/v1/api-tokens", headers=headers)
            assert after.json() == []

            blocked = await client.get(
                "/api/v1/projects", headers={"Authorization": f"Bearer {plaintext}"}
            )
            assert blocked.status_code == 401, blocked.text

            async with SessionLocal() as session:
                actions = {
                    row.action
                    for row in (
                        await session.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                        )
                    ).scalars()
                }
            assert "api_token.revoke" in actions
            assert user_id is not None
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_authenticates_and_scopes_are_not_amplified() -> None:
    """用 btc_ token 走真实认证链路：权限 = scopes，不继承创建者角色。"""
    tenant_id, _, email = await seed_tenant_user("api-token-auth")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            # 只给 project:read：能读项目，不能写项目、也读不到成本。
            limited = await _create_token(
                client, headers, name="只读 token", scopes=["project:read"]
            )
            token_headers = {"Authorization": f"Bearer {limited['token']}"}

            me = await client.get("/api/v1/auth/me", headers=token_headers)
            assert me.status_code == 200, me.text
            assert me.json()["permissions"] == ["project:read"]
            assert "api_token" in me.json()["roles"]

            caps = await client.get("/api/v1/auth/capabilities", headers=token_headers)
            assert caps.status_code == 200, caps.text
            assert caps.json()["permissions"] == ["project:read"]

            projects = await client.get("/api/v1/projects", headers=token_headers)
            assert projects.status_code == 200, projects.text

            blocked_create = await client.post(
                "/api/v1/projects", json={"name": "不该建成的项目"}, headers=token_headers
            )
            assert blocked_create.status_code == 403, blocked_create.text

            # 创建者是 tenant_admin（有 tenant:settings:manage），但 token 明示不含该 scope，
            # 因此不能用 token 反向管理 token —— 权限不得放大。
            blocked_tokens = await client.get("/api/v1/api-tokens", headers=token_headers)
            assert blocked_tokens.status_code == 403, blocked_tokens.text

            # last_used_at 在校验时更新。
            async with SessionLocal() as session:
                stored = (
                    await session.execute(
                        select(ApiToken).where(ApiToken.tenant_id == tenant_id)
                    )
                ).scalar_one()
                assert stored.last_used_at is not None
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_scope_beyond_creator_permissions_is_dropped() -> None:
    """scope 写了创建者没有的能力 → 该能力对 token 不可见（交集，不放大）。"""
    tenant_id, _, email = await seed_tenant_user("api-token-intersection")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_token(
                client,
                headers,
                name="越权 scope",
                scopes=["project:read", "platform:tenant:manage"],
            )
            token_headers = {"Authorization": f"Bearer {created['token']}"}
            caps = await client.get("/api/v1/auth/capabilities", headers=token_headers)
            assert caps.status_code == 200, caps.text
            assert caps.json()["permissions"] == ["project:read"]
            # 高权限接口必须 403，而不是因为创建者是 admin 就放行。
            denied = await client.get("/api/v1/users", headers=token_headers)
            assert denied.status_code == 403, denied.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_expired_and_forged_are_401() -> None:
    tenant_id, user_id, email = await seed_tenant_user("api-token-expiry")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_token(client, headers, name="会过期的 token")

            # 直接把过期时间改到过去，模拟到期。
            async with SessionLocal() as session:
                stored = (
                    await session.execute(
                        select(ApiToken).where(ApiToken.id == UUID(created["id"]))
                    )
                ).scalar_one()
                from datetime import UTC, datetime, timedelta

                stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()

            expired = await client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {created['token']}"},
            )
            assert expired.status_code == 401, expired.text

            # 伪造的 btc_ 值 → 401（走 token 分支，不会退化成 JWT 解析报错 500）。
            forged = await client.get(
                "/api/v1/projects", headers={"Authorization": f"Bearer btc_{uuid4().hex}"}
            )
            assert forged.status_code == 401, forged.text

            # JWT 行为不变：无效 JWT 仍然 401。
            bad_jwt = await client.get(
                "/api/v1/projects",
                headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig"},
            )
            assert bad_jwt.status_code == 401, bad_jwt.text
            assert user_id is not None
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_is_tenant_isolated() -> None:
    tenant_id, _, email = await seed_tenant_user("api-token-tenant-a")
    other_id, _, other_email = await seed_tenant_user("api-token-tenant-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            other_headers = await _login(client, other_email)
            created = await _create_token(client, headers, name="A 的 token")

            # B 看不到 A 的 token；按 ID 吊销 A 的 token → 404（不泄露存在性）。
            listed = await client.get("/api/v1/api-tokens", headers=other_headers)
            assert listed.status_code == 200
            assert listed.json() == []
            cross = await client.delete(
                f"/api/v1/api-tokens/{created['id']}", headers=other_headers
            )
            assert cross.status_code == 404, cross.text

            # A 的 token 本身也不能跨租户：它只能看到 A 的数据。
            a_token_headers = {"Authorization": f"Bearer {created['token']}"}
            a_projects = await client.get("/api/v1/projects", headers=a_token_headers)
            assert a_projects.status_code == 200
            assert all(
                project["name"] != "B 的项目" for project in a_projects.json()
            )

            # 非本租户用户没有 tenant:settings:manage 之外的路径读到该 token。
            async with SessionLocal() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ApiToken).where(ApiToken.tenant_id == other_id)
                        )
                    ).scalars()
                )
            assert rows == []
            assert tenant_id != other_id
    finally:
        await cleanup_tenants([tenant_id, other_id])


@pytest.mark.integration
async def test_api_token_endpoints_require_settings_permission() -> None:
    tenant_id, _, email = await seed_tenant_user("api-token-perm", role_code="bid_engineer")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            listed = await client.get("/api/v1/api-tokens", headers=headers)
            assert listed.status_code == 403, listed.text
            created = await client.post(
                "/api/v1/api-tokens",
                json={"name": "x", "scopes": ["project:read"]},
                headers=headers,
            )
            assert created.status_code == 403, created.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_hash_is_unique_in_db() -> None:
    """同一明文不可能对应两条记录：hash 唯一索引兜底。"""
    tenant_id, _, email = await seed_tenant_user("api-token-unique")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            first = await _create_token(client, headers, name="token-1")
            second = await _create_token(client, headers, name="token-2")
            assert first["token"] != second["token"]
            async with SessionLocal() as session:
                hashes = {
                    row.token_hash
                    for row in (
                        await session.execute(
                            select(ApiToken).where(ApiToken.tenant_id == tenant_id)
                        )
                    ).scalars()
                }
                assert len(hashes) == 2
                # 认证只按 hash 命中，明文不参与查询。
                authenticated = await api_tokens.authenticate_api_token(
                    session, first["token"]
                )
                assert authenticated is not None
                assert authenticated.tenant_id == tenant_id
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_cleanup_leaves_no_audit_trail_with_plaintext() -> None:
    """兜底断言：租户删除后不应残留含明文的审计/记录。"""
    tenant_id, _, email = await seed_tenant_user("api-token-audit")
    plaintext = ""
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_token(client, headers, name="审计检查")
            plaintext = created["token"]
        async with SessionLocal() as session:
            events = list(
                (
                    await session.execute(
                        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
        for event in events:
            assert plaintext not in str(event.data)
    finally:
        await cleanup_tenants([tenant_id])
        # 直接删租户后确认 token 行随级联删除（不留孤儿）。
        async with SessionLocal() as session:
            orphans = list(
                (
                    await session.execute(
                        select(ApiToken).where(ApiToken.tenant_id == tenant_id)
                    )
                ).scalars()
            )
        assert orphans == []


@pytest.mark.integration
async def test_api_token_does_not_leak_into_jwt_path() -> None:
    """JWT 分支行为不变：普通登录拿到的 JWT 照常可用，且不被误判成 token。"""
    tenant_id, _, email = await seed_tenant_user("api-token-jwt")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            assert login_response.status_code == 200
            jwt_token = login_response.json()["access_token"]
            assert not api_tokens.is_api_token_value(jwt_token)
            headers = {"Authorization": f"Bearer {jwt_token}"}
            me = await client.get("/api/v1/auth/me", headers=headers)
            assert me.status_code == 200, me.text
            assert me.json()["email"] == email
            assert me.json()["roles"] != ["api_token"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_api_token_rejects_invalid_payload() -> None:
    tenant_id, _, email = await seed_tenant_user("api-token-invalid")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            empty_scopes = await client.post(
                "/api/v1/api-tokens",
                json={"name": "x", "scopes": []},
                headers=headers,
            )
            assert empty_scopes.status_code == 422, empty_scopes.text
            empty_name = await client.post(
                "/api/v1/api-tokens",
                json={"name": "", "scopes": ["project:read"]},
                headers=headers,
            )
            assert empty_name.status_code == 422, empty_name.text
            bad_expiry = await client.post(
                "/api/v1/api-tokens",
                json={"name": "x", "scopes": ["project:read"], "expires_in_days": 0},
                headers=headers,
            )
            assert bad_expiry.status_code == 422, bad_expiry.text
            # 未知 token id → 404。
            missing = await client.delete(f"/api/v1/api-tokens/{uuid4()}", headers=headers)
            assert missing.status_code == 404, missing.text
    finally:
        await cleanup_tenants([tenant_id])
        async with SessionLocal() as session:
            await session.execute(delete(ApiToken).where(ApiToken.tenant_id == tenant_id))
            await session.commit()
        async with SessionLocal() as session:
            assert (
                await session.execute(select(User).where(User.tenant_id == tenant_id))
            ).scalar_one_or_none() is None
            assert (
                await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            ).scalar_one_or_none() is None
