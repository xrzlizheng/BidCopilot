import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

ALLOWED = {
    "0bsd",
    "apache-2.0",
    "apache software license",
    "bsd",
    "bsd license",
    "bsd-2-clause",
    "bsd-3-clause",
    "blueoak-1.0.0",
    "cc-by-4.0",
    "cc0-1.0",
    "isc",
    "isc license (iscl)",
    "3-clause bsd license",
    "mit",
    "mit license",
    "mit-0",
    "mit-cmu",
    "mpl-2.0",
    "odc-by-1.0",
    "psf-2.0",
    "python-2.0",
    "unlicense",
    # MinerU：Apache-2.0 + 附加条款（MAU>1 亿或月收入>2000 万美元才需单独商业许可；
    # 在线服务须显著标注使用 MinerU——署名见 README「开源协议 → 依赖署名」节）。
    # 与本仓规模无关的商业门槛 + 已履行的署名义务，判定与 AGENTS.md 的
    # 「只吸收 Apache-2.0 兼容代码」不冲突（MinerU 是依赖，代码不进本仓）。
    "licenseref-mineru-open-source-license",
}
BLOCKED_MARKERS = ("agpl", "gpl", "sspl", "cc-by-nc", "noncommercial")

# pip-licenses 对缺 License 分类器、或把 LICENSE 原文塞进 License 字段的包
# 报 UNKNOWN / 整段文本；这些是逐个核对过分发包内 LICENSE 原文后确认的许可
# （证据见各条目注释），不是放行未知许可。
KNOWN_LICENSES = {
    # resvg_py 0.5.0 dist-info/licenses/LICENSE 为 MIT 原文
    # （"Permission is hereby granted, free of charge, ..."），
    # 但 METADATA 无 License 分类器，pip-licenses 报 UNKNOWN。
    "resvg_py": "mit",
    # mineru-llama-cpp 的 METADATA 把整段 MIT 原文作为 License 值返回；
    # 原文即 MIT（"Copyright (c) 2026 mineru-llama-cpp contributors"）。
    "mineru-llama-cpp": "mit",
}


def normalize(value: str) -> str:
    return value.strip().lower()


def is_blocked(license_name: str) -> bool:
    lowered = normalize(license_name)
    return any(marker in lowered for marker in BLOCKED_MARKERS)


def is_allowed(license_name: str) -> bool:
    if is_blocked(license_name):
        return False
    normalized = normalize(license_name)
    if normalized in ALLOWED:
        return True
    if "unlicense" in normalized or "mozilla public license" in normalized:
        return True
    text = normalized.replace("(", " ").replace(")", " ")
    tokens = [token.strip() for token in re.split(r"[,;/]|\s+or\s+|\s+and\s+", text) if token.strip()]
    return any(token in ALLOWED for token in tokens)


def backend_licenses() -> list[tuple[str, str]] | None:
    """返回依赖许可证清单；前置工具不可用时返回 None 表示「未检查」。

    None 与 [] 语义不同：前者是 skipped，必须由 main() 判定为失败，
    否则依赖缺失时门禁会静默放行。
    """
    if not shutil.which("uv"):
        print(
            "error: 'uv' not found in PATH, backend licenses were NOT checked",
            file=sys.stderr,
        )
        return None
    try:
        result = subprocess.run(
            ["uv", "run", "--no-sync", "pip-licenses", "--format=json"],
            cwd=BACKEND,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: uv pip-licenses failed ({exc}), backend licenses were NOT checked", file=sys.stderr)
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: cannot parse pip-licenses output ({exc})", file=sys.stderr)
        return None
    rows: list[tuple[str, str]] = []
    for item in payload:
        name = item.get("Name") or item.get("name") or "unknown"
        license_name = item.get("License") or item.get("license") or "UNKNOWN"
        if normalize(name) in KNOWN_LICENSES:
            license_name = KNOWN_LICENSES[normalize(name)]
        rows.append((name, license_name))
    return rows


def _pnpm_command(args: list[str]) -> list[str]:
    executable = shutil.which("pnpm") or "pnpm"
    if os.name == "nt":
        return ["cmd", "/c", executable, *args]
    return [executable, *args]


def frontend_licenses() -> list[tuple[str, str]] | None:
    if not shutil.which("pnpm"):
        print(
            "error: 'pnpm' not found in PATH, frontend licenses were NOT checked",
            file=sys.stderr,
        )
        return None
    try:
        result = subprocess.run(
            _pnpm_command(["licenses", "list", "--json"]),
            cwd=FRONTEND,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: pnpm licenses failed ({exc}), frontend licenses were NOT checked", file=sys.stderr)
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: cannot parse pnpm licenses output ({exc})", file=sys.stderr)
        return None
    rows: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for license_name, packages in payload.items():
            if isinstance(packages, list):
                for package in packages:
                    name = package.get("name", "unknown") if isinstance(package, dict) else "unknown"
                    rows.append((name, license_name))
            elif isinstance(packages, dict):
                rows.append((packages.get("name", "unknown"), license_name))
    elif isinstance(payload, list):
        for package in payload:
            if isinstance(package, dict):
                rows.append(
                    (package.get("name", "unknown"), package.get("license", "UNKNOWN"))
                )
    return rows


def check(rows: list[tuple[str, str]], label: str) -> list[str]:
    violations: list[str] = []
    seen: set[str] = set()
    for name, license_name in rows:
        if name in {"bidcopilot-backend"}:
            continue
        normalized = normalize(license_name)
        if normalized in seen:
            continue
        seen.add(normalized)
        if not is_allowed(license_name):
            violations.append(f"{label}: {name} uses unapproved license {license_name}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="前置工具缺失时降级为 warning 并返回 0（仅限本地开发；CI 不得使用）",
    )
    args = parser.parse_args()

    backend_rows = backend_licenses()
    frontend_rows = None
    skipped: list[str] = []
    if backend_rows is None:
        skipped.append("backend")
    if not args.backend_only:
        frontend_rows = frontend_licenses()
        if frontend_rows is None:
            skipped.append("frontend")

    violations = check(backend_rows or [], "backend")
    if not args.backend_only:
        violations.extend(check(frontend_rows or [], "frontend"))
    if violations:
        print("license violations:")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    if skipped:
        message = f"license check incomplete: {', '.join(skipped)} not scanned"
        if args.allow_skip:
            print(f"warning: {message} (--allow-skip)", file=sys.stderr)
            return 0
        print(f"error: {message}", file=sys.stderr)
        print("hint: install the missing tool, or pass --allow-skip for local dev only", file=sys.stderr)
        return 2
    print("license check ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
