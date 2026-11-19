"""暗标（盲评）上传件自查：身份泄露、自指表述、元数据与页眉页脚残留。

只做合规提示与证据定位，不提供规避检测能力；判定依据见
docs/product/2026-11-02-anbiao-blind-review-rules.md 的 A 组规则。
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import Company, Personnel
from app.modules.identity.models import Tenant

SELF_REFERENCE = re.compile(r"我公司|本公司|我司|本单位|我单位|我方|我集团|我企业")
LEGAL_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "集团", "公司")
CONTACT_KEYS = (
    "phone",
    "telephone",
    "mobile",
    "tel",
    "email",
    "mail",
    "website",
    "site",
    "url",
    "fax",
    "address",
    "reg_address",
    "registered_address",
    "legal_representative",
    "legal_person",
)
CONTACT_PATTERN = re.compile(
    r"(?:1[3-9]\d{9}|0\d{2,3}-?\d{7,8}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|https?://[^\s，。；]+)"
)

RULE_BY_KIND = {
    "company": (
        "BID-101",
        "企业名称/简称/曾用名",
        "改为「投标人」等中性表述；名称与简称都属于可识别信息。",
    ),
    "credit_code": (
        "BID-102",
        "统一社会信用代码",
        "删除或用「投标人」指代；信用代码可直接定位主体。",
    ),
    "contact": (
        "BID-103",
        "联系方式/地址/网址",
        "删除电话、邮箱、网址与详细地址；暗标技术标不应出现联系方式。",
    ),
    "personnel": (
        "BID-105",
        "人员姓名/证书编号",
        "用岗位（项目经理/技术负责人等）替代姓名；证书编号一并移除。",
    ),
    "metadata": (
        "BID-106",
        "文档元数据",
        "清空作者、单位、最后修改者与自定义属性后再提交（暗标要求去身份）。",
    ),
    "filename": (
        "BID-107",
        "文件名含身份信息",
        "按招标文件要求使用中性文件名，不得含企业名/人名/内部简称。",
    ),
    "header_footer": (
        "BID-108",
        "页眉页脚残留",
        "清空页眉页脚中的企业名/标识/装饰线；页码按招标文件要求设置。",
    ),
    "self_reference": (
        "BID-104",
        "自指表述（我公司/本单位）",
        "统一改为「投标人」「本项目」；自指同样会暴露投标人身份。",
    ),
}


@dataclass(frozen=True)
class AnbiaoIdentity:
    terms: tuple[tuple[str, str], ...]  # (term, kind)，按长度降序
    personals: tuple[str, ...]


def _aliases(name: str) -> list[str]:
    for suffix in LEGAL_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= 4:
            return [name[: -len(suffix)]]
    return []


async def load_tenant_identity(session: AsyncSession, tenant_id: UUID) -> AnbiaoIdentity:
    tenant = await session.get(Tenant, tenant_id)
    terms: dict[str, str] = {}
    if tenant is not None and tenant.name:
        terms[tenant.name] = "company"
    companies = list(
        (
            await session.execute(
                select(Company).where(Company.tenant_id == tenant_id).limit(200)
            )
        ).scalars()
    )
    for company in companies:
        terms[company.name] = "company"
        for alias in _aliases(company.name):
            terms.setdefault(alias, "company")
        if company.credit_code and len(company.credit_code.strip()) >= 6:
            terms[company.credit_code.strip()] = "credit_code"
        contacts = company.contacts or {}
        for key in CONTACT_KEYS:
            value = contacts.get(key)
            if isinstance(value, str) and len(value.strip()) >= 4:
                terms[value.strip()] = "contact"
    personnel = list(
        (
            await session.execute(
                select(Personnel).where(Personnel.tenant_id == tenant_id).limit(500)
            )
        ).scalars()
    )
    personals: list[str] = []
    for person in personnel:
        if person.name and len(person.name.strip()) >= 2:
            personals.append(person.name.strip())
            terms[person.name.strip()] = "personnel"
        if person.cert_no and len(person.cert_no.strip()) >= 6:
            terms[person.cert_no.strip()] = "personnel"
    ordered = tuple(sorted(terms.items(), key=lambda item: (-len(item[0]), item[0])))
    return AnbiaoIdentity(terms=ordered, personals=tuple(personals))


def _evidence(text: str, start: int, end: int) -> str:
    return text[max(0, start - 30) : end + 30].replace("\n", " ")[:200]


def scan_document(
    *,
    text: str,
    filename: str,
    metadata: dict[str, Any] | None,
    header_footer: str = "",
    identity: AnbiaoIdentity,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(rule_id: str, title: str, suggestion: str, location: str, evidence: str) -> None:
        key = (rule_id, evidence[:80])
        if key in seen:
            return
        seen.add(key)
        findings.append(
            {
                "rule_id": rule_id,
                "level": "high",
                "title": title,
                "suggestion": suggestion,
                "location": location,
                "evidence": evidence,
            }
        )

    for term, kind in identity.terms:
        rule_id, title, suggestion = RULE_BY_KIND[kind]
        for match in re.finditer(re.escape(term), text):
            add(rule_id, title, suggestion, "正文", _evidence(text, match.start(), match.end()))
    for match in SELF_REFERENCE.finditer(text):
        rule_id, title, suggestion = RULE_BY_KIND["self_reference"]
        add(rule_id, title, suggestion, "正文", _evidence(text, match.start(), match.end()))
    for match in CONTACT_PATTERN.finditer(text):
        # 联系方式即使不属于本企业也提示：暗标技术标不应出现联系方式。
        rule_id, title, suggestion = RULE_BY_KIND["contact"]
        add(rule_id, title, suggestion, "正文", _evidence(text, match.start(), match.end()))

    if header_footer.strip():
        for term, kind in identity.terms:
            if kind in {"company", "personnel"} and term in header_footer:
                rule_id, title, suggestion = RULE_BY_KIND["header_footer"]
                add(rule_id, title, suggestion, "页眉/页脚", header_footer[:200])
                break

    for field in ("author", "last_modified_by", "company", "title"):
        value = (metadata or {}).get(field)
        if not value:
            continue
        text_value = str(value).strip()
        if not text_value:
            continue
        rule_id, title, suggestion = RULE_BY_KIND["metadata"]
        add(rule_id, title, suggestion, f"元数据:{field}", text_value[:200])

    for term, kind in identity.terms:
        if kind in {"company", "personnel"} and term in filename:
            rule_id, title, suggestion = RULE_BY_KIND["filename"]
            add(rule_id, title, suggestion, "文件名", filename)
            break
    return findings
