"""Post-deployment smoke test for a seeded BidCopilot demo tenant.

Usage:
    cd backend
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' uv run python ../scripts/smoke_demo.py --base-url http://127.0.0.1:8010

The demo password must be supplied explicitly via --password or the
BIDCOPILOT_DEMO_PASSWORD environment variable; there is no built-in default.

It exercises login, project workspace, factcheck/approval, compliance scan,
Excel/DOCX export and analytics without calling external data or LLM providers.
"""

import argparse
import os
import sys
from typing import Any

import httpx


class SmokeFailure(Exception):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _json(response: httpx.Response, step: str) -> Any:
    _expect(response.is_success, f"{step}: HTTP {response.status_code} {response.text[:300]}")
    return response.json()


def run(base_url: str, email: str, password: str, project_name: str | None) -> None:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60, trust_env=False) as client:
        login = _json(
            client.post("/api/v1/auth/login", json={"email": email, "password": password}),
            "login",
        )
        token = login.get("access_token")
        _expect(bool(token), "login: missing access_token")
        headers = {"Authorization": f"Bearer {token}"}
        me = _json(client.get("/api/v1/auth/me", headers=headers), "auth/me")
        print(f"login ok: {me.get('email')} / {me.get('tenant_name')}")

        projects = _json(client.get("/api/v1/projects", headers=headers), "projects")
        if project_name:
            matched = [project for project in projects if project["name"] == project_name]
        else:
            matched = [project for project in projects if project.get("status") == "won"]
        _expect(bool(matched), "projects: no matching project")
        project = matched[0]
        project_id = project["id"]
        print(f"project ok: {project['name']} ({project_id})")

        detail = _json(client.get(f"/api/v1/projects/{project_id}", headers=headers), "project detail")
        payload = (detail.get("latest_constraint") or {}).get("payload") or {}
        _expect(bool(payload.get("scoring_items")), "project detail: missing scoring items")
        print(f"constraint ok: {len(payload['scoring_items'])} scoring items")

        sections = _json(
            client.get(f"/api/v1/projects/{project_id}/sections", headers=headers), "sections"
        )
        _expect(bool(sections), "sections: no sections")
        approved = 0
        for section in sections:
            _json(
                client.post(f"/api/v1/sections/{section['id']}/factcheck", headers=headers),
                "factcheck",
            )
            _json(
                client.post(
                    f"/api/v1/sections/{section['id']}/approve",
                    headers=headers,
                    json={"acknowledge_unsourced": True},
                ),
                "approve",
            )
            approved += 1
        print(f"approval ok: {approved} sections")

        built = _json(
            client.post(f"/api/v1/projects/{project_id}/responses/build", headers=headers),
            "responses build",
        )
        responses = _json(
            client.get(f"/api/v1/projects/{project_id}/responses", headers=headers),
            "responses list",
        )
        _expect(responses["total"] == built["total"], "responses: build/list count mismatch")
        unresolved = responses["unresolved"]
        print(f"responses ok: {responses['total']} items, {unresolved} unresolved")

        ids = [item["id"] for item in responses["items"]]
        tainted = client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            headers=headers,
            json={
                "response_ids": [*ids, "00000000-0000-0000-0000-000000000000"],
                "response_status": "responded",
            },
        )
        _expect(tainted.status_code == 404, f"responses batch: foreign id must 404, got {tainted.status_code}")
        untouched = _json(
            client.get(f"/api/v1/projects/{project_id}/responses", headers=headers), "responses recheck"
        )
        _expect(
            untouched["summary"] == responses["summary"],
            "responses batch must not half-apply when one id is foreign",
        )

        picked = ids[:2]
        originals = {item["id"]: item for item in responses["items"] if item["id"] in picked}
        batched = _json(
            client.post(
                f"/api/v1/projects/{project_id}/responses/batch",
                headers=headers,
                json={
                    "response_ids": picked,
                    "response_status": "responded",
                    "response_note": "冒烟批量标记",
                },
            ),
            "responses batch",
        )
        _expect(batched["updated"] == len(picked), "responses batch: updated count mismatch")
        for response_id, original in originals.items():
            _json(
                client.patch(
                    f"/api/v1/projects/{project_id}/responses/{response_id}",
                    headers=headers,
                    json={
                        "response_status": original["response_status"],
                        "response_note": original["response_note"],
                        "evidence_refs": original["evidence_refs"],
                        "section_ref": original["section_ref"],
                    },
                ),
                f"responses restore {response_id}",
            )
        restored = _json(
            client.get(f"/api/v1/projects/{project_id}/responses", headers=headers), "responses restored"
        )
        _expect(restored["summary"] == responses["summary"], "responses batch: restore drifted")
        print(f"batch ok: tainted 404 without half-apply, {batched['updated']} marked then restored")

        report = _json(
            client.post(f"/api/v1/projects/{project_id}/compliance/scan", headers=headers),
            "compliance scan",
        )
        _expect(bool(report.get("id")), "compliance scan: missing report id")
        rule_ids = {finding["rule_id"] for finding in report.get("findings", [])}
        if unresolved:
            _expect("RESP-OPEN-001" in rule_ids, "compliance scan: RESP-OPEN-001 not reported")
        print(f"compliance ok: report {report['id']}, rules hit {len(rule_ids)}")

        xlsx = client.post(
            f"/api/v1/compliance/reports/{report['id']}/export",
            headers=headers,
            json={"format": "xlsx"},
        )
        _expect(xlsx.is_success and xlsx.content.startswith(b"PK"), "xlsx export failed")
        docx = client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            headers=headers,
            json={"dark_bid": False},
        )
        gate = ""
        if unresolved:
            _expect(docx.status_code == 409, f"export gate: expected 409, got HTTP {docx.status_code}")
            gate = "gate blocked then forced, "
            docx = client.post(
                f"/api/v1/projects/{project_id}/export/docx",
                headers=headers,
                json={"dark_bid": False, "acknowledge_unresolved": True},
            )
        _expect(
            docx.is_success and docx.content.startswith(b"PK"),
            f"docx export failed: HTTP {docx.status_code} {docx.text[:200]}",
        )
        responses_xlsx = client.get(f"/api/v1/projects/{project_id}/responses/export", headers=headers)
        _expect(
            responses_xlsx.is_success and responses_xlsx.content.startswith(b"PK"),
            "responses xlsx export failed",
        )
        print(
            f"export ok: {gate}"
            f"xlsx {len(xlsx.content)} bytes, "
            f"responses xlsx {len(responses_xlsx.content)} bytes, "
            f"docx {len(docx.content)} bytes"
        )

        analytics = _json(
            client.get("/api/v1/analytics/bid-results", headers=headers), "analytics"
        )
        _expect(analytics["summary"]["total"] >= 1, "analytics: no bid results")
        print(
            "analytics ok: "
            f"total={analytics['summary']['total']} "
            f"win_rate={analytics['summary']['win_rate']}%"
        )
        ops = _json(client.get("/api/v1/analytics/data-ops", headers=headers), "data ops")
        print(
            "data ops ok: "
            f"calls={ops['costs']['total_calls']} "
            f"failed={ops['costs']['failed_calls']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="BidCopilot demo smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("BIDCOPILOT_DEMO_PASSWORD"),
        help="demo tenant password (or set BIDCOPILOT_DEMO_PASSWORD; no built-in default)",
    )
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    if not args.password:
        print(
            "demo password required: pass --password or set BIDCOPILOT_DEMO_PASSWORD "
            "(no built-in default credential)",
            file=sys.stderr,
        )
        return 2
    try:
        run(args.base_url, args.email, args.password, args.project)
    except (SmokeFailure, httpx.HTTPError) as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
