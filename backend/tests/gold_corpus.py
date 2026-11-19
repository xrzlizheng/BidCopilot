import contextlib
import re
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Any

from docx import Document

from app.modules.tender.parser import build_constraint_set, parse_document


@dataclass(frozen=True)
class GoldCase:
    name: str
    filename: str
    content: bytes
    scoring_items: tuple[str, ...]
    qualifications: tuple[str, ...]
    reject_clauses: tuple[str, ...]
    budget_amount: str | None
    dark_bid: bool


@dataclass(frozen=True)
class Prf:
    expected: int
    predicted: int
    matched: int
    covered: int = 0

    @property
    def precision(self) -> float:
        if not self.predicted:
            return 1.0
        return self.covered / self.predicted

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


@dataclass(frozen=True)
class GoldResult:
    case: str
    scoring: Prf
    qualification: Prf
    reject: Prf
    budget_ok: bool
    dark_bid_ok: bool


_PUNCT = re.compile(r"[\s，,。.；;：:、（）()【】\[\]“”\"'《》/·—\-]+")


def _canon(value: str) -> str:
    return _PUNCT.sub("", value)


def _match_scoring(expected: tuple[str, ...], predicted: list[dict[str, Any]]) -> Prf:
    expected_keys = [_canon(item) for item in expected]
    predicted_keys = [_canon(item["item"]) for item in predicted]
    matched = sum(
        1 for key in expected_keys if any(key in other or other in key for other in predicted_keys)
    )
    covered = sum(
        1 for other in predicted_keys
        if any(key in other or other in key for key in expected_keys)
    )
    return Prf(
        expected=len(expected_keys),
        predicted=len(predicted_keys),
        matched=matched,
        covered=covered,
    )


def _match_clauses(expected: tuple[str, ...], predicted: list[dict[str, Any]], key: str) -> Prf:
    expected_keys = [_canon(item) for item in expected]
    predicted_keys = [_canon(item[key]) for item in predicted]
    matched_expected = sum(1 for key_ in expected_keys if any(key_ in other for other in predicted_keys))
    matched_predicted = sum(1 for key_ in predicted_keys if any(other in key_ for other in expected_keys))
    return Prf(
        expected=len(expected_keys),
        predicted=len(predicted_keys),
        matched=matched_expected,
        covered=matched_predicted,
    )


def _new_document() -> Document:
    return Document()


