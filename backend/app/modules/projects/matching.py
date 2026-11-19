import re
from datetime import date
from typing import Any

from app.core.time import business_today

LEVEL_RANK = {
    "甲级": 5,
    "一级": 5,
    "A级": 5,
    "乙级": 4,
    "二级": 4,
    "B级": 4,
    "丙级": 3,
    "三级": 3,
    "C级": 3,
    "四级": 2,
}
ASSET_LABELS = {
    "qualifications": "资质",
    "personnel": "人员",
    "performances": "业绩",
    "credits": "信用",
}


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _asset_text(asset: dict[str, Any]) -> str:
    return " ".join(str(asset.get(key) or "") for key in ("name", "cert_type", "level", "status"))


def shares_keyword(left: str | None, right: str | None) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    for token in re.findall(r"[a-z0-9]{2,}", left_norm):
        if token in right_norm:
            return True
    for index in range(max(0, len(left_norm) - 2)):
        if left_norm[index : index + 3] in right_norm:
            return True
    return False


def evidence_kinds(item: str, criteria: str | None) -> list[str]:
    text = f"{item} {criteria or ''}"
    kinds: list[str] = []
    if any(keyword in text for keyword in ("资质", "认证", "证书", "等级")):
        kinds.append("qualifications")
    if any(keyword in text for keyword in ("人员", "项目经理", "团队", "工程师", "持证")):
        kinds.append("personnel")
    if any(keyword in text for keyword in ("业绩", "案例", "合同", "项目经验")):
        kinds.append("performances")
    if any(keyword in text for keyword in ("信用", "失信", "违法")):
        kinds.append("credits")
    return kinds


