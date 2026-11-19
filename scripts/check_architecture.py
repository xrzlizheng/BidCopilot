import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "backend" / "app"

FORBIDDEN_BY_PREFIX: dict[str, set[str]] = {
    "modules/opportunities": {"httpx", "app.modules.gateway.keeprisk_client"},
    "modules/assets": {"httpx", "app.modules.gateway"},
    "modules/gateway": {"app.modules.opportunities", "app.modules.assets"},
    "core": {"app.modules"},
}


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def matches(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(f"{forbidden}.")


def main() -> int:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT).as_posix()
        imported = module_imports(path)
        if relative.startswith("modules/opportunities/"):
            forbidden = FORBIDDEN_BY_PREFIX["modules/opportunities"]
        elif relative.startswith("modules/assets/"):
            forbidden = FORBIDDEN_BY_PREFIX["modules/assets"]
        elif relative.startswith("modules/gateway/"):
            forbidden = FORBIDDEN_BY_PREFIX["modules/gateway"]
        elif relative.startswith("core/"):
            forbidden = FORBIDDEN_BY_PREFIX["core"]
        else:
            forbidden = set()
        for item in imported:
            if item in {"tests", "alembic"} or item.startswith(("tests.", "alembic.")):
                violations.append(f"{relative}: forbidden import {item}")
            if any(matches(item, rule) for rule in forbidden):
                violations.append(f"{relative}: forbidden import {item}")
    if violations:
        print("architecture violations:")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(f"architecture ok: {len(list(APP_ROOT.rglob('*.py')))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
