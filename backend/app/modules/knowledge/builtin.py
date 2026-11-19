from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.models import KnowledgePack

BUILTIN_VERSION = "1.0"

STRUCTURE_PACKS: dict[str, dict[str, Any]] = {
    "builtin.gov_it": {
        "name": "政府采购 · IT 服务",
        "description": "适用于政务云、系统集成、运维服务类项目。",
        "sections": [
            {
                "key": "bid_letter",
                "title": "投标函",
                "section_type": "cover",
                "purpose": "正式响应招标文件，声明投标意愿与报价承诺。",
                "required_materials": ["投标函", "法定代表人授权书"],
                "guidance": "引用项目名称/编号，声明响应招标文件全部实质性要求。",
            },
            {
                "key": "qualification",
                "title": "资格证明文件",
                "section_type": "qualification",
                "purpose": "证明投标人满足资格门槛。",
                "required_materials": ["营业执照", "信息系统集成资质", "项目经理证书", "业绩合同"],
                "guidance": "按招标文件资格条款顺序编排，证书附有效期。",
            },
            {
                "key": "technical",
                "title": "技术方案",
                "section_type": "technical",
                "purpose": "响应技术评分项，说明方案、实施与保障。",
                "required_materials": ["技术方案", "人员配置", "实施计划"],
                "guidance": "按评分项逐条响应，给出架构、计划、人员与风险控制。",
            },
            {
                "key": "commercial",
                "title": "商务响应",
                "section_type": "commercial",
                "purpose": "响应商务条款、报价与合同要求。",
                "required_materials": ["报价表", "商务偏离表", "业绩"],
                "guidance": "偏离表逐条标注响应/偏离，报价与投标函一致。",
            },
            {
                "key": "service",
                "title": "售后服务与承诺",
                "section_type": "service",
                "purpose": "说明运维、培训、响应时效与质保。",
                "required_materials": ["服务承诺", "培训方案"],
                "guidance": "量化响应时间、到场时间、巡检频次与质保期限。",
            },
        ],
    },
    "builtin.gov_goods": {
        "name": "政府采购 · 货物",
        "description": "适用于设备、软硬件采购与交付类项目。",
        "sections": [
            {
                "key": "bid_letter",
                "title": "投标函",
                "section_type": "cover",
                "purpose": "声明投标意愿与报价承诺。",
                "required_materials": ["投标函", "法定代表人授权书"],
                "guidance": "明确交付期、质保期与报价有效期。",
            },
            {
                "key": "qualification",
                "title": "资格证明文件",
                "section_type": "qualification",
                "purpose": "证明投标人与产品资质。",
                "required_materials": ["营业执照", "产品授权", "检测报告"],
                "guidance": "逐项对应资格条款，附授权链与检测报告。",
            },
            {
                "key": "technical",
                "title": "技术规格响应",
                "section_type": "technical",
                "purpose": "逐条响应技术参数与偏离。",
                "required_materials": ["技术参数表", "产品彩页"],
                "guidance": "参数逐条响应，偏离项给出替代方案与影响。",
            },
            {
                "key": "commercial",
                "title": "商务与报价",
                "section_type": "commercial",
                "purpose": "报价、交付、验收与付款响应。",
                "required_materials": ["报价表", "交付计划"],
                "guidance": "报价口径与税率清晰，交付计划到周。",
            },
        ],
    },
    "builtin.engineering": {
        "name": "工程类投标",
        "description": "适用于弱电、智能化、机房与工程实施类项目。",
        "sections": [
            {
                "key": "bid_letter",
                "title": "投标函",
                "section_type": "cover",
                "purpose": "声明投标意愿、工期与质量目标。",
                "required_materials": ["投标函", "法定代表人授权书"],
                "guidance": "明确工期、质量标准与安全责任。",
            },
            {
                "key": "qualification",
                "title": "资格审查资料",
                "section_type": "qualification",
                "purpose": "证明企业、人员与业绩资格。",
                "required_materials": ["营业执照", "资质证书", "安全生产许可证", "项目经理证书"],
                "guidance": "证书有效期与人员社保信息可核验。",
            },
            {
                "key": "construction",
                "title": "施工组织设计",
                "section_type": "technical",
                "purpose": "说明施工部署、进度、质量与安全。",
                "required_materials": ["施工组织设计", "进度计划", "安全方案"],
                "guidance": "关键工序、里程碑与资源投入可落地。",
            },
            {
                "key": "commercial",
                "title": "商务标",
                "section_type": "commercial",
                "purpose": "工程量清单报价与合同响应。",
                "required_materials": ["工程量清单报价", "商务偏离表"],
                "guidance": "报价与清单一致，暂列金额与税金单列。",
            },
        ],
    },
    "builtin.enterprise_general": {
        "name": "企业通用投标",
        "description": "适用于民营企业采购与通用服务项目。",
        "sections": [
            {
                "key": "bid_letter",
                "title": "投标函",
                "section_type": "cover",
                "purpose": "声明投标意愿与承诺。",
                "required_materials": ["投标函"],
                "guidance": "明确报价有效期与联系人。",
            },
            {
                "key": "company",
                "title": "公司介绍与资质",
                "section_type": "qualification",
                "purpose": "建立企业可信度。",
                "required_materials": ["营业执照", "资质证书", "业绩"],
                "guidance": "用可核验的资质与案例说明能力。",
            },
            {
                "key": "solution",
                "title": "解决方案",
                "section_type": "technical",
                "purpose": "响应需求并提供实施路径。",
                "required_materials": ["技术方案", "实施计划"],
                "guidance": "需求-方案-证据三段式，避免空泛描述。",
            },
            {
                "key": "service",
                "title": "服务与报价",
                "section_type": "commercial",
                "purpose": "说明服务内容与报价。",
                "required_materials": ["报价表", "服务承诺"],
                "guidance": "服务边界、SLA 与报价口径清晰。",
            },
        ],
    },
}

