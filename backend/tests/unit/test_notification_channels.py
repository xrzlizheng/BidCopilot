from uuid import uuid4

import pytest

from app.core.errors import ValidationAppError
from app.modules.notifications import channels


def test_wechat_payload_includes_body() -> None:
    payload = channels._wechat_payload("标题", "正文")
    assert payload == {"msgtype": "text", "text": {"content": "标题\n正文"}}


def test_wechat_payload_title_only() -> None:
    payload = channels._wechat_payload("标题", None)
    assert payload["text"]["content"] == "标题"


async def test_create_channel_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationAppError):
        await channels.create_channel(
            None,  # type: ignore[arg-type]
            tenant_id=uuid4(),
            kind="sms",
            target="anything",
            label=None,
            user_id=uuid4(),
        )


async def test_create_channel_rejects_insecure_wechat_target() -> None:
    with pytest.raises(ValidationAppError):
        await channels.create_channel(
            None,  # type: ignore[arg-type]
            tenant_id=uuid4(),
            kind="wechat",
            target="http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
            label=None,
            user_id=uuid4(),
        )
