import html as html_lib
import re

import nh3

MAX_CONTENT_CHARS = 200_000

ALLOWED_TAGS = {
    "a",
    "br",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt"},
}


def sanitize_html(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    # 先按原始长度截断再清洗：避免超长正文整篇进入 HTML 解析（CPU/内存），
    # 也避免清洗后才截断落在标签/实体中间。
    value = value[:MAX_CONTENT_CHARS]
    cleaned = nh3.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    )
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return cleaned[:MAX_CONTENT_CHARS], text[:MAX_CONTENT_CHARS]


def safe_source_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    # scheme 大小写不敏感（HTTP:// 也是合法链接），保留原始大小写返回。
    if value.lower().startswith(("http://", "https://")):
        return value
    return None
