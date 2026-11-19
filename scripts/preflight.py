"""Preflight checks for a BidCopilot pilot or private deployment.

Usage:
    uv run --project backend python scripts/preflight.py
    uv run --project backend python scripts/preflight.py --probe-ocr
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.modules.gateway.snapshots import build_snapshot_store  # noqa: E402


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _expected_head() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head() or "unknown"


async def _check_database(settings: Settings) -> Check:
    try:
        connection = await asyncpg.connect(_plain_dsn(settings.DATABASE_URL))
        try:
            version = await connection.fetchval("SELECT version_num FROM alembic_version")
            tables = await connection.fetchval(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name <> 'alembic_version'"
            )
        finally:
            await connection.close()
    except Exception as exc:
        return Check("PostgreSQL", "FAIL", str(exc)[:120])
    expected = _expected_head()
    if version != expected:
        return Check("PostgreSQL", "FAIL", f"migration {version}, expected {expected}")
    return Check("PostgreSQL", "PASS", f"{version}, {tables} tables")


async def _check_redis(settings: Settings) -> Check:
    client = Redis.from_url(settings.REDIS_URL)
    try:
        await client.ping()
        return Check("Redis", "PASS", "ping ok")
    except Exception as exc:
        return Check("Redis", "FAIL", str(exc)[:120])
    finally:
        await client.aclose()


async def _check_storage(settings: Settings) -> Check:
    try:
        store = build_snapshot_store(settings)
        ref = await store.put_bytes("preflight/check.txt", b"ok", "text/plain")
        await store.delete_bytes(ref)
        return Check("Snapshot storage", "PASS", settings.SNAPSHOT_STORE)
    except Exception as exc:
        return Check("Snapshot storage", "FAIL", str(exc)[:120])


async def _probe_ocr(settings: Settings) -> Check:
    from app.modules.tender.ocr import OcrQuotaError, build_vision_client

    client = build_vision_client(settings)
    if client is None or not client.configured():
        return Check("MiniMax OCR", "WARN", "not configured")
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    try:
        await client.extract_text(png)
        return Check("MiniMax OCR", "PASS", "live call ok")
    except OcrQuotaError as exc:
        return Check("MiniMax OCR", "FAIL", str(exc)[:120])
    except Exception as exc:
        return Check("MiniMax OCR", "FAIL", f"{type(exc).__name__}: {exc}"[:120])


def _readiness_checks(settings: Settings) -> list[Check]:
    checks = [
        Check(
            "LLM",
            "PASS" if settings.LLM_BASE_URL and settings.LLM_API_KEY and settings.LLM_MODEL else "FAIL",
            f"{settings.LLM_MODEL or 'not configured'} @ {settings.LLM_BASE_URL or '-'}",
        ),
        Check(
            "Judge",
            "PASS" if settings.JUDGE_BASE_URL and settings.JUDGE_API_KEY and settings.JUDGE_MODEL else "FAIL",
            f"{settings.JUDGE_MODEL or 'not configured'} @ {settings.JUDGE_BASE_URL or '-'}",
        ),
        Check(
            "KeepRisk",
            "PASS" if settings.KEEPRISK_API_BASE_URL and settings.KEEPRISK_APP_CODE else "WARN",
            settings.KEEPRISK_API_BASE_URL or "not configured",
        ),
        Check(
            "MiniMax",
            "PASS" if settings.MINIMAX_API_HOST and settings.MINIMAX_API_KEY else "WARN",
            settings.MINIMAX_API_HOST or "not configured",
        ),
    ]
    return checks


async def main() -> int:
    parser = argparse.ArgumentParser(description="BidCopilot pilot preflight")
    parser.add_argument("--probe-ocr", action="store_true", help="make a real MiniMax OCR call")
    args = parser.parse_args()
    settings = Settings(_env_file=BACKEND / ".env")

    checks = [
        await _check_database(settings),
        await _check_redis(settings),
        await _check_storage(settings),
        *_readiness_checks(settings),
    ]
    if args.probe_ocr:
        checks.append(await _probe_ocr(settings))

    print("BidCopilot 试点预检")
    for check in checks:
        print(f"[{check.status:4}] {check.name}: {check.detail}")
    failed = [check for check in checks if check.status == "FAIL"]
    print("result:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
