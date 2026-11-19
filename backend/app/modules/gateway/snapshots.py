import asyncio
from pathlib import Path
from typing import Protocol

from minio import Minio

from app.config import Settings


class SnapshotStore(Protocol):
    async def put_bytes(self, key: str, payload: bytes, content_type: str = "application/json") -> str: ...
    async def get_bytes(self, ref: str) -> bytes: ...
    async def delete_bytes(self, ref: str) -> None: ...


class LocalSnapshotStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _confined(self, ref: str) -> Path:
        """把 ref 解析到 root 内绝对路径。

        兼容存量数据里以绝对路径存储的 ref（读取/删除仍可用）；
        越界（含 `..` 逃逸）一律拒绝。
        """
        candidate = Path(ref)
        path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self.root / ref).resolve()
        )
        if not path.is_relative_to(self.root):
            raise ValueError("object key escapes snapshot root")
        return path

    def _relative_key(self, path: Path) -> str:
        """root 内绝对路径 → 相对 POSIX key，绝不透传服务器绝对路径。"""
        return path.relative_to(self.root).as_posix()

    async def put_bytes(self, key: str, payload: bytes, content_type: str = "application/json") -> str:
        path = self._confined(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, payload)
        # 总是返回相对 root 的 POSIX key（与 MinIO 的 s3://bucket/key 同性质）：
        # 相对 key 归一后原样返回；调用方传入 root 内绝对路径时转成相对 key，
        # 避免把服务器绝对路径写进对象引用再透传前端。
        return self._relative_key(path)

    async def get_bytes(self, ref: str) -> bytes:
        return await asyncio.to_thread(self._confined(ref).read_bytes)

    async def delete_bytes(self, ref: str) -> None:
        await asyncio.to_thread(self._confined(ref).unlink, missing_ok=True)


class MinioSnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.MINIO_BUCKET
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )

    async def put_bytes(self, key: str, payload: bytes, content_type: str = "application/json") -> str:
        def _put() -> None:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            from io import BytesIO

            self.client.put_object(
                self.bucket,
                key,
                BytesIO(payload),
                length=len(payload),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        return f"s3://{self.bucket}/{key}"

    async def get_bytes(self, ref: str) -> bytes:
        if not ref.startswith("s3://"):
            raise ValueError(f"unsupported object ref: {ref}")
        _, _, key = ref.partition("s3://")[2].partition("/")
        if not key:
            raise ValueError(f"invalid object ref: {ref}")

        def _get() -> bytes:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)

    async def delete_bytes(self, ref: str) -> None:
        if not ref.startswith("s3://"):
            raise ValueError(f"unsupported object ref: {ref}")
        _, _, key = ref.partition("s3://")[2].partition("/")
        if not key:
            raise ValueError(f"invalid object ref: {ref}")
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)


def build_snapshot_store(settings: Settings) -> SnapshotStore:
    if settings.SNAPSHOT_STORE == "local":
        return LocalSnapshotStore(settings.SNAPSHOT_LOCAL_DIR)
    return MinioSnapshotStore(settings)
