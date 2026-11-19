from pydantic import BaseModel


class AiUsageResponse(BaseModel):
    """项目维度的 AI 用量：篇数与字数。"""

    pieces: int
    words: int
