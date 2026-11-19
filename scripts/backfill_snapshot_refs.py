"""回填历史对象引用：把本地快照路径（.snapshots\\...）迁移到当前 SNAPSHOT_STORE。

背景：早期版本用本地文件存储（SNAPSHOT_STORE=local）写入过 knowledge_images /
knowledge_documents 的 ``object_ref``；切换到 MinIO 后这些引用不可读，接口返回
503，页面显示「图片不可用」。本脚本把本地文件按同一 key 重新上传到当前存储，
再把 ``object_ref`` 改写为存储返回的 ``s3://...``。

默认 dry-run（只打印计划），加 ``--apply`` 才真正上传并写库。

用法（在容器内执行，容器内可直连数据库与 MinIO）：

    # 1) 把宿主机上的 .snapshots 目录拷进容器（镜像不包含它）
    docker cp backend/.snapshots bidcopilot-app:/tmp/bidcopilot-backend/.snapshots
    docker cp scripts/backfill_snapshot_refs.py bidcopilot-app:/opt/bidcopilot/scripts/

    # 2) 先看计划
    docker exec bidcopilot-app python /opt/bidcopilot/scripts/backfill_snapshot_refs.py \
        --local-root /tmp/bidcopilot-backend

    # 3) 确认后执行
    docker exec bidcopilot-app python /opt/bidcopilot/scripts/backfill_snapshot_refs.py \
        --local-root /tmp/bidcopilot-backend --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.modules.gateway.snapshots import build_snapshot_store  # noqa: E402
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeImage  # noqa: E402

# 导入 app.main 以注册全部 ORM 模型（外键目标表需要都在 metadata 里），
# 否则单独查询 knowledge 模型会因缺少 tenants/companies 表而报 NoReferencedTableError。
import app.main  # noqa: E402,F401


def _resolve_local_path(local_root: Path, object_ref: str) -> Path | None:
    """把历史引用解析成磁盘路径。

    ``--local-root`` 指向「包含 .snapshots 的目录」（对应容器里的 backend 目录），
    历史引用形如 ``.snapshots\\knowledge\\<tenant>\\images\\<name>.png``，
    在 Windows 上写成了反斜杠。这里统一成正斜杠后直接拼接。
    """
    normalized = object_ref.replace("\\", "/").strip()
    candidate = (local_root / normalized).resolve()
    root = local_root.resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def _object_key(folder: str, tenant_id: object, object_ref: str) -> str:
    basename = object_ref.replace("\\", "/").rsplit("/", 1)[-1]
    return f"knowledge/{tenant_id}/{folder}/{basename}"


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    local_root = Path(args.local_root).resolve()
    print(f"SNAPSHOT_STORE={settings.SNAPSHOT_STORE}")
    print(f"local_root={local_root}")
    if not local_root.is_dir():
        print("local-root 不是目录", file=sys.stderr)
        return 2

    store = build_snapshot_store(settings)
    migrated = 0
    planned = 0
    missing: list[str] = []

    async with SessionLocal() as session:
        images = list(
            (
                await session.execute(
                    select(KnowledgeImage).where(KnowledgeImage.object_ref.notlike("s3://%"))
                )
            )
            .scalars()
            .all()
        )
        documents = list(
            (
                await session.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.object_ref.notlike("s3://%"))
                )
            )
            .scalars()
            .all()
        )
        print(f"待处理：图片 {len(images)} 行，文档 {len(documents)} 行")

        for kind, folder, rows in (
            ("图片", "images", images),
            ("文档", "documents", documents),
        ):
            for row in rows:
                path = _resolve_local_path(local_root, row.object_ref)
                if path is None:
                    missing.append(f"{kind} {row.id} {getattr(row, 'title', '')}: {row.object_ref}")
                    continue
                payload = path.read_bytes()
                if row.size_bytes is not None and len(payload) != row.size_bytes:
                    print(
                        f"  警告：{row.id} 磁盘 {len(payload)}B != 记录 {row.size_bytes}B",
                        file=sys.stderr,
                    )
                key = _object_key(folder, row.tenant_id, row.object_ref)
                print(f"  {kind} {row.id} {getattr(row, 'title', '')}: {len(payload)}B -> {key}")
                planned += 1
                if args.apply:
                    ref = await store.put_bytes(
                        key,
                        payload,
                        content_type=row.content_type or "application/octet-stream",
                    )
                    row.object_ref = ref
                    migrated += 1

        if args.apply:
            await session.commit()

    print()
    if missing:
        print(f"无法解析本地文件的引用 {len(missing)} 条（需人工重新上传）：")
        for line in missing:
            print(f"  - {line}")
    if args.apply:
        print(f"已迁移 {migrated} 行到 {settings.SNAPSHOT_STORE} 存储。")
    else:
        print(f"dry-run：计划迁移 {planned} 行，未修改任何数据（加 --apply 执行）。")
    return 0 if not missing else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="回填本地快照引用到当前存储")
    parser.add_argument(
        "--local-root",
        required=True,
        help="包含 .snapshots 的目录（容器内路径，例如 /tmp/bidcopilot-backend）",
    )
    parser.add_argument("--apply", action="store_true", help="真正上传并写库（默认 dry-run）")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
