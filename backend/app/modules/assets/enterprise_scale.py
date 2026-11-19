"""中小企业划型：按《中小企业划型标准规定》（工信部联企业〔2011〕300 号）判定。

为什么必须按标准算，而不是让企业自己填一个「小型」：

- 政府采购「专门面向中小企业」的项目，**声明函填错即废标**，且属于提供虚假材料，
  可能被列入不良行为记录；
- 划型看的是「行业 + 从业人员 + 营业收入 + 资产总额」四要素，不同行业阈值差别极大：
  软件业 300 人算中型，物业 300 人才刚进中型，批发业 20 人就够中型；
- 很多企业按「感觉自己不大」自认小微，一核对就超了。

本模块只做**标准换算**，输入行业与规模指标，输出划型结果和依据。
指标缺失时返回 unknown 并说明缺哪个——不猜，因为猜错的代价是废标。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, NamedTuple

SCALES = ("大型", "中型", "小型", "微型")


class IndustrySpec(NamedTuple):
    """单个行业的划型阈值。

    `use_assets` 为 True 时用资产总额替代营业收入（建筑业、房地产、租赁商务服务）。
    `max_*` 是「中小微上限」，超过即大型；`mid_*`/`small_*` 是逐级下探的下限。
    """

    key: str
    label: str
    max_employees: int | None = None
    max_revenue: Decimal | None = None
    max_assets: Decimal | None = None
    mid_employees: int | None = None
    mid_revenue: Decimal | None = None
    mid_assets: Decimal | None = None
    small_employees: int | None = None
    small_revenue: Decimal | None = None
    small_assets: Decimal | None = None
    use_assets: bool = False


def _w(value: str) -> Decimal:
    """万元。"""
    return Decimal(value)


# 单位：营业收入/资产总额均为万元，人员为人
INDUSTRIES: dict[str, IndustrySpec] = {
    "agriculture": IndustrySpec(
        "agriculture", "农、林、牧、渔业",
        max_revenue=_w("20000"), mid_revenue=_w("500"), small_revenue=_w("50"),
    ),
    "industry": IndustrySpec(
        "industry", "工业",
        max_employees=1000, max_revenue=_w("40000"),
        mid_employees=300, mid_revenue=_w("2000"),
        small_employees=20, small_revenue=_w("300"),
    ),
    "construction": IndustrySpec(
        "construction", "建筑业",
        max_revenue=_w("80000"), max_assets=_w("80000"),
        mid_revenue=_w("6000"), mid_assets=_w("5000"),
        small_revenue=_w("300"), small_assets=_w("300"),
        use_assets=True,
    ),
    "wholesale": IndustrySpec(
        "wholesale", "批发业",
        max_employees=200, max_revenue=_w("40000"),
        mid_employees=20, mid_revenue=_w("5000"),
        small_employees=5, small_revenue=_w("1000"),
    ),
    "retail": IndustrySpec(
        "retail", "零售业",
        max_employees=300, max_revenue=_w("20000"),
        mid_employees=50, mid_revenue=_w("500"),
        small_employees=10, small_revenue=_w("100"),
    ),
    "transport": IndustrySpec(
        "transport", "交通运输业",
        max_employees=1000, max_revenue=_w("30000"),
        mid_employees=300, mid_revenue=_w("3000"),
        small_employees=20, small_revenue=_w("200"),
    ),
    "warehouse": IndustrySpec(
        "warehouse", "仓储业",
        max_employees=200, max_revenue=_w("30000"),
        mid_employees=100, mid_revenue=_w("1000"),
        small_employees=20, small_revenue=_w("100"),
    ),
    "post": IndustrySpec(
        "post", "邮政业",
        max_employees=1000, max_revenue=_w("30000"),
        mid_employees=300, mid_revenue=_w("2000"),
        small_employees=20, small_revenue=_w("100"),
    ),
    "hotel": IndustrySpec(
        "hotel", "住宿业",
        max_employees=300, max_revenue=_w("10000"),
        mid_employees=100, mid_revenue=_w("2000"),
        small_employees=10, small_revenue=_w("100"),
    ),
    "catering": IndustrySpec(
        "catering", "餐饮业",
        max_employees=300, max_revenue=_w("10000"),
        mid_employees=100, mid_revenue=_w("2000"),
        small_employees=10, small_revenue=_w("100"),
    ),
    "telecom": IndustrySpec(
        "telecom", "信息传输业",
        max_employees=2000, max_revenue=_w("100000"),
        mid_employees=100, mid_revenue=_w("1000"),
        small_employees=10, small_revenue=_w("100"),
    ),
    "software": IndustrySpec(
        "software", "软件和信息技术服务业",
        max_employees=300, max_revenue=_w("10000"),
        mid_employees=100, mid_revenue=_w("1000"),
        small_employees=10, small_revenue=_w("50"),
    ),
    "real_estate": IndustrySpec(
        "real_estate", "房地产开发经营",
        max_revenue=_w("200000"), max_assets=_w("10000"),
        mid_revenue=_w("1000"), mid_assets=_w("5000"),
        small_revenue=_w("100"), small_assets=_w("2000"),
        use_assets=True,
    ),
    "property": IndustrySpec(
        "property", "物业管理",
        max_employees=1000, max_revenue=_w("5000"),
        mid_employees=300, mid_revenue=_w("1000"),
        small_employees=100, small_revenue=_w("500"),
    ),
    "leasing": IndustrySpec(
        "leasing", "租赁和商务服务业",
        max_employees=300, max_assets=_w("120000"),
        mid_employees=100, mid_assets=_w("8000"),
        small_employees=10, small_assets=_w("100"),
        use_assets=True,
    ),
    "other": IndustrySpec(
        "other", "其他未列明行业",
        max_employees=300, mid_employees=100, small_employees=10,
    ),
}

# 公告/企业档案里的行业写法 → 标准行业；顺序敏感，先匹配更具体的长词
INDUSTRY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("software", ("软件", "信息技术服务", "信息系统", "软件开发", "系统集成", "运维服务",
                  "信息技术", "IT服务", "数字化", "数据服务", "人工智能")),
    ("telecom", ("信息传输", "电信", "广播电视传输", "通信", "运营商")),
    ("construction", ("建筑", "施工", "市政", "装修", "安装工程", "工程建设", "园林",
                      "水利工程", "公路工程")),
    ("real_estate", ("房地产开发",)),
    ("property", ("物业管理", "物业")),
    ("leasing", ("租赁", "商务服务", "咨询", "招标代理", "代理服务", "人力资源",
                 "会计", "律师", "设计服务")),
    ("wholesale", ("批发",)),
    ("retail", ("零售", "商贸", "销售")),
    ("transport", ("交通运输", "运输", "道路运输", "物流")),
    ("warehouse", ("仓储",)),
    ("post", ("邮政", "快递")),
    ("hotel", ("住宿",)),
    ("catering", ("餐饮",)),
    ("agriculture", ("农业", "林业", "牧业", "渔业", "种植", "养殖")),
    ("industry", ("工业", "制造", "生产", "加工")),
)


def resolve_industry(text: str | None) -> str | None:
    """把行业名或标准 key 映射到标准行业 key；无法判定返回 None。

    接受三种输入，前端下拉框传的是 key，早期只做中文别名匹配会导致
    「传了 software 却说无法判定行业」——这是端到端跑出来的真实 bug。
    """
    if not text:
        return None
    candidate = text.strip()
    # 1) 已经是标准 key
    if candidate in INDUSTRIES:
        return candidate
    # 2) 标准中文行业名（如「软件和信息技术服务业」）
    for key, spec in INDUSTRIES.items():
        if candidate == spec.label:
            return key
    # 3) 自由文本别名
    for key, aliases in INDUSTRY_ALIASES:
        for alias in aliases:
            if alias in candidate:
                return key
    return None


class ScaleResult(NamedTuple):
    scale: str | None            # 大型/中型/小型/微型；无法判定为 None
    industry: str | None         # 标准行业 key
    industry_label: str | None
    basis: str                   # 判定依据（含命中阈值）
    missing: list[str]           # 缺失的判定要素


def classify_enterprise(
    *,
    industry: str | None,
    employees: int | None = None,
    revenue: Decimal | None = None,
    total_assets: Decimal | None = None,
) -> ScaleResult:
    """按工信部联企业〔2011〕300 号判定企业规模。

    指标单位：employees 人，revenue / total_assets 万元。
    缺关键指标时返回 scale=None 并列出缺什么，绝不猜测。
    """
    key = resolve_industry(industry)
    if key is None:
        return ScaleResult(None, None, None, "无法从「%s」判定标准行业" % (industry or "空"), ["行业"])

    spec = INDUSTRIES[key]
    missing: list[str] = []
    needed_secondary = "资产总额" if spec.use_assets else "营业收入"
    secondary_value = total_assets if spec.use_assets else revenue
    if secondary_value is None:
        missing.append(needed_secondary)
    if spec.max_employees is not None and employees is None:
        missing.append("从业人员")
    if missing:
        return ScaleResult(
            None, key, spec.label,
            f"{spec.label}：缺少{'、'.join(missing)}，无法判定",
            missing,
        )

    # 1) 先判是否达到「大型」：标准的写法是各项指标**同时**达到大型下限
    def at_or_above(threshold: Any, value: Any) -> bool:
        """该维度不参与判定（threshold 为 None）时视为满足。"""
        if threshold is None:
            return True
        return value is not None and value >= threshold

    if (
        at_or_above(spec.max_employees, employees)
        and at_or_above(spec.max_revenue, revenue)
        and at_or_above(spec.max_assets, total_assets)
        and (spec.max_employees is not None or spec.max_revenue is not None or spec.max_assets is not None)
    ):
        return ScaleResult(
            "大型", key, spec.label,
            f"{spec.label}：从业人员 {employees}、{needed_secondary} {secondary_value} 万元，"
            f"达到或超过大型下限",
            [],
        )

    # 2) 中型：各项指标同时达到中型下限
    has_mid = (
        spec.mid_employees is not None or spec.mid_revenue is not None or spec.mid_assets is not None
    )
    if has_mid and (
        _meets(spec.mid_employees, employees)
        and _meets(spec.mid_revenue if not spec.use_assets else None, revenue)
        and _meets(spec.mid_assets if spec.use_assets else None, total_assets)
    ):
        return ScaleResult(
            "中型", key, spec.label,
            f"{spec.label}：从业人员 {employees or '—'}、{needed_secondary} {secondary_value} 万元，"
            f"达到中型下限",
            [],
        )

    # 3) 小型
    if (
        _meets(spec.small_employees, employees)
        and _meets(spec.small_revenue if not spec.use_assets else None, revenue)
        and _meets(spec.small_assets if spec.use_assets else None, total_assets)
    ):
        return ScaleResult(
            "小型", key, spec.label,
            f"{spec.label}：从业人员 {employees or '—'}、{needed_secondary} {secondary_value} 万元，"
            f"达到小型下限",
            [],
        )

    return ScaleResult(
        "微型", key, spec.label,
        f"{spec.label}：从业人员 {employees or '—'}、{needed_secondary} {secondary_value} 万元，"
        f"未达小型下限",
        [],
    )


def _meets(threshold: int | Decimal | None, value: Any) -> bool:
    """阈值缺失视为「该维度不参与判定」。"""
    if threshold is None:
        return True
    if value is None:
        return False
    return value >= threshold


def is_sme(scale: str | None) -> bool | None:
    """中型/小型/微型属于中小企业；大型不属于；未知返回 None。"""
    if scale is None:
        return None
    return scale in {"中型", "小型", "微型"}


def parse_employees(text: str | None) -> int | None:
    """从「120人」「从业人员120人」「120 名员工」这类写法取人数。"""
    if not text:
        return None
    match = re.search(r"(\d{1,6})\s*(?:人|名)", text)
    if match:
        return int(match.group(1))
    if text.strip().isdigit():
        return int(text.strip())
    return None
