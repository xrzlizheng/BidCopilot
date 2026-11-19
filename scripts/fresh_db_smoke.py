"""Validate a fresh deployment against a temporary empty database.

Creates a uniquely named PostgreSQL database, runs all migrations, seeds the demo
tenant, starts an isolated API process, runs the post-deployment smoke test and
drops the temporary database. The source database is never modified.

Usage:
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' uv run --project backend python scripts/fresh_db_smoke.py

The demo password must be supplied explicitly via --password or the
BIDCOPILOT_DEMO_PASSWORD environment variable; there is no built-in default.
"""

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402


def _admin_and_temp_dsn(base: str, database: str) -> tuple[str, str]:
    parts = urlsplit(base)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    temp = urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))
    return admin.replace("postgresql+asyncpg://", "postgresql://"), temp


async def _create_database(admin_dsn: str, database: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'CREATE DATABASE "{database}"')
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str, database: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    finally:
        await connection.close()


async def _count_tables(dsn: str) -> int:
    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        count = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
        )
        return int(count)
    finally:
        await connection.close()


def _run(command: list[str], env: dict[str, str]) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=BACKEND, env=env, check=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, process: subprocess.Popen[bytes], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"API exited before becoming ready (code {process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.5)
    raise RuntimeError(f"API did not become ready within {timeout}s")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="fresh database deployment smoke")
    parser.add_argument("--keep", action="store_true", help="keep the temporary database for inspection")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("BIDCOPILOT_DEMO_PASSWORD"),
        help="demo tenant password (or set BIDCOPILOT_DEMO_PASSWORD; no built-in default)",
    )
    args = parser.parse_args()
    if not args.password:
        print(
            "demo password required: pass --password or set BIDCOPILOT_DEMO_PASSWORD "
            "(no built-in default credential)",
            file=sys.stderr,
        )
        return 2

    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    database = f"bidcopilot_fresh_{suffix}"
    if not re.fullmatch(r"[a-z0-9_]+", database):
        raise SystemExit("invalid temporary database name")

    settings = Settings(_env_file=BACKEND / ".env")
    admin_dsn, temp_dsn = _admin_and_temp_dsn(settings.DATABASE_URL, database)
    env = os.environ.copy()
    env["DATABASE_URL"] = temp_dsn
    server: subprocess.Popen[bytes] | None = None
    created = False
    try:
        asyncio.run(_create_database(admin_dsn, database))
        created = True
        print(f"created temporary database {database}", flush=True)

        _run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        table_count = asyncio.run(_count_tables(temp_dsn))
        print(f"schema tables: {table_count} business + alembic_version", flush=True)
        _run(
            [
                sys.executable,
                "-m",
                "app.cli",
                "seed-demo",
                "--slug",
                "demo",
                "--email",
                args.email,
                "--password",
                args.password,
            ],
            env,
        )

        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        _wait_ready(f"{base_url}/healthz", server)
        _wait_ready(f"{base_url}/readyz", server)
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "smoke_demo.py"),
                "--base-url",
                base_url,
                "--email",
                args.email,
                "--password",
                args.password,
            ],
            env,
        )
        print(f"fresh deployment smoke passed on {database}", flush=True)
        return 0
    finally:
        _stop(server)
        if created and not args.keep:
            asyncio.run(_drop_database(admin_dsn, database))
            print(f"dropped temporary database {database}", flush=True)
        elif created:
            print(f"kept temporary database {database}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