FORMAT_PACKS: dict[str, dict[str, Any]] = {
    "builtin.format_gov": {
        "name": "政采标准格式",
        "description": "A4、常规页边距、宋体正文、黑体标题、1.5 倍行距。",
        "format": {
            "page": {
                "size": "A4",
                "margins_cm": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
            },
            "body": {
                "font": "宋体",
                "size_pt": 12,
                "line_spacing": 1.5,
                "first_line_indent_chars": 2,
            },
            "headings": [
                {"level": 1, "font": "黑体", "size_pt": 16, "bold": True},
                {"level": 2, "font": "黑体", "size_pt": 14, "bold": True},
                {"level": 3, "font": "宋体", "size_pt": 12, "bold": True},
            ],
        },
    },
    "builtin.format_enterprise": {
        "name": "企业简洁格式",
        "description": "A4、较窄页边距、宋体正文、微软雅黑标题、1.35 倍行距。",
        "format": {
            "page": {
                "size": "A4",
                "margins_cm": {"top": 2.2, "bottom": 2.2, "left": 2.6, "right": 2.6},
            },
            "body": {
                "font": "宋体",
                "size_pt": 11,
                "line_spacing": 1.35,
                "first_line_indent_chars": 2,
            },
            "headings": [
                {"level": 1, "font": "微软雅黑", "size_pt": 15, "bold": True},
                {"level": 2, "font": "微软雅黑", "size_pt": 13, "bold": True},
                {"level": 3, "font": "宋体", "size_pt": 11, "bold": True},
            ],
        },
    },
}


async def ensure_builtin_packs(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('builtin_packs'))"))
    existing = {
        pack.code: pack
        for pack in (
            await session.execute(select(KnowledgePack).where(KnowledgePack.is_builtin.is_(True)))
        )
        .scalars()
        .all()
    }
    for code, spec in STRUCTURE_PACKS.items():
        pack = existing.get(code)
        if pack is None:
            session.add(
                KnowledgePack(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    category="structure",
                    description=spec["description"],
                    content_json={"sections": spec["sections"]},
                    version=BUILTIN_VERSION,
                    is_builtin=True,
                    scope="system",
                    status="published",
                    owner_status="active",
                )
            )
        else:
            pack.name = spec["name"]
            pack.description = spec["description"]
            pack.content_json = {"sections": spec["sections"]}
            pack.version = BUILTIN_VERSION
            pack.enabled = True
            pack.scope = "system"
            pack.status = "published"
            pack.owner_status = "active"
    for code, spec in FORMAT_PACKS.items():
        pack = existing.get(code)
        if pack is None:
            session.add(
                KnowledgePack(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    category="format",
                    description=spec["description"],
                    content_json=spec["format"],
                    version=BUILTIN_VERSION,
                    is_builtin=True,
                    scope="system",
                    status="published",
                    owner_status="active",
                )
            )
        else:
            pack.name = spec["name"]
            pack.description = spec["description"]
            pack.content_json = spec["format"]
            pack.version = BUILTIN_VERSION
            pack.enabled = True
            pack.scope = "system"
            pack.status = "published"
            pack.owner_status = "active"
    await session.flush()