def match_scoring_item(item: str, criteria: str | None, assets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    kinds = evidence_kinds(item, criteria)
    matched: list[dict[str, Any]] = []
    missing: list[str] = []
    text = f"{item} {criteria or ''}"
    for kind in kinds:
        if kind == "performances":
            candidates = list(assets.get(kind, []))
        else:
            candidates = [
                asset for asset in assets.get(kind, []) if shares_keyword(_asset_text(asset), text)
            ]
        for asset in candidates[:5]:
            matched.append({"kind": kind, "id": asset["id"], "name": asset["name"]})
        if not candidates:
            missing.append(ASSET_LABELS[kind])

    if not kinds:
        coverage = "covered"
        risk_note = "无需企业资产证据，需在章节中逐条响应评分标准。"
    elif missing and not matched:
        coverage = "missing"
        risk_note = f"缺少证据：{'、'.join(missing)}"
    elif missing:
        coverage = "partial"
        risk_note = f"部分证据缺失：{'、'.join(missing)}"
    else:
        coverage = "covered"
        risk_note = None

    matched_names = "、".join(asset["name"] for asset in matched[:3])
    strategy = "按评分标准逐条响应"
    if matched_names:
        strategy += f"，引用企业资产：{matched_names}"
    if missing:
        strategy += f"；需补齐：{'、'.join(missing)}"

    return {
        "evidence_required": [ASSET_LABELS[kind] for kind in kinds],
        "matched_assets": matched,
        "missing_evidence": missing,
        "coverage_status": coverage,
        "response_strategy": strategy,
        "risk_note": risk_note,
    }


def _level_rank(level: str | None) -> int | None:
    if not level:
        return None
    for key, rank in LEVEL_RANK.items():
        if key in level:
            return rank
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def qualification_matches(
    requirements: list[dict[str, Any]],
    assets: dict[str, list[dict[str, Any]]],
    deadline: date | None,
) -> list[dict[str, Any]]:
    effective_deadline = deadline or business_today()
    results: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        category = requirement.get("category", "OTHER")
        text = requirement.get("text") or requirement.get("name") or ""
        base = {
            "requirement_index": index,
            "category": category,
            "text": text,
            "level": requirement.get("level"),
            "quantity": requirement.get("quantity"),
            "source_span": requirement.get("source_span"),
        }
        if category == "QUALIFICATION":
            candidates = [
                asset
                for asset in assets.get("qualifications", [])
                if shares_keyword(asset.get("name"), text)
            ]
            if not candidates:
                results.append({**base, "status": "UNKNOWN", "reason": "资产库未找到匹配资质", "matched_asset_ids": []})
                continue
            candidate = candidates[0]
            expires = _parse_date(candidate.get("valid_to"))
            if expires is None:
                results.append({**base, "status": "UNKNOWN", "reason": "证书有效期缺失", "matched_asset_ids": [candidate["id"]]})
                continue
            if expires < effective_deadline:
                results.append({**base, "status": "FAIL", "reason": "证书已过期", "matched_asset_ids": [candidate["id"]]})
                continue
            required_rank = _level_rank(requirement.get("level"))
            actual_rank = _level_rank(candidate.get("level"))
            if required_rank is not None and actual_rank is None:
                results.append({**base, "status": "UNKNOWN", "reason": "资质等级无法比较", "matched_asset_ids": [candidate["id"]]})
                continue
            if required_rank is not None and actual_rank is not None and actual_rank < required_rank:
                results.append({**base, "status": "FAIL", "reason": "资质等级不足", "matched_asset_ids": [candidate["id"]]})
                continue
            results.append({**base, "status": "PASS", "reason": "资质匹配且有效期覆盖", "matched_asset_ids": [candidate["id"]]})
        elif category == "PERSONNEL":
            candidates = [
                asset
                for asset in assets.get("personnel", [])
                if shares_keyword(asset.get("cert_type") or asset.get("name"), text)
                or shares_keyword(asset.get("name"), text)
            ]
            if not candidates:
                results.append({**base, "status": "UNKNOWN", "reason": "资产库未找到匹配人员", "matched_asset_ids": []})
                continue
            candidate = candidates[0]
            expires = _parse_date(candidate.get("valid_to"))
            if expires is None:
                results.append({**base, "status": "UNKNOWN", "reason": "人员证书有效期缺失", "matched_asset_ids": [candidate["id"]]})
                continue
            status = "FAIL" if expires < effective_deadline else "PASS"
            reason = "人员证书已过期" if status == "FAIL" else "人员证书匹配且有效"
            results.append({**base, "status": status, "reason": reason, "matched_asset_ids": [candidate["id"]]})
        elif category == "PERFORMANCE":
            candidates = list(assets.get("performances", []))
            quantity = requirement.get("quantity") or 1
            if not candidates:
                results.append({**base, "status": "UNKNOWN", "reason": "资产库未找到匹配业绩", "matched_asset_ids": []})
            elif len(candidates) < quantity:
                results.append(
                    {
                        **base,
                        "status": "FAIL",
                        "reason": f"业绩数量不足（要求 {quantity}，找到 {len(candidates)}）",
                        "matched_asset_ids": [candidate["id"] for candidate in candidates],
                    }
                )
            else:
                results.append(
                    {
                        **base,
                        "status": "PASS",
                        "reason": "业绩数量满足要求",
                        "matched_asset_ids": [candidate["id"] for candidate in candidates],
                    }
                )
        elif category == "CREDIT":
            candidates = [
                asset
                for asset in assets.get("credits", [])
                if shares_keyword(asset.get("name"), text)
            ]
            if not candidates:
                results.append({**base, "status": "UNKNOWN", "reason": "资产库未找到信用记录", "matched_asset_ids": []})
                continue
            statuses = " ".join(str(asset.get("status") or "") for asset in candidates)
            if any(keyword in statuses for keyword in ("异常", "失信", "违法", "注销")):
                results.append({**base, "status": "FAIL", "reason": "信用记录存在异常", "matched_asset_ids": [c["id"] for c in candidates]})
            elif "正常" in statuses:
                results.append({**base, "status": "PASS", "reason": "信用记录正常", "matched_asset_ids": [c["id"] for c in candidates]})
            else:
                results.append({**base, "status": "UNKNOWN", "reason": "信用状态无法判断", "matched_asset_ids": [c["id"] for c in candidates]})
        else:
            results.append({**base, "status": "UNKNOWN", "reason": "该要求类型暂不支持自动判定", "matched_asset_ids": []})
    return results


def legacy_decision_payload(
    qualifications: list[dict[str, Any]], matrix: list[dict[str, Any]]
) -> dict[str, Any]:
    """旧版 Bid-No-Bid 口径（资格 + 评分证据覆盖），仅作为决策引擎的 legacy 兼容模式。"""
    passed = sum(1 for item in qualifications if item["status"] == "PASS")
    failed = sum(1 for item in qualifications if item["status"] == "FAIL")
    unknown = sum(1 for item in qualifications if item["status"] == "UNKNOWN")
    covered = sum(1 for item in matrix if item["coverage_status"] == "covered")
    partial = sum(1 for item in matrix if item["coverage_status"] == "partial")
    missing = sum(1 for item in matrix if item["coverage_status"] == "missing")

    if failed > 0:
        status = "No-Go"
    elif unknown > 0 or partial > 0 or missing > 0:
        status = "Conditional Go"
    else:
        status = "Go"

    missing_evidence = sorted(
        {item_name for item in matrix for item_name in item.get("missing_evidence", [])}
    )
    factors = [
        {"name": "资格门槛", "value": f"PASS {passed} / FAIL {failed} / UNKNOWN {unknown}"},
        {"name": "评分证据覆盖", "value": f"covered {covered} / partial {partial} / missing {missing}"},
        {"name": "缺失证据", "value": "、".join(missing_evidence) if missing_evidence else "无"},
    ]
    if status == "No-Go":
        summary = "存在硬性资格 FAIL，建议不投或先完成整改。"
    elif status == "Conditional Go":
        summary = "存在 UNKNOWN 或证据缺口，补齐后可升级为 Go。"
    else:
        summary = "资格与评分证据已覆盖，建议进入投标准备。"
    return {"status": status, "summary": summary, "factors": factors}
