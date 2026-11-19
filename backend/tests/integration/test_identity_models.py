from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.modules.identity.models import Role, Tenant, User, UserRole


@pytest.mark.integration
async def test_tenant_user_role_roundtrip() -> None:
    suffix = uuid4().hex
    async with SessionLocal() as session:
        tenant = Tenant(name="Test Tenant", slug=f"test-{suffix}", kind="customer")
        session.add(tenant)
        await session.flush()

        user = User(
            tenant_id=tenant.id,
            email=f"{suffix}@example.com",
            password_hash="not-used",
            display_name="Test User",
        )
        role = Role(
            tenant_id=tenant.id,
            code=f"role-{suffix}",
            name="Test Role",
            permissions=["asset:read"],
            is_system=False,
        )
        session.add_all([user, role])
        await session.flush()
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()

        loaded = (
            await session.execute(
                select(User).where(User.tenant_id == tenant.id, User.email == f"{suffix}@example.com")
            )
        ).scalar_one()
        assert loaded.display_name == "Test User"

        link = (
            await session.execute(select(UserRole).where(UserRole.user_id == user.id))
        ).scalar_one()
        assert link.role_id == role.id

        await session.delete(tenant)
        await session.commit()
