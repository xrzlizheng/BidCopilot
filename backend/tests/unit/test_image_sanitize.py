import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image

from app.modules.knowledge.image_sanitize import (
    is_webp,
    strip_image_metadata,
    strip_jpeg_metadata,
    strip_png_metadata,
    webp_supported,
    webp_to_png,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _png_with_text() -> bytes:
    payload = b"Comment\x00company-secret"
    chunk = (
        struct.pack(">I", len(payload))
        + b"tEXt"
        + payload
        + struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF)
    )
    return PNG[:-12] + chunk + PNG[-12:]


def test_strip_png_metadata_removes_text_chunk() -> None:
    payload = _png_with_text()
    assert b"company-secret" in payload
    cleaned = strip_png_metadata(payload)
    assert b"tEXt" not in cleaned
    assert b"company-secret" not in cleaned
    assert cleaned.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IEND" in cleaned


def test_strip_jpeg_metadata_removes_app1_and_comment() -> None:
    app1_data = b"Exif\x00\x00secret"
    comment_data = b"note!"
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(app1_data)) + app1_data
    comment = b"\xff\xfe" + struct.pack(">H", 2 + len(comment_data)) + comment_data
    payload = b"\xff\xd8" + app1 + comment + b"\xff\xd9"
    cleaned = strip_jpeg_metadata(payload)
    assert cleaned == b"\xff\xd8\xff\xd9"


def test_strip_image_metadata_dispatches_by_magic() -> None:
    assert b"tEXt" not in strip_image_metadata(_png_with_text(), "image/png")
    assert strip_image_metadata(b"\xff\xd8\xff\xd9", "image/jpeg") == b"\xff\xd8\xff\xd9"
    assert strip_image_metadata(b"plain", "text/plain") == b"plain"


WEBP_RGB = bytes.fromhex(
    "524946461e000000574542505650384c110000002f0600010007508f22d7a3ff8188e87f0000"
)
WEBP_RGBA = bytes.fromhex(
    "524946461e000000574542505650384c110000002f0600011007508f22d7a3808188e87f0000"
)

requires_webp = pytest.mark.skipif(
    not webp_supported(),
    reason="this Pillow build has no webp codec; conversion is gated on it",
)


def test_is_webp_detects_container_without_decoding() -> None:
    assert is_webp(WEBP_RGB)
    assert is_webp(WEBP_RGBA)
    assert not is_webp(PNG)
    assert not is_webp(b"RIFF")
    assert not is_webp(b"RIFF" + bytes(8) + b"AVI ")


@requires_webp
def test_webp_to_png_preserves_pixels_and_drops_metadata() -> None:
    converted = webp_to_png(WEBP_RGB)
    assert converted.startswith(bytes.fromhex("89504e470d0a1a0a"))
    assert struct.unpack(">II", converted[16:24]) == (7, 5)
    for chunk in (b"tEXt", b"iTXt", b"zTXt", b"eXIf"):
        assert chunk not in converted
    assert strip_image_metadata(converted, "image/png") == converted
    with Image.open(BytesIO(converted)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (200, 30, 30)


@requires_webp
def test_webp_to_png_keeps_alpha() -> None:
    with Image.open(BytesIO(webp_to_png(WEBP_RGBA))) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0)) == (200, 30, 30, 128)


def test_webp_to_png_reports_missing_codec() -> None:
    if webp_supported():
        pytest.skip("runtime has a webp codec; missing-codec branch not reachable here")
    with pytest.raises(RuntimeError, match="cannot decode webp"):
        webp_to_png(WEBP_RGB)
