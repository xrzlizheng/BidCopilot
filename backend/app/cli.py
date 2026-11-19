import argparse
import asyncio
import getpass
import json
from uuid import UUID

from sqlalchemy import select

from app.db.session import SessionLocal
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


async def repair_audit_chain(tenant_id: UUID | None, dry_run: bool) -> None:
    """按租户修复审计哈希链；缺省遍历所有租户，每租户打印一行报告 JSON。"""
    from app.modules.audit.service import AuditService

    async with SessionLocal() as session:
        if tenant_id is None:
            tenant_ids = list(
                (await session.execute(select(Tenant.id).order_by(Tenant.created_at)))
                .scalars()
                .all()
            )
        else:
            tenant_ids = [tenant_id]
        service = AuditService(session)
        for current in tenant_ids:
            report = await service.repair_chain(current, dry_run=dry_run)
            print(json.dumps(report, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(prog="bidcopilot")
    sub = parser.add_subparsers(dest="command", required=True)
    extra_handlers: dict = {}
    # 企业版扩展点：overlay 提供 cli_extra.py 时注册附加子命令（如 init-platform）。
    try:
        from app.cli_extra import register_cli
        register_cli(sub, extra_handlers)
    except ImportError:
        pass

    demo = sub.add_parser("seed-demo", help="seed an idempotent pilot demo tenant")
    demo.add_argument("--name", default="演示投标企业")
    demo.add_argument("--slug", default="demo")
    demo.add_argument("--email", default="demo@example.com")
    demo.add_argument("--password")

    repair = sub.add_parser(
        "repair-audit-chain", help="recompute broken per-tenant audit hash chains"
    )
    repair.add_argument(
        "--tenant-id", type=UUID, default=None, help="target tenant UUID; default: all tenants"
    )
    repair.add_argument("--dry-run", action="store_true", help="report only, no writes")

    args = parser.parse_args()
    if args.command in extra_handlers:
        extra_handlers[args.command](args)
    elif args.command == "seed-demo":
        from app.demo import seed_demo

        password = args.password or getpass.getpass("demo admin password: ")
        if not password:
            # 非交互环境（CI/管道）里 getpass 可能读到空串：明确报错，
            # 绝不回退到任何内置默认口令。
            parser.error("必须提供演示管理员口令：--password 或交互输入；不使用任何默认口令")

        async def _run() -> None:
            async with SessionLocal() as session:
                result = await seed_demo(
                    session,
                    tenant_name=args.name,
                    tenant_slug=args.slug,
                    email=args.email,
                    password=password,
                )
            print(f"demo tenant: {result.tenant_slug} ({result.tenant_id})")
            print(f"demo admin: {result.email}")
            print(f"demo company: {result.company_id}")
            print(
                "demo projects: " + ", ".join(str(project_id) for project_id in result.project_ids)
            )

        asyncio.run(_run())
    elif args.command == "repair-audit-chain":
        asyncio.run(repair_audit_chain(args.tenant_id, args.dry_run))


if __name__ == "__main__":
    main()
