"""Fail when likely credentials are present in tracked or untracked project files.

Values are never printed; only path, line and rule are reported. Gitignored files
such as backend/.env are intentionally out of scope.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2_000_000
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".dump", ".tgz", ".zip"}

RULES = {
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "gitlab-token": re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt-token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    ),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "secret-assignment": re.compile(
        r"(?i)\b(OPENAI_API_KEY|LLM_API_KEY|JUDGE_API_KEY|MINIMAX_API_KEY|"
        r"MINIMAX_SEARCH_API_KEY|DEEPSEEK_SEARCH_API_KEY|KEEPRISK_APP_CODE|"
        r"KEEPRISK_MCP_TOKEN|JWT_SECRET_KEY|MODEL_SECRET_MASTER_KEY|"
        r"MINIO_SECRET_KEY|MINIO_ROOT_PASSWORD|POSTGRES_PASSWORD|SMTP_PASSWORD)"
        r"\b\s*[=:]\s*['\"]?([^\s'\"]{12,})"
    ),
}
PLACEHOLDERS = ("<", "$", "your", "change", "replace", "test", "example", "xxxx", "****", "...",
                "placeholder", "dummy", "fake", "minio")


def project_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        path = ROOT / line
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        paths.append(path)
    return paths


def scan(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in RULES.items():
            match = pattern.search(line)
            if match is None:
                continue
            if rule == "secret-assignment" and any(
                marker in match.group(2).lower() for marker in PLACEHOLDERS
            ):
                continue
            findings.append((line_number, rule))
    return findings


def files_under(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scan-dir",
        type=Path,
        default=None,
        help="scan an arbitrary directory (e.g. an offline bundle) instead of git files",
    )
    args = parser.parse_args()
    if args.scan_dir is not None:
        base = args.scan_dir.resolve()
        files = files_under(base)
    else:
        base = ROOT
        files = project_files()
    hits = [(path, finding) for path in files for finding in scan(path)]
    for path, (line_number, rule) in hits:
        try:
            label = path.relative_to(base)
        except ValueError:
            label = path
        print(f"{label}:{line_number}: {rule}")
    if hits:
        print(f"secrets check failed: {len(hits)} finding(s)", file=sys.stderr)
        return 1
    print(f"secrets check ok: {len(files)} files scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
