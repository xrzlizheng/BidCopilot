"""投标风险画像：把公告里「看着像要求、实际是废标点」的条款单独抽出来。

这些不是泛泛的资质罗列，而是招投标实操里最常踩、最容易被忽略的几类：

1. **业绩门槛**：近 3 年 / 不少于 2 个 / 单个合同≥XX 万——最容易「差一点点」被废；
2. **联合体限制**：不接受联合体时，资质不全就无法靠联合体补齐；
3. **分支机构**：分公司能否用总公司资质，各地口径差别很大，必须显式标注；
4. **中小企业声明函**：专门面向中小企业的项目，**声明函缺失即废标**，而非扣分；
5. **本地化要求**：本地注册/本地社保/本地服务机构——隐性排他条款，需评估能否满足；
6. **原件核验**：开标需带原件，缺一即废；
7. **同一集团/关联企业**：多家关联公司同时投标会被认定串通；
8. **信用查询截图**：漏站点/漏查询板块/查询时间不符/未盖章，可能按未实质性响应废标。

设计原则：**只抽取原文并标注风险等级，不做合规判断**。是否满足、能否规避属于投标决策，
必须由人确认——系统给出错误结论比不给结论危险得多。
"""

from __future__ import annotations

import re
from typing import Any

# 风险等级：blocker=不满足即废标；high=极可能废标或大幅扣分；medium=需评估
RISK_LEVELS = ("blocker", "high", "medium")


class RiskRule:
    __slots__ = ("code", "name", "level", "pattern", "hint")

    def __init__(
        self, code: str, name: str, level: str, pattern: re.Pattern[str], hint: str
    ) -> None:
        self.code = code
        self.name = name
        self.level = level
        self.pattern = pattern
        self.hint = hint


# 业绩门槛：「近三年具有不少于 2 个类似项目业绩」——数量与年限同时约束，最常出错
PERFORMANCE_MIN = re.compile(
    r"(?:近|最近|自)[^。；]{0,8}?(?P<years>[一二三四五1-5])\s*年[^。；]{0,40}?"
    r"(?:不少于|至少|不低于|达)\s*(?P<count>[一二三四五六七八九十\d]+)\s*(?:个|项|份)"
)
PERFORMANCE_AMOUNT = re.compile(
    r"(?:单个|单项|每)[^。；]{0,10}?合同[^。；]{0,12}?"
    r"(?:金额|价款)?[^。；]{0,6}?(?:不低于|不少于|达到|≥)\s*(?P<amount>\d[\d,.]*)\s*(?P<unit>万元|元)"
)

CONSORTIUM_REJECT = re.compile(r"不(?:接受|允许|得)[^。；]{0,10}联合体")
CONSORTIUM_ALLOW = re.compile(r"(?:接受|允许)[^。；]{0,10}联合体")

# 分公司规则要收紧：真实文件里「分公司」常出现在证照清单（「分公司营业执照复印件」），
# 那不是限制条款。要求句子里同时出现「资质/资格」与「使用/沿用/认可」这类语义。
BRANCH_CLAUSE = re.compile(
    r"(?:分公司|分支机构)[^。；]{0,60}?(?:使用|沿用|借用|认可|承继|具备)[^。；]{0,20}?"
    r"(?:资质|资格|业绩)"
    r"|(?:资质|资格|业绩)[^。；]{0,40}?(?:分公司|分支机构)[^。；]{0,20}?(?:使用|沿用|认可)"
)
BRANCH_ALLOW = re.compile(r"分公司[^。；]{0,20}?(?:可以|可|允许)[^。；]{0,20}?(?:使用|借用|沿用)")

SME_DECLARATION = re.compile(r"中小企业声明函")
SME_ONLY = re.compile(r"(?:专门|仅|只)[^。；]{0,6}面向[^。；]{0,8}中小企业")

LOCAL_REGISTER = re.compile(
    r"(?:本地|本市|本省|辖区内|注册地)[^。；]{0,12}?"
    r"(?:注册|登记|纳税|社保|缴纳|设有|设立|分支机构|服务机构)"
)
ORIGINAL_DOC = re.compile(r"(?:携带|提供|出示|核验|提交)[^。；]{0,10}?原件")
AFFILIATE_LIMIT = re.compile(r"(?:同一|关联|母公司|控股)[^。；]{0,14}?(?:不得|禁止)[^。；]{0,14}?(?:同时|共同)?(?:投标|参与)")
AGENCY_REJECT = re.compile(r"(?:为本项目|本项目)[^。；]{0,16}?(?:提供|承担)[^。；]{0,10}?(?:设计|咨询|监理|代理)[^。；]{0,20}?(?:不得|禁止)")

