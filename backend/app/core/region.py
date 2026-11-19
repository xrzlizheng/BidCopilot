"""地区补全与规范化（基于民政部全国行政区划数据）。

数据来源：民政部全国行政区划信息查询平台（xzqh.mca.gov.cn）内嵌的官方区划表，
存于 `app/core/data/cn_divisions.json`：34 省级 / 333 地级 / 2844 县级，共 3211 条，
带 6 位国家标准行政区划代码。数据只读、随镜像发布，运行时零外部依赖。

为什么不用 JioNLP：
- JioNLP 的 `parse_location` 在「河北石家庄长安区」这类无后缀写法上确实更强，
  但会引入 jiojio 的 81.6MB 模型；而本项目真正需要的是「**能不能确定**」而非
  「猜得更像」，官方数据能给出唯一性判定，这一点比模糊匹配更重要。
- 补错地区的代价远高于留空：用户按错误省份筛商机、按错误地区判断属地业绩要求，
  都会直接做错决策。所以这里的原则是「有疑不补」。

三条硬规则：
1. 区县名在全国唯一的（2778/2807）才能反推上级；跨省市重名的 29 个一律不补；
2. 泛称（城区/开发区/新区…）不补；
3. 已有地区做规范化：修重复、补省级、直辖市按「省=市」处理；无法确定时保留原值。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

DATA_FILE = Path(__file__).with_name("data") / "cn_divisions.json"

# 泛称：多省重名或根本不是行政区的词，命中即拒绝补全
AMBIGUOUS_TOKENS = (
    "城区",
    "开发区",
    "高新区",
    "新区",
    "保税区",
    "工业园",
    "产业园",
    "经济技术开发区",
    "经济开发区",
    "示范区",
    "试验区",
    "保税港区",
    "口岸",
)

# 直辖市：省=市同名，区县直接挂在市下
MUNICIPALITIES = frozenset({"北京市", "天津市", "上海市", "重庆市"})


class RegionParts(NamedTuple):
    province: str | None
    city: str | None
    county: str | None

    def joined(self) -> str | None:
        parts = [part for part in (self.province, self.city, self.county) if part]
        return " ".join(parts) if parts else None


@lru_cache(maxsize=1)
def _index() -> dict[str, object]:
    """一次性构建索引；模块级缓存，进程内只解析一次 100KB JSON。"""
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    provinces: dict[str, str] = {}       # 2 位码 -> 省名
    cities: dict[str, str] = {}          # 4 位码 -> 市名
    city_province: dict[str, str] = {}   # 市名 -> 省名
    county_owner: dict[str, set[tuple[str, str]]] = {}  # 县名 -> {(省, 市)}
    province_names: set[str] = set()
    city_names: set[str] = set()
    county_names: set[str] = set()

    for item in raw:
        code, name = item["c"], item["n"]
        if code.endswith("0000"):
            provinces[code[:2]] = name
            province_names.add(name)
        elif code.endswith("00"):
            cities[code[:4]] = name
            city_names.add(name)
            city_province[name] = provinces.get(code[:2], "")
        else:
            county_names.add(name)
            owner = (provinces.get(code[:2], ""), cities.get(code[:4], ""))
            county_owner.setdefault(name, set()).add(owner)

    return {
        "provinces": provinces,
        "cities": cities,
        "city_province": city_province,
        "county_owner": county_owner,
        "province_names": province_names,
        "city_names": city_names,
        "county_names": county_names,
    }


def _province_names() -> set[str]:
    return _index()["province_names"]  # type: ignore[return-value]


def _city_names() -> set[str]:
    return _index()["city_names"]  # type: ignore[return-value]


def _county_names() -> set[str]:
    return _index()["county_names"]  # type: ignore[return-value]


def _lookup_province(name: str) -> str | None:
    """省名精确匹配；支持「安徽」「内蒙古」这类简称。"""
    names = _province_names()
    if name in names:
        return name
    for full in names:
        stem = re.sub(r"(省|市|自治区|特别行政区|壮族|回族|维吾尔)", "", full)
        if name == stem:
            return full
    return None


def _lookup_city(name: str) -> str | None:
    names = _city_names()
    if name in names:
        return name
    if not name.endswith("市") and f"{name}市" in names:
        return f"{name}市"
    return None


def _county_owners(name: str) -> set[tuple[str, str]]:
    # 索引里存的是 dict[str, set[tuple[str, str]]]；`_index()` 返回 object 故需标注。
    county_owner: dict[str, set[tuple[str, str]]] = _index()["county_owner"]  # type: ignore[assignment]
    return county_owner.get(name) or set()


def _lookup_county(
    name: str, *, province: str | None = None, city: str | None = None
) -> tuple[str | None, str | None] | None:
    """县级名反查 (省, 市)。

    全国唯一时直接返回；跨省市重名（如「朝阳区」在北京/长春）**只在已知上级
    能唯一消歧时**才返回——上级已知还拒绝，会让「北京市朝阳区」丢掉区县。
    """
    owners = _county_owners(name)
    if not owners:
        return None
    if len(owners) == 1:
        found_province, found_city = next(iter(owners))
        return found_province or None, found_city or None
    if city:
        narrowed = {item for item in owners if item[1] == city}
        if len(narrowed) == 1:
            found_province, found_city = next(iter(narrowed))
            return found_province or None, found_city or None
    if province:
        narrowed = {item for item in owners if item[0] == province}
        # 同一省内仍可能重名（石家庄新华区 / 沧州新华区），要求只剩一个
        if len(narrowed) == 1:
            found_province, found_city = next(iter(narrowed))
            return found_province or None, found_city or None
    return None


@lru_cache(maxsize=1)
def _city_stems() -> list[tuple[str, str]]:
    """地级市简称 -> 全名，按长度倒序保证「张家口」优先于「张家」。

    只收 2 字以上的简称；「中山」「东城」这类既可能是市名又是路名/区名的
    已通过 AMBIGUOUS_CITY_STEMS 排除，避免把路名当城市。
    """
    pairs: list[tuple[str, str]] = []
    for name in _city_names():
        stem = name[:-1] if name.endswith("市") else name
        if len(stem) < 2:
            continue
        pairs.append((stem, name))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


AMBIGUOUS_CITY_STEMS = frozenset(
    {"中山", "东城", "西城", "高新", "开发", "新区", "城区", "新城", "工业", "临港"}
)


def _find_city_in_text(text: str) -> str | None:
    """先找带「市」的全名，再退到无后缀简称（「河北石家庄」「广西南宁」）。"""
    for name in _city_names():
        if name in text:
            return name
    for stem, full in _city_stems():
        if stem in AMBIGUOUS_CITY_STEMS:
            continue
        if stem in text:
            return full
    return None


def _scan_names(text: str, names: set[str]) -> list[tuple[int, str]]:
    """在文本中找出所有名称，按位置排序；长名优先，避免「北京市」被「北京」截断。"""
    hits: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for name in sorted(names, key=len, reverse=True):
        start = 0
        while True:
            pos = text.find(name, start)
            if pos < 0:
                break
            end = pos + len(name)
            if any(not (end <= s or pos >= e) for s, e in taken):
                start = pos + 1
                continue
            hits.append((pos, name))
            taken.append((pos, end))
            start = end
    hits.sort()
    return hits


def region_from_text(*candidates: str | None) -> str | None:
    """从若干段文本（标题/采购人/已有地址）推断并拼装完整地区。

    只在能唯一确定时补全；有歧义返回已确定的部分或 None。
    """
    text = " ".join(value for value in candidates if value)
    if not text:
        return None

    province: str | None = None
    city: str | None = None
    county: str | None = None

    for _, name in _scan_names(text, _province_names()):
        province = _lookup_province(name)
        if province:
            break

    city = _find_city_in_text(text)
    if city is None:
        for _, name in _scan_names(text, _city_names()):
            city = _lookup_city(name)
            if city:
                break

    for _, name in _scan_names(text, _county_names()):
        if any(token in name for token in AMBIGUOUS_TOKENS):
            continue
        owner = _lookup_county(name, province=province, city=city)
        if owner is None:
            continue
        county = name
        if province is None:
            province = owner[0]
        if city is None:
            city = owner[1]
        break

    if province is None and city is not None:
        city_province: dict[str, str] = _index()["city_province"]  # type: ignore[assignment]
        province = city_province.get(city) or None

    if province in MUNICIPALITIES:
        city = province

    if province is None and city is None and county is None:
        return None
    return RegionParts(province, city, county).joined()


def normalize_region(value: str | None) -> str | None:
    """整理已存在的地区串：修重复（「北京 丰台区 丰台区」）、补缺失的上级。"""
    if not value or not value.strip():
        return None
    tokens = [token for token in re.split(r"[\s,，/]+", value.strip()) if token]
    if not tokens:
        return None

    province = city = county = None
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        found_province = _lookup_province(token)
        if found_province is not None:
            province = found_province
            continue
        found_city = _lookup_city(token)
        if found_city is not None:
            city = found_city
            continue
        if _lookup_county(token, province=province, city=city) is not None or token.endswith(
            ("区", "县", "旗")
        ):
            county = token

    if province is None and city is None and county is None:
        # 完全认不出：交给文本推断，仍失败则原样返回（不能把用户已有的值弄丢）
        return region_from_text(value) or value.strip()

    if province is None and city is not None:
        city_province: dict[str, str] = _index()["city_province"]  # type: ignore[assignment]
        province = city_province.get(city) or None
    if province is None and county is not None:
        owner = _lookup_county(county, city=city)
        if owner is not None:
            province = owner[0]
            city = city or owner[1]
    if province in MUNICIPALITIES:
        city = province
    return RegionParts(province, city, county).joined() or value.strip()


def filter_terms(value: str | None) -> tuple[bool, str]:
    """把筛选输入转成 (是否精确前缀, 匹配串)。

    为什么不能无脑归一化：用户输「朝阳」可能是北京朝阳区，也可能是辽宁朝阳市；
    强行归一成「辽宁省 朝阳市」会漏掉北京朝阳区的记录。所以只在**无歧义**时
    才用前缀精确匹配，其余一律退回模糊匹配——宁可多返回，不能漏返回。
    """
    if not value or not value.strip():
        return False, ""
    raw = value.strip()

    province = _lookup_province(raw)
    if province is not None and len(raw) >= 2:
        return True, province

    city = _lookup_city(raw)
    if city is not None and raw.endswith("市"):
        # 只有明确带「市」才当前缀；「朝阳」这种裸名可能是区
        return True, city

    # 区县名：必须在全国唯一才敢精确匹配（梁溪区 -> 江苏省 无锡市 梁溪区）
    if raw.endswith(("区", "县", "旗")):
        owner = _lookup_county(raw)
        if owner is not None:
            normalized = RegionParts(owner[0], owner[1], raw).joined()
            if normalized:
                return True, normalized

    return False, raw


def region_confidence(value: str | None) -> str:
    """给出地区完整度分级，供前端决定是否提示「请核对」。

    - exact：省市区齐全
    - partial：只有省或省市
    - inferred：只有单一层级
    """
    if not value:
        return "none"
    size = len(value.split())
    if size >= 3:
        return "exact"
    if size == 2:
        return "partial"
    return "inferred"


def address_gaps(region: str | None) -> list[str]:
    """列出缺失的行政层级，用于「数据不完整」提示。"""
    if not region:
        return ["省", "市", "区/县"]
    parts = region.split()
    missing: list[str] = []
    if not any(part in _province_names() for part in parts):
        missing.append("省")
    if len(parts) < 2:
        missing.append("市")
    if len(parts) < 3:
        missing.append("区/县")
    return missing
