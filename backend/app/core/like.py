"""LIKE 模式转义：用户输入按字面量匹配，%/_/\\ 不得变成通配符。

调用侧统一写法：`column.ilike(like_contains(q), escape="\\")`。
"""


def escape_like(value: str) -> str:
    # 反斜杠必须最先转义，否则会把后续注入的转义符再转义一遍。
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    return f"%{escape_like(value)}%"


def like_prefix(value: str) -> str:
    return f"{escape_like(value)}%"
