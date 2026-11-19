"""合同起草：投标承诺快照 + 内置条款模板 + 既有审查条款 -> 合同初稿。

硬约束：机器生成草稿必须标注「机器生成草稿，需法务复核」；每条条款都必须带
source_json（来源为 BidCommitment、条款模板或既有 ContractClause），拒绝无来源条款。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from app.modules.contracts.lifecycle import money_str

DRAFT_DISCLAIMER = "机器生成草稿，需法务复核"
TEMPLATE_VERSION = "clm-v1"

# 条款库：source_json 必然写入 template_id，生成器不会产出无来源条款。
CLAUSE_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "template_id": "payment_terms",
        "title": "付款方式",
        "category": "payment",
        "text": "甲方按合同约定节点分期支付合同价款；乙方应在每期付款前提交等额有效发票及经确认的履约证明材料。",
    },
    {
        "template_id": "acceptance",
        "title": "验收标准",
        "category": "acceptance",
        "text": "乙方完成交付后，甲方应在约定期限内依据合同附件中的量化验收指标组织验收并出具书面验收意见。",
    },
    {
        "template_id": "penalty_cap",
        "title": "违约责任",
        "category": "penalty",
        "text": "任何一方违约应承担相应违约责任；违约金累计不超过合同总价的百分之十。",
    },
    {
        "template_id": "ip_ownership",
        "title": "知识产权",
        "category": "ip",
        "text": "双方确认履约过程中形成的知识产权归属、许可范围与使用限制，未经对方书面同意不得擅自使用或披露。",
    },
    {
        "template_id": "confidentiality",
        "title": "保密条款",
        "category": "confidentiality",
        "text": "双方对合同及履约过程中知悉的商业秘密和保密信息承担保密义务，保密期限不因合同终止而失效。",
    },
    {
        "template_id": "dispute_resolution",
        "title": "争议解决",
        "category": "dispute",
        "text": "因合同引起的争议，双方应友好协商；协商不成的，提交合同约定地有管辖权的人民法院诉讼解决。",
    },
    {
        "template_id": "change_control",
        "title": "变更与续签",
        "category": "other",
        "text": "合同变更、续签或补充协议须经双方书面确认并履行内部审批；任何变更均应留痕，不得以口头方式覆盖原条款。",
    },
)

_COMMITMENT_TITLES = {
    "price": ("合同价款（依据投标承诺）", "price"),
    "schedule": ("工期/服务期（依据投标承诺）", "schedule"),
    "warranty": ("质保期（依据投标承诺）", "quality"),
    "personnel": ("拟派人员（依据投标承诺）", "other"),
}


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _commitment_clause(item: Any, project_id: str | None) -> dict[str, Any]:
    kind = str(_field(item, "kind") or "other")
    label = str(_field(item, "label") or kind)
    value_text = str(_field(item, "value_text") or "")
    commitment_id = _field(item, "id")
    title, category = _COMMITMENT_TITLES.get(kind, (f"投标承诺：{label}", "other"))
    text = f"依据投标承诺「{label}」：{value_text}。"
    if kind == "price":
        amount = _field(item, "value_amount")
        if amount is not None:
            text = f"依据投标承诺，合同总价为人民币 {money_str(amount)} 元。"
    elif kind in {"schedule", "warranty"}:
        days = _field(item, "value_days")
        if days is not None:
            text = f"依据投标承诺「{label}」，期限为 {int(days)} 天。"
    source = {
        "type": "bid_commitment",
        "commitment_id": str(commitment_id) if commitment_id else None,
        "kind": kind,
        "label": label,
        "value_text": value_text,
        "project_id": project_id,
    }
    return {
        "title": title,
        "text": text,
        "category": category,
        "source_json": source,
    }


def _template_clause(template: dict[str, str], record: dict[str, Any]) -> dict[str, Any]:
    source: dict[str, Any] = {
        "type": "clause_template",
        "template_id": template["template_id"],
        "template_version": TEMPLATE_VERSION,
        "project_id": record.get("project_id"),
        "contract_no": record.get("contract_no"),
    }
    if template["template_id"] == "payment_terms" and record.get("amount") is not None:
        source["contract_amount"] = money_str(record.get("amount"))
    return {
        "title": template["title"],
        "text": template["text"],
        "category": template["category"],
        "source_json": source,
    }


def _review_clause(item: Any) -> dict[str, Any]:
    clause_id = _field(item, "id")
    review_id = _field(item, "review_id")
    return {
        "title": str(_field(item, "title") or "既有审查条款"),
        "text": str(_field(item, "text") or ""),
        "category": str(_field(item, "category") or "other"),
        "source_json": {
            "type": "contract_review_clause",
            "review_id": str(review_id) if review_id else None,
            "clause_id": str(clause_id) if clause_id else None,
            "clause_seq": _field(item, "seq"),
        },
    }


def build_contract_draft(
    *,
    record: dict[str, Any],
    commitments: Iterable[Any] = (),
    review_clauses: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """生成带来源的合同条款草稿；返回的每条 source_json 均非空。"""
    project_id = record.get("project_id")
    if isinstance(project_id, UUID):
        project_id = str(project_id)
    record_payload = dict(record)
    record_payload["project_id"] = project_id

    generated: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    def add(clause: dict[str, Any]) -> None:
        source = clause.get("source_json") or {}
        if not source:
            raise ValueError("禁止生成无来源条款")
        key = repr(sorted(source.items()))
        if key in seen_sources:
            return
        seen_sources.add(key)
        generated.append(clause)

    # 1) 投标承诺快照：每条都带 commitment_id 来源
    commitments_list = list(commitments)
    price_commitment_seen = False
    for item in commitments_list:
        clause = _commitment_clause(item, project_id)
        if _field(item, "kind") == "price":
            price_commitment_seen = True
        add(clause)

    # 2) 合同价款：有投标承诺时不重复；无承诺时使用条款模板来源
    if not price_commitment_seen:
        amount = record_payload.get("amount")
        amount_text = money_str(amount) if amount is not None else "待双方确认"
        add(
            {
                "title": "合同价款",
                "text": f"合同总价为人民币 {amount_text} 元，币种 {record_payload.get('currency') or 'CNY'}。",
                "category": "price",
                "source_json": {
                    "type": "clause_template",
                    "template_id": "price_terms",
                    "template_version": TEMPLATE_VERSION,
                    "project_id": project_id,
                    "record_amount": amount_text,
                },
            }
        )

    # 3) 内置条款库
    for template in CLAUSE_TEMPLATES:
        add(_template_clause(template, record_payload))

    # 4) 既有合同审查条款库（若记录关联了 review）
    for clause in review_clauses:
        add(_review_clause(clause))

    # 5) 草稿声明本身也必须有来源
    add(
        {
            "title": "草稿声明",
            "text": DRAFT_DISCLAIMER + "。本文件由 BidCopilot 依据投标承诺与条款模板自动生成，未经法务复核不得对外签署。",
            "category": "other",
            "source_json": {
                "type": "clause_template",
                "template_id": "machine_draft_disclaimer",
                "template_version": TEMPLATE_VERSION,
                "project_id": project_id,
            },
        }
    )

    for seq, clause in enumerate(generated, start=1):
        clause["seq"] = seq
    return generated


def draft_meta(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "disclaimer": DRAFT_DISCLAIMER,
        "generated_from": {
            "project_id": str(record.get("project_id")) if record.get("project_id") else None,
            "review_id": str(record.get("review_id")) if record.get("review_id") else None,
            "contract_no": record.get("contract_no"),
            "generated_on": date.today().isoformat(),
            "template_version": TEMPLATE_VERSION,
        },
    }