# 信用查询截图是形式审查高频废标点：信用干净不代表「证明过程」合规。
# 真实招标文件常把查询网站、查询板块、查询时间、盖章位置拆成多条硬性要求；
# 这里只识别「需要提交信用查询证据」，具体缺哪一项仍必须人工对照原文。
CREDIT_QUERY_EVIDENCE = re.compile(
    r'(?:信用中国|中国政府采购网|国家企业信用信息公示系统)'
    r'[^。；]{0,120}?(?:截图|查询记录|查询结果|网页打印|打印件)'
    r'|(?:截图|查询记录|查询结果|网页打印|打印件)'
    r'[^。；]{0,120}?(?:信用中国|中国政府采购网|国家企业信用信息公示系统)'
    r'|信用(?:信息|记录)?查询[^。；]{0,60}?(?:截图|查询记录|查询结果|网页打印|打印件)'
)

RULES: tuple[RiskRule, ...] = (
    RiskRule(
        "performance_threshold",
        "业绩门槛（年限+数量）",
        "blocker",
        PERFORMANCE_MIN,
        "业绩不满足即废标；注意「近三年」通常按合同签订或验收时间认定，各地口径不同",
    ),
    RiskRule(
        "performance_amount",
        "业绩合同金额门槛",
        "high",
        PERFORMANCE_AMOUNT,
        "单个合同金额不达标不计入有效业绩，需核对合同原件金额",
    ),
    RiskRule(
        "consortium_rejected",
        "不接受联合体投标",
        "blocker",
        CONSORTIUM_REJECT,
        "资质不全时无法通过联合体补齐，需独立满足全部资格条件",
    ),
    RiskRule(
        "branch_qualification",
        "分公司资质使用限制",
        "high",
        BRANCH_CLAUSE,
        "分公司能否使用总公司资质各地口径不同，无明确允许时按不可用准备",
    ),
    RiskRule(
        "sme_declaration",
        "中小企业声明函",
        "blocker",
        SME_DECLARATION,
        "专门面向中小企业的项目，声明函缺失或填写不符即废标（不是扣分）",
    ),
    RiskRule(
        "sme_only_scope",
        "专门面向中小企业",
        "high",
        SME_ONLY,
        "非中小企业不得参与，需先确认企业规模划型（按行业标准，非自认定）",
    ),
    RiskRule(
        "local_requirement",
        "本地化要求",
        "high",
        LOCAL_REGISTER,
        "隐性排他条款，需评估能否在投标截止前满足（注册/社保/机构设立均有周期）",
    ),
    RiskRule(
        "original_document",
        "原件核验",
        "high",
        ORIGINAL_DOC,
        "开标现场需携带原件，缺一即废；需提前准备并在清单中列为必带",
    ),
    RiskRule(
        "affiliate_bidding",
        "关联企业同时投标限制",
        "blocker",
        AFFILIATE_LIMIT,
        "同一集团多家公司同时投标可能被认定串通，需内部排查关联关系",
    ),
    RiskRule(
        "agency_conflict",
        "前期服务方回避",
        "blocker",
        AGENCY_REJECT,
        "参与过本项目设计/咨询/监理的单位及其关联方不得投标",
    ),
    RiskRule(
        "credit_query_evidence",
        "信用查询截图（形式审查）",
        "blocker",
        CREDIT_QUERY_EVIDENCE,
        "信用查询截图/查询记录缺页、查询板块不全、查询时间不符或未盖章，可能按未实质性响应废标；按招标文件逐项核对查询网站、板块、时间与盖章位置",
    ),
)

