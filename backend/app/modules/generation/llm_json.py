"""LLM 结构化输出的容错调用。

模型（尤其小参数量判别模型）偶发在中文长字符串里漏转义引号，导致 JSON 解析失败。
这里做有界重试：解析失败时把「必须转义引号」的约束追加到 system 再试一次，
仍失败则抛出带原文片段的错误，便于定位是模型问题还是提示词问题。

`complete_json_object_ex` 额外返回本次调用的可观测信号（重试次数、耗时、输出长度）。
重试次数是零成本的置信度来源：第一次就吐出合法 JSON，说明结构化判断稳定；反复修复
才成功，说明输出处于漂移边缘。调用方据此决定是否要人工复核。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.modules.generation.llm_client import LLMClient, LLMResponseError

SNIPPET_LEN = 200
# 错误信息会经 AppError.message 原样回显给前端，绝不能带模型原文（可能含提示词、
# 企业资产、招标正文）。原文只进日志，供排障。
SAFE_MESSAGE = "LLM 返回内容不是合法 JSON"

logger = logging.getLogger("bidcopilot.generation.llm_json")

RETRY_HINT = (
    "上一次输出不是合法 JSON。请只输出一个 JSON 对象：字符串内的双引号必须转义为 \\\"，"
    "不得换行未转义，不得输出 markdown 代码块、注释或任何解释文字。"
)


@dataclass(frozen=True)
class JsonCallMeta:
    """一次结构化调用留下的信号，用于折算置信度与排障。"""

    attempts_used: int
    repair_count: int
    elapsed_seconds: float
    output_chars: int


def parse_json_object(content: str) -> dict[str, Any]:
    """从模型输出里取出第一个 JSON 对象；失败时抛出带原文片段的 LLMResponseError。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1])
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMResponseError(SAFE_MESSAGE)
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMResponseError(
            f"{SAFE_MESSAGE}（{exc.msg} @ {exc.lineno}:{exc.colno}）"
        ) from exc
    if not isinstance(data, dict):
        raise LLMResponseError(SAFE_MESSAGE)
    return data


async def complete_json_object_ex(
    client: LLMClient,
    *,
    system: str,
    user: str,
    attempts: int = 2,
    total_budget_seconds: float | None = None,
) -> tuple[dict[str, Any], JsonCallMeta]:
    """调用模型并解析 JSON 对象，同时返回重试/耗时等信号。

    `total_budget_seconds` 限制所有尝试的总耗时：单次超时 × 重试次数很容易超过
    反向代理的 read timeout（nginx 默认 300s），一旦超限，客户端拿到的是 504 而不是
    业务错误，且数据库里已经写了 failed。给个总预算，超了就带着已发生的错误退出。
    """
    attempts = max(1, attempts)
    last_error: LLMResponseError | None = None
    started = time.monotonic()
    for index in range(attempts):
        if (
            total_budget_seconds is not None
            and index > 0
            and time.monotonic() - started >= total_budget_seconds
        ):
            logger.warning("llm_json.budget_exhausted attempts=%d", index)
            break
        prompt = system if index == 0 else f"{system}\n\n{RETRY_HINT}"
        content = await client.complete(system=prompt, user=user)
        try:
            data = parse_json_object(content)
        except LLMResponseError as exc:
            last_error = exc
            logger.warning(
                "llm_json.attempt_failed model=%s attempt=%d len=%d snippet=%r",
                getattr(client, "model", None),
                index + 1,
                len(content),
                content[:SNIPPET_LEN],
            )
            continue
        return data, JsonCallMeta(
            attempts_used=index + 1,
            # 第 1 次就成功 = 0 次修复，是"模型自己说得清楚"的正向信号。
            repair_count=index,
            elapsed_seconds=round(time.monotonic() - started, 3),
            output_chars=len(content),
        )
    if last_error is not None:
        raise last_error
    raise LLMResponseError("LLM 未返回 JSON 对象")


async def complete_json_object(
    client: LLMClient,
    *,
    system: str,
    user: str,
    attempts: int = 2,
    total_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """只要结果的薄封装，保持既有调用点不变。"""
    data, _ = await complete_json_object_ex(
        client,
        system=system,
        user=user,
        attempts=attempts,
        total_budget_seconds=total_budget_seconds,
    )
    return data
