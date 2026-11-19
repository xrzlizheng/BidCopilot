import pytest
from pydantic import ValidationError

from app.config import Settings


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
            JWT_SECRET_KEY="short",
        )


def test_placeholder_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
            JWT_SECRET_KEY="replace-with-at-least-32-random-characters",
        )


def test_low_entropy_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
            JWT_SECRET_KEY="x" * 40,
        )


def test_snapshot_store_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
            JWT_SECRET_KEY="A1b2C3d4" + "E5f6G7h8I9j0K1l2",
            SNAPSHOT_STORE="ftp",
        )