def _save(document: Document) -> bytes:
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _gov_it_case() -> GoldCase:
    document = _new_document()
    document.add_heading("第一章 招标公告", level=1)
    document.add_paragraph("项目编号：ZC-2026-0815")
    document.add_paragraph("项目名称：市级政务云安全运维服务采购项目")
    document.add_paragraph("预算金额：人民币 860.5 万元。")
    document.add_paragraph("获取招标文件时间：2026年10月25日至2026年11月1日。")
    document.add_paragraph("投标截止时间：2026年11月18日 09:30。")
    document.add_paragraph("开标时间：2026年11月18日 09:30。")
    document.add_paragraph("投标人须具备涉密信息系统集成乙级及以上资质。")
    document.add_paragraph("投标人须提供近三年度经审计的财务报告。")
    document.add_paragraph("项目经理须具备信息系统项目管理师（高级）证书。")
    document.add_paragraph("近三年具有不少于 3 个类似政务云项目业绩。")
    document.add_paragraph("未按招标文件要求密封的投标文件将被否决。")
    document.add_paragraph("投标报价超过最高限价的，其投标无效。")
    document.add_paragraph("投标文件应包括投标函、报价表、技术方案、服务承诺。")
    document.add_paragraph("投标文件应采用 A4 纸双面打印，正文宋体小四，行距 1.5 倍。")
    document.add_paragraph("本项目采用暗标评审，技术方案不得出现投标人名称。")
    table = document.add_table(rows=6, cols=3)
    for col, text in enumerate(("评分项", "分值", "评分标准")):
        table.cell(0, col).text = text
    rows = (
        ("技术方案", "45 分", "方案完整、可落地，架构合理"),
        ("服务团队（项目经理）", "15 分", "人员配置合理，经验匹配"),
        ("类似业绩", "10 分", "提供合同关键页"),
        ("投标报价", "30 分", "价格分统一采用低价优先法"),
        ("合计", "100 分", "满分 100 分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="gov_it_table",
        filename="市级政务云安全运维招标文件.docx",
        content=_save(document),
        scoring_items=("技术方案", "服务团队", "类似业绩", "投标报价"),
        qualifications=(
            "涉密信息系统集成乙级",
            "近三年度经审计的财务报告",
            "信息系统项目管理师",
            "不少于 3 个类似政务云项目业绩",
        ),
        reject_clauses=("未按招标文件要求密封", "超过最高限价"),
        budget_amount="8605000",
        dark_bid=True,
    )


def _engineering_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某市科创中心改扩建工程招标文件", level=1)
    document.add_paragraph("招标人：某市城市建设投资集团有限公司")
    document.add_paragraph("联系人：张工　联系电话：0571-88880000")
    document.add_paragraph("招标控制价：人民币 8,500,000.00 元。")
    document.add_paragraph("投标保证金：人民币 20 万元。")
    document.add_paragraph("投标截止时间：2026年12月2日 14:00。")
    document.add_paragraph("投标人须具有建筑工程施工总承包二级及以上资质。")
    document.add_paragraph("拟派项目经理须具备一级建造师注册证书和安全生产考核合格证（B证）。")
    document.add_paragraph("投标人近五年应完成不少于 2 项合同金额不低于 3000 万元的类似工程。")
    document.add_paragraph("投标人应提供近一年度财务审计报告。")
    document.add_paragraph("有下列情形之一的，评标委员会将否决其投标：")
    document.add_paragraph("投标文件未按规定签字盖章的，作无效投标处理。")
    document.add_paragraph("投标人串通投标的，取消投标资格。")
    document.add_paragraph("投标文件组成：投标函、商务标、技术标、资格审查资料。")
    document.add_paragraph("投标文件正本一份、副本四份，采用 A4 纸胶装，正文仿宋四号字。")
    document.add_paragraph("技术方案：40 分。施工组织设计完整、针对性强。")
    document.add_paragraph("商务业绩：20 分。近三年类似工程业绩。")
    document.add_paragraph("投标报价：40 分。")
    return GoldCase(
        name="engineering_paragraph",
        filename="科创中心改扩建工程招标文件.docx",
        content=_save(document),
        scoring_items=("技术方案", "商务业绩", "投标报价"),
        qualifications=(
            "建筑工程施工总承包二级及以上资质",
            "一级建造师注册证书",
            "不少于 2 项合同金额不低于 3000 万元",
            "近一年度财务审计报告",
        ),
        reject_clauses=("未按规定签字盖章", "取消投标资格"),
        budget_amount="8500000",
        dark_bid=False,
    )


def _goods_case() -> GoldCase:
    document = _new_document()
    document.add_heading("医疗设备采购项目询价文件", level=1)
    document.add_paragraph("采购预算：人民币 198 万元。")
    document.add_paragraph("报价截止时间：2026年11月25日 17:00。")
    document.add_paragraph("投标人须具备医疗器械经营许可证（第二类及以上）。")
    document.add_paragraph("投标人须通过 ISO 9001 质量管理体系认证。")
    document.add_paragraph("所投产品须提供制造商授权书和售后服务承诺函。")
    document.add_paragraph("未提供制造商授权书的，投标无效。")
    document.add_paragraph("投标文件逾期送达的，不予受理。")
    document.add_paragraph("投标文件应包括开标一览表、分项报价表、技术偏离表。")
    document.add_paragraph("投标文件应使用 A4 纸，正文宋体五号，页码连续编排。")
    table = document.add_table(rows=5, cols=3)
    for col, text in enumerate(("评审因素", "权重", "评分细则")):
        table.cell(0, col).text = text
    rows = (
        ("价格分", "30 分", "满足要求且报价最低"),
        ("技术参数响应", "50 分", "全部参数正偏离得满分"),
        ("售后服务方案", "20 分", "响应时限与备件保障"),
        ("合计", "100 分", "满分 100 分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="goods_table",
        filename="医疗设备采购询价文件.docx",
        content=_save(document),
        scoring_items=("价格分", "技术参数响应", "售后服务方案"),
        qualifications=("医疗器械经营许可证", "ISO 9001 质量管理体系认证", "制造商授权书"),
        reject_clauses=("未提供制造商授权书", "逾期送达"),
        budget_amount="1980000",
        dark_bid=False,
    )


def _transport_merged_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某市公交运输服务采购项目招标文件", level=1)
    document.add_paragraph("采购预算：人民币 450 万元。")
    document.add_paragraph("投标截止时间：2026年12月15日 09:00。")
    document.add_paragraph("投标人须具备道路运输经营许可证。")
    document.add_paragraph("近三年具有不少于 2 个类似运输服务业绩。")
    document.add_paragraph("未按要求提交投标保证金的，投标无效。")
    document.add_paragraph("投标文件应包括投标函、报价表、运输组织方案。")
    table = document.add_table(rows=5, cols=3)
    table.style = "Table Grid"
    table.cell(0, 0).merge(table.cell(0, 2)).text = "评标办法（综合评分法）"
    for col, text in enumerate(("评审因素", "分值", "评分标准")):
        table.cell(1, col).text = text
    rows = (
        ("运输组织方案", "50 分", "线路组织、时效与应急措施"),
        ("项目团队", "20 分", "驾驶员与调度人员配置"),
        ("投标报价", "30 分", "价格分统一计算"),
    )
    for row_index, row in enumerate(rows, start=2):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="transport_merged_header",
        filename="公交运输服务采购招标文件.docx",
        content=_save(document),
        scoring_items=("运输组织方案", "项目团队", "投标报价"),
        qualifications=("道路运输经营许可证", "不少于 2 个类似运输服务业绩"),
        reject_clauses=("未按要求提交投标保证金",),
        budget_amount="4500000",
        dark_bid=False,
    )


def _serial_numbered_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某数据中心设备采购项目评分表", level=1)
    document.add_paragraph("最高限价：人民币 320 万元。")
    document.add_paragraph("投标人须具备信息系统集成及服务资质。")
    document.add_paragraph("未通过资格审查的投标将被否决。")
    document.add_paragraph("投标截止时间为 2027年1月8日 10:00。")
    table = document.add_table(rows=4, cols=4)
    for col, text in enumerate(("序号", "评审内容", "分值", "说明")):
        table.cell(0, col).text = text
    rows = (
        ("1", "技术方案", "40 分", "架构完整、可落地"),
        ("2", "项目团队", "20 分", "人员配置合理"),
        ("3", "投标报价", "40 分", "价格分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="serial_numbered_table",
        filename="数据中心设备采购评分表.docx",
        content=_save(document),
        scoring_items=("技术方案", "项目团队", "投标报价"),
        qualifications=("信息系统集成及服务资质",),
        reject_clauses=("未通过资格审查",),
        budget_amount="3200000",
        dark_bid=False,
    )


def _qualification_table_case() -> GoldCase:
    document = _new_document()
    document.add_heading("资格审查表", level=1)
    document.add_paragraph("最高限价：人民币 500 万元。")
    document.add_paragraph("未通过资格审查的投标将被否决。")
    table = document.add_table(rows=4, cols=3)
    for col, text in enumerate(("序号", "审查内容", "审查标准")):
        table.cell(0, col).text = text
    rows = (
        ("1", "营业执照", "具备有效的营业执照副本"),
        ("2", "资质", "须具备涉密信息系统集成乙级及以上资质"),
        ("3", "业绩", "近三年具有不少于 2 个类似项目业绩"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="qualification_table",
        filename="资格审查表.docx",
        content=_save(document),
        scoring_items=(),
        qualifications=(
            "具备有效的营业执照",
            "涉密信息系统集成乙级",
            "不少于 2 个类似项目业绩",
        ),
        reject_clauses=("未通过资格审查",),
        budget_amount="5000000",
        dark_bid=False,
    )


def _dark_bid_phrasing_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某涉密系统采购项目招标文件", level=1)
    document.add_paragraph("最高限价：人民币 900 万元。")
    document.add_paragraph("技术方案中不得出现投标人名称。")
    document.add_paragraph("投标人须具备涉密信息系统集成甲级资质。")
    document.add_paragraph("未按要求密封的，投标无效。")
    table = document.add_table(rows=3, cols=3)
    for col, text in enumerate(("评分项", "分值", "评分标准")):
        table.cell(0, col).text = text
    for row_index, row in enumerate(
        (("技术方案", "60 分", "方案完整"), ("投标报价", "40 分", "价格分")), start=1
    ):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="dark_bid_phrasing",
        filename="涉密系统采购招标文件.docx",
        content=_save(document),
        scoring_items=("技术方案", "投标报价"),
        qualifications=("涉密信息系统集成甲级",),
        reject_clauses=("未按要求密封",),
        budget_amount="9000000",
        dark_bid=True,
    )



def _service_weight_case() -> GoldCase:
    document = _new_document()
    document.add_heading("智慧园区综合运营服务项目招标文件", level=1)
    document.add_paragraph("项目预算：人民币壹佰贰拾万元整（¥1,200,000.00）。")
    document.add_paragraph("投标人须具备有效的营业执照及物业服务一级资质。")
    document.add_paragraph("投标截止时间为 2027年2月10日 09:30。")
    document.add_paragraph("评分办法采用综合评分法，权重合计 100%。")
    table = document.add_table(rows=5, cols=3)
    for col, text in enumerate(("评审因素", "评审标准", "权重")):
        table.cell(0, col).text = text
    rows = (
        ("运营方案", "方案完整、人员配置合理", "30%"),
        ("信息化管理能力", "具备平台化运维能力", "25%"),
        ("类似项目业绩", "近三年不少于 3 个", "15%"),
        ("投标报价", "满足要求且价格最低", "30%"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="service_weight_percent",
        filename="智慧园区综合运营服务招标文件.docx",
        content=_save(document),
        scoring_items=("运营方案", "信息化管理能力", "类似项目业绩", "投标报价"),
        qualifications=("有效的营业执照", "物业服务一级资质"),
        reject_clauses=(),
        budget_amount="1200000",
        dark_bid=False,
    )


def _invalid_bid_list_case() -> GoldCase:
    document = _new_document()
    document.add_heading("医院信息化等级保护建设项目采购文件", level=1)
    document.add_paragraph("最高限价：人民币 800 万元。")
    document.add_paragraph("有下列情形之一的，视为无效投标：")
    document.add_paragraph("1. 未按要求提交投标保证金的；")
    document.add_paragraph("2. 投标文件未按招标文件要求签署盖章的；")
    document.add_paragraph("3. 投标人不具备本项目要求的资格条件的；")
    document.add_paragraph("4. 投标报价超过最高限价的。")
    document.add_paragraph("投标人须具备等级保护测评二级及以上资质。")
    table = document.add_table(rows=3, cols=3)
    for col, text in enumerate(("序号", "评分项", "分值")):
        table.cell(0, col).text = text
    rows = (
        ("1", "安全建设方案", "50 分"),
        ("2", "报价", "50 分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="invalid_bid_numbered_list",
        filename="医院信息化等级保护建设采购文件.docx",
        content=_save(document),
        scoring_items=("安全建设方案", "报价"),
        qualifications=("等级保护测评二级",),
        reject_clauses=(
            "未按要求提交投标保证金",
            "未按招标文件要求签署盖章",
            "不具备本项目要求的资格条件",
            "投标报价超过最高限价",
        ),
        budget_amount="8000000",
        dark_bid=False,
    )


def _two_table_appendix_case() -> GoldCase:
    document = _new_document()
    document.add_heading("高校实验室仪器设备采购项目招标文件", level=1)
    document.add_paragraph("预算金额 2600000 元。")
    document.add_paragraph("附表 1：资格审查条件")
    qa = document.add_table(rows=3, cols=2)
    for col, text in enumerate(("审查项", "要求")):
        qa.cell(0, col).text = text
    for row_index, row in enumerate(
        (("营业执照", "具有有效营业执照"), ("制造授权", "具备原厂授权书")), start=1
    ):
        for col, text in enumerate(row):
            qa.cell(row_index, col).text = text
    document.add_paragraph("附表 2：技术评分表")
    table = document.add_table(rows=4, cols=3)
    for col, text in enumerate(("序号", "评分内容", "得分")):
        table.cell(0, col).text = text
    rows = (
        ("1", "设备参数响应", "40 分"),
        ("2", "售后服务方案", "30 分"),
        ("3", "培训方案", "30 分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    document.add_paragraph("未通过资格审查的投标将被否决。")
    return GoldCase(
        name="two_table_appendix",
        filename="高校实验室仪器设备采购招标文件.docx",
        content=_save(document),
        scoring_items=("设备参数响应", "售后服务方案", "培训方案"),
        qualifications=("有效营业执照", "原厂授权书"),
        reject_clauses=("未通过资格审查",),
        budget_amount="2600000",
        dark_bid=False,
    )


def _dark_bid_with_range_case() -> GoldCase:
    document = _new_document()
    document.add_heading("涉密载体销毁服务采购项目（暗标）", level=1)
    document.add_paragraph("本项目技术标采用暗标方式编制，投标文件中不得出现投标人名称、印章或标识。")
    document.add_paragraph("预算金额：人民币 96 万元。")
    document.add_paragraph("投标人须具备国家秘密载体销毁资质。")
    document.add_paragraph("未按暗标格式编制的投标文件将被拒绝接收。")
    table = document.add_table(rows=4, cols=3)
    for col, text in enumerate(("序号", "评审点", "分值区间")):
        table.cell(0, col).text = text
    rows = (
        ("1", "销毁工艺方案", "20-40 分"),
        ("2", "保密管理措施", "10-30 分"),
        ("3", "应急响应能力", "10-20 分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    return GoldCase(
        name="dark_bid_score_range",
        filename="涉密载体销毁服务采购文件.docx",
        content=_save(document),
        scoring_items=("销毁工艺方案", "保密管理措施", "应急响应能力"),
        qualifications=("国家秘密载体销毁资质",),
        reject_clauses=("未按暗标格式编制",),
        budget_amount="960000",
        dark_bid=True,
    )



TXT_TENDER = """某区政务数据共享平台运维服务项目招标文件
项目预算：人民币 150 万元。
投标人须具备有效的营业执照及 CMMI3 级及以上资质。
投标人须具有不少于 2 个类似项目业绩。
评分采用综合评分法：
1. 技术方案：40 分
2. 运维保障能力：30 分
3. 投标报价：30 分
有下列情形之一的，视为无效投标：
1. 未按要求提交投标保证金的；
2. 投标报价超过预算金额的。
投标截止时间为 2027年4月12日 09:30。
本项目要求暗标评审，投标文件中不得出现投标人名称。
"""

MD_TENDER = """# 智慧校园网络改造采购项目评分办法

预算金额 4800000 元。

## 资格要求

- 投标人须具备有效的营业执照
- 须具备电子与智能化工程专业承包二级资质

## 评分表

| 序号 | 评分项 | 分值 |
| --- | --- | --- |
| 1 | 网络架构设计 | 35 分 |
| 2 | 设备选型与参数响应 | 35 分 |
| 3 | 报价 | 30 分 |

未通过资格性审查的投标将被否决。
"""


def _txt_case() -> GoldCase:
    return GoldCase(
        name="txt_plain_text",
        filename="政务数据共享平台运维招标文件.txt",
        content=TXT_TENDER.encode("utf-8"),
        scoring_items=("技术方案", "运维保障能力", "投标报价"),
        qualifications=("有效的营业执照", "CMMI3 级及以上资质", "不少于 2 个类似项目业绩"),
        reject_clauses=("未按要求提交投标保证金", "投标报价超过预算金额"),
        budget_amount="1500000",
        dark_bid=True,
    )


def _md_case() -> GoldCase:
    return GoldCase(
        name="md_markdown_table",
        filename="智慧校园网络采购评分办法.md",
        content=MD_TENDER.encode("utf-8"),
        scoring_items=("网络架构设计", "设备选型与参数响应", "报价"),
        qualifications=("有效的营业执照", "电子与智能化工程专业承包二级资质"),
        reject_clauses=("未通过资格性审查",),
        budget_amount="4800000",
        dark_bid=False,
    )


def _pdf_case() -> GoldCase:
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    page = canvas.Canvas(buffer, pagesize=A4)
    lines = [
        "某市医疗影像云备份服务采购文件",
        "最高限价：人民币贰佰捌拾万元整。",
        "投标人须具备有效的营业执照及医疗器械经营许可证。",
        "评分办法：",
        "数据迁移方案 40 分",
        "容灾与备份能力 35 分",
        "投标报价 25 分",
        "未按要求提供保证金的投标将被否决。",
        "投标截止时间为 2027年5月6日 15:00。",
    ]
    y = 780
    for line in lines:
        page.setFont("STSong-Light", 12)
        page.drawString(50, y, line)
        y -= 24
    page.showPage()
    page.save()
    return GoldCase(
        name="pdf_text_layer",
        filename="医疗影像云备份服务采购文件.pdf",
        content=buffer.getvalue(),
        scoring_items=("数据迁移方案", "容灾与备份能力", "投标报价"),
        qualifications=("有效的营业执照", "医疗器械经营许可证"),
        reject_clauses=("未按要求提供保证金",),
        budget_amount="2800000",
        dark_bid=False,
    )



def _pdf_table_bytes(elements) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4)
    document.build(elements)
    return buffer.getvalue()


def _pdf_style():
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import TableStyle

    with contextlib.suppress(Exception):  # 已注册则忽略
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )


def _pdf_par(text: str):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    style = ParagraphStyle("body", fontName="STSong-Light", fontSize=11, leading=18)
    return Paragraph(text, style)


def _pdf_table_scoring_case() -> GoldCase:
    from reportlab.platypus import Table

    table = Table(
        [
            ["评分项", "分值"],
            ["技术方案", "30"],
            ["运维服务体系", "25"],
            ["企业业绩", "20"],
            ["投标报价", "25"],
        ],
        colWidths=[240, 90],
    )
    table.setStyle(_pdf_style())
    content = _pdf_table_bytes(
        [
            _pdf_par("某省电子政务外网运维服务采购项目招标文件"),
            _pdf_par("预算金额：人民币 500 万元。"),
            _pdf_par("投标人须具备信息安全服务资质（二级及以上）。"),
            _pdf_par("投标文件逾期送达的，招标人有权拒绝接收。"),
            table,
        ]
    )
    return GoldCase(
        name="pdf_table_scoring",
        filename="电子政务外网运维服务采购.pdf",
        content=content,
        scoring_items=("技术方案", "运维服务体系", "企业业绩", "投标报价"),
        qualifications=("信息安全服务资质",),
        reject_clauses=("逾期送达",),
        budget_amount="5000000",
        dark_bid=False,
    )


def _pdf_table_merged_header_case() -> GoldCase:
    from reportlab.platypus import Table

    table = Table(
        [
            ["综合评分办法", ""],
            ["评分项", "分值"],
            ["数据迁移方案", "30"],
            ["商业信誉", "25"],
            ["投标报价", "45"],
        ],
        colWidths=[240, 90],
    )
    style = _pdf_style()

    style.add("SPAN", (0, 0), (1, 0))
    table.setStyle(style)
    content = _pdf_table_bytes(
        [
            _pdf_par("某市住房公积金系统迁移服务项目"),
            _pdf_par("最高限价：人民币 320 万元。"),
            _pdf_par("投标人须具备 CMMI3 级及以上认证。"),
            table,
        ]
    )
    return GoldCase(
        name="pdf_table_merged_header",
        filename="公积金系统迁移服务项目.pdf",
        content=content,
        scoring_items=("数据迁移方案", "商业信誉", "投标报价"),
        qualifications=("CMMI3",),
        reject_clauses=(),
        budget_amount="3200000",
        dark_bid=False,
    )


def _pdf_table_appendix_case() -> GoldCase:
    from reportlab.platypus import Table

    scoring = Table(
        [
            ["评分项", "分值"],
            ["数据治理能力", "35"],
            ["运维响应机制", "25"],
        ],
        colWidths=[240, 90],
    )
    scoring.setStyle(_pdf_style())
    price = Table(
        [
            ["项目", "金额（元）"],
            ["云主机租用", "120000"],
            ["存储扩容", "80000"],
        ],
        colWidths=[240, 120],
    )
    price.setStyle(_pdf_style())
    content = _pdf_table_bytes(
        [
            _pdf_par("某集团数据中台建设项目招标文件"),
            _pdf_par("预算金额：人民币 300 万元。"),
            _pdf_par("投标人须具备 ISO27001 信息安全管理体系认证。"),
            _pdf_par("串通投标的，其投标无效。"),
            scoring,
            _pdf_par("报价表："),
            price,
        ]
    )
    return GoldCase(
        name="pdf_table_appendix",
        filename="数据中台建设项目.pdf",
        content=content,
        scoring_items=("数据治理能力", "运维响应机制"),
        qualifications=("ISO27001",),
        reject_clauses=("串通投标",),
        budget_amount="3000000",
        dark_bid=False,
    )


def _multi_item_line_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某园区智能化改造项目招标文件", level=1)
    document.add_paragraph("预算金额：人民币 640 万元。")
    document.add_paragraph("计分标准：技术部分 40分；商务部分 30分；价格部分 30分。")
    document.add_paragraph("投标人须具备电子与智能化工程专业承包二级及以上资质。")
    return GoldCase(
        name="multi_item_line",
        filename="园区智能化改造项目.docx",
        content=_save(document),
        scoring_items=("技术部分", "商务部分", "价格部分"),
        qualifications=("电子与智能化工程专业承包二级",),
        reject_clauses=(),
        budget_amount="6400000",
        dark_bid=False,
    )


def _factor_alias_table_case() -> GoldCase:
    document = _new_document()
    document.add_heading("某银行灾备中心运维服务项目", level=1)
    document.add_paragraph("预算金额：人民币 210 万元。")
    table = document.add_table(rows=4, cols=2)
    table.style = "Table Grid"
    cells = [
        ("评分因素", "分值"),
        ("应急演练方案", "30"),
        ("驻场服务团队", "30"),
        ("投标报价", "40"),
    ]
    for row, (left, right) in enumerate(cells):
        table.cell(row, 0).text = left
        table.cell(row, 1).text = right
    document.add_paragraph("投标人须具备 ISO20000 IT 服务管理体系认证。")
    return GoldCase(
        name="factor_alias_table",
        filename="银行灾备中心运维服务.docx",
        content=_save(document),
        scoring_items=("应急演练方案", "驻场服务团队", "投标报价"),
        qualifications=("ISO20000",),
        reject_clauses=(),
        budget_amount="2100000",
        dark_bid=False,
    )


def build_gold_cases() -> list[GoldCase]:
    return [
        _gov_it_case(),
        _engineering_case(),
        _goods_case(),
        _transport_merged_case(),
        _serial_numbered_case(),
        _qualification_table_case(),
        _dark_bid_phrasing_case(),
        _service_weight_case(),
        _invalid_bid_list_case(),
        _two_table_appendix_case(),
        _dark_bid_with_range_case(),
        _txt_case(),
        _md_case(),
        _pdf_case(),
        _pdf_table_scoring_case(),
        _pdf_table_merged_header_case(),
        _pdf_table_appendix_case(),
        _multi_item_line_case(),
        _factor_alias_table_case(),
    ]


def evaluate_gold_corpus(cases: list[GoldCase] | None = None) -> list[GoldResult]:
    results: list[GoldResult] = []
    for case in cases if cases is not None else build_gold_cases():
        expected = build_constraint_set(
            parse_document(case.content, case.filename), {"name": case.name}
        )
        results.append(
            GoldResult(
                case=case.name,
                scoring=_match_scoring(case.scoring_items, expected["scoring_items"]),
                qualification=_match_clauses(
                    case.qualifications, expected["qualification_requirements"], "text"
                ),
                reject=_match_clauses(case.reject_clauses, expected["reject_clauses"], "text"),
                budget_ok=(
                    case.budget_amount is None
                    or (
                        expected["budget"]["amount"] is not None
                        and Decimal(expected["budget"]["amount"]) == Decimal(case.budget_amount)
                    )
                ),
                dark_bid_ok=expected["dark_bid"]["required"] == case.dark_bid,
            )
        )
    return results