_CN_COUNT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_int(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return _CN_COUNT.get(value)


def _span(block: Any) -> dict[str, Any]:
    return {
        "block_index": getattr(block, "index", None),
        "table_index": getattr(block, "table_index", None),
        "row": getattr(block, "row", None),
        "col": getattr(block, "col", None),
        "kind": getattr(block, "kind", None),
        "quote": (getattr(block, "text", "") or "")[:200],
    }


def extract_bid_risks(blocks: list[Any]) -> dict[str, Any]:
    """抽取投标风险点，返回 {items: [...], summary: {...}, flags: {...}}。

    同一 block 命中同一规则只记一次；同一规则在多处出现则分别记录，
    因为不同章节的要求可能不一致（正文 vs 前附表），这种矛盾本身就该被看到。
    """
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # 同一规则在多处出现（正文 + 前附表 + 编制说明）时只保留信息量最大的一条，
    # 否则「专门面向中小企业」会在真实文件里重复 6 次，把真正的门槛淹没。
    best_by_code: dict[str, dict[str, Any]] = {}

    for block in blocks:
        text = (getattr(block, "text", "") or "").strip()
        if not text:
            continue
        for rule in RULES:
            match = rule.pattern.search(text)
            if match is None:
                continue
            key = (rule.code, text[:80])
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {
                "code": rule.code,
                "name": rule.name,
                "level": rule.level,
                "hint": rule.hint,
                "quote": text[:200],
                "source_span": _span(block),
            }
            if rule.code == "performance_threshold":
                years = _to_int(match.group("years"))
                count = _to_int(match.group("count"))
                entry["years"] = years
                entry["min_count"] = count
            elif rule.code == "performance_amount":
                raw = match.group("amount").replace(",", "")
                try:
                    amount = float(raw)
                except ValueError:
                    amount = None
                if amount is not None and match.group("unit") == "万元":
                    amount *= 10000
                entry["min_amount"] = amount
            items.append(entry)
            current = best_by_code.get(rule.code)
            if current is None or len(entry["quote"]) > len(current["quote"]):
                best_by_code[rule.code] = entry

    # 去重：规则层面只留一条最具代表性的，原始命中数单独记录便于审计
    raw_hits = len(items)
    items = sorted(best_by_code.values(), key=lambda item: RISK_LEVELS.index(item["level"]))

    flags = {
        "consortium_allowed": any(
            CONSORTIUM_ALLOW.search(getattr(block, "text", "") or "") for block in blocks
        )
        and not any(item["code"] == "consortium_rejected" for item in items),
        "branch_allowed": any(
            BRANCH_ALLOW.search(getattr(block, "text", "") or "") for block in blocks
        ),
        "sme_only": any(item["code"] == "sme_only_scope" for item in items),
        "original_required": any(item["code"] == "original_document" for item in items),
        "credit_query_required": any(
            item["code"] == "credit_query_evidence" for item in items
        ),
    }

    counts = {"blocker": 0, "high": 0, "medium": 0}
    for item in items:
        counts[item["level"]] = counts.get(item["level"], 0) + 1

    return {
        "items": items,
        "flags": flags,
        "must_bring": derive_must_bring(items, flags),
        "summary": {
            "total": len(items),
            "raw_hits": raw_hits,
            "blocker": counts["blocker"],
            "high": counts["high"],
            "medium": counts["medium"],
            "verdict": "需逐项确认" if items else "未识别到硬性门槛",
        },
    }


# 风险点 -> 开标必带材料。这张表是「从风险到动作」的桥：
# 用户看到「中小企业声明函（缺即废）」还不够，得知道开标那天要带什么。
_MUST_BRING: dict[str, dict[str, Any]] = {
    "sme_declaration": {
        "item": "《中小企业声明函》原件（按第六章格式，加盖公章）",
        "why": "本项目专门面向中小企业，缺失或与实际不符即废标",
        "critical": True,
    },
    "sme_only_scope": {
        "item": "企业规模证明材料（从业人员、营业收入或资产总额口径）",
        "why": "需能自证属于中小企业，否则声明函可能被认定不实",
        "critical": True,
    },
    "performance_threshold": {
        "item": "业绩合同 + 中标通知书 + 验收证明（按公告要求的年限与数量）",
        "why": "业绩不满足即废标；三者缺一常被判无效业绩",
        "critical": True,
    },
    "performance_amount": {
        "item": "业绩合同原件（需能体现单个合同金额）",
        "why": "合同金额达不到门槛不计入有效业绩",
        "critical": False,
    },
    "original_document": {
        "item": "营业执照、资质证书等原件（按公告列明范围）",
        "why": "开标现场核验原件，缺一即废",
        "critical": True,
    },
    "credit_query_evidence": {
        "item": "信用中国、中国政府采购网、国家企业信用信息公示系统查询截图（加盖公章，查询日期符合要求）",
        "why": "漏站点/漏板块/日期不符/未盖章，常按未实质性响应废标",
        "critical": True,
    },
    "affiliate_bidding": {
        "item": "关联企业投标情况说明（同一集团不得同时投标）",
        "why": "多家关联公司同时投标可能被认定串通",
        "critical": False,
    },
    "agency_conflict": {
        "item": "无前期服务关系承诺函",
        "why": "参与过本项目设计/咨询/监理的单位不得投标",
        "critical": False,
    },
    "branch_qualification": {
        "item": "总公司授权书 + 资质证明（如以分公司名义投标）",
        "why": "分公司能否使用总公司资质各地口径不同，需书面授权",
        "critical": False,
    },
    "local_requirement": {
        "item": "本地注册/社保缴纳/服务机构设立证明",
        "why": "本地化要求属排他条款，无证明可能被判不响应",
        "critical": False,
    },
}


def derive_must_bring(
    items: list[dict[str, Any]], flags: dict[str, Any]
) -> list[dict[str, Any]]:
    """把风险点派生成开标必带材料清单。

    只对**已识别的风险**生成条目——没识别的不能凭空造，否则用户会以为
    清单是完整的。清单末尾固定附一句「以公告原文为准」，避免被当成权威依据。
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        code = item.get("code")
        # code 来自解析结果，理论上恒为 str；显式收窄同时防止不可哈希值让 dict.get 抛错
        if not isinstance(code, str):
            continue
        spec = _MUST_BRING.get(code)
        if spec is None or code in seen:
            continue
        seen.add(code)
        result.append(
            {
                "code": code,
                "item": spec["item"],
                "why": spec["why"],
                "critical": spec["critical"],
                "source": item.get("name"),
            }
        )
    # 原件核验有时只在证照清单里体现，风险规则未必命中，用 flag 兜底
    if flags.get("original_required") and "original_document" not in seen:
        spec = _MUST_BRING["original_document"]
        result.append(
            {
                "code": "original_document",
                "item": spec["item"],
                "why": spec["why"],
                "critical": True,
                "source": "原件核验",
            }
        )
    # 关键项排前面，避免被非关键项淹没
    result.sort(key=lambda row: (not row["critical"], row["code"]))
    return result
