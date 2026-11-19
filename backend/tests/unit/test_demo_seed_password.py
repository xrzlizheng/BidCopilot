import inspect

import pytest

from app.demo import seed_demo


async def test_seed_demo_rejects_missing_password() -> None:
    """口令缺失必须明确报错，且校验发生在任何数据库操作之前。

    传哨兵对象而非真实 session：若守卫失位，会话被触达会立即炸出，
    而不是安静地走默认口令把种子种下去。
    """

    class _ExplodingSession:
        def __getattr__(self, name: str):  # pragma: no cover - 不该被触达
            raise AssertionError(f"口令缺失时不应触达数据库: {name}")

    with pytest.raises(ValueError, match="password"):
        await seed_demo(_ExplodingSession(), password=None)  # type: ignore[arg-type]


async def test_seed_demo_rejects_empty_password() -> None:
    """空串同样视为未提供：空口令本身也是弱凭据。"""

    class _ExplodingSession:
        def __getattr__(self, name: str):  # pragma: no cover - 不该被触达
            raise AssertionError(f"口令缺失时不应触达数据库: {name}")

    with pytest.raises(ValueError, match="password"):
        await seed_demo(_ExplodingSession(), password="")  # type: ignore[arg-type]


def test_seed_demo_signature_has_no_default_password() -> None:
    """签名层回归：password 不得再带任何默认值（历史弱值 "Password123!"）。"""
    parameter = inspect.signature(seed_demo).parameters["password"]
    assert parameter.default is None
