import json
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def main() -> int:
    backend = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    chart = yaml.safe_load(
        (ROOT / "deploy" / "helm" / "bidcopilot" / "Chart.yaml").read_text(encoding="utf-8")
    )
    values = yaml.safe_load(
        (ROOT / "deploy" / "helm" / "bidcopilot" / "values.yaml").read_text(encoding="utf-8")
    )
    found = {
        "backend": backend["project"]["version"],
        "frontend": frontend["version"],
        "chart": chart["version"],
        "chart.appVersion": str(chart["appVersion"]),
    }
    mismatches = {key: value for key, value in found.items() if value != VERSION}
    if mismatches:
        print(f"version mismatch, expected {VERSION}: {mismatches}")
        return 1
    # 单一构建产物（ADR-0001）：chart 消费与单容器同一镜像。tag 留空表示
    # 使用 Chart.AppVersion（每个版本一个新 tag），不再要求写死 tag。
    image = values.get("image") or {}
    if not image.get("repository"):
        print("values.image.repository must be set")
        return 1
    print(f"versions consistent: {VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
