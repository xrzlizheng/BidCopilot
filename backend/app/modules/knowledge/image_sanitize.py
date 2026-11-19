PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
JPEG_METADATA_MARKERS = {0xE1, 0xED, 0xFE}
WEBP_SIGNATURE = b"RIFF"
WEBP_CONTAINER = b"WEBP"


def is_webp(content: bytes) -> bool:
    return (
        len(content) >= 12
        and content[:4] == WEBP_SIGNATURE
        and content[8:12] == WEBP_CONTAINER
    )


def webp_supported() -> bool:
    try:
        from PIL import Image, features

        Image.preinit()
        return bool(features.check("webp"))
    except Exception:
        return False


def webp_to_png(content: bytes) -> bytes:
    """Re-encode WebP as PNG: python-docx cannot embed WebP and the re-encode drops source metadata."""
    from io import BytesIO

    from PIL import Image, features

    Image.preinit()
    if not features.check("webp"):
        raise RuntimeError("this runtime's Pillow build cannot decode webp")
    with Image.open(BytesIO(content)) as source:
        source.load()
        mode = source.mode
        if mode in {"P", "LA", "RGBA"} or "transparency" in source.info:
            target = source.convert("RGBA")
        elif mode == "CMYK":
            target = source.convert("RGB")
        else:
            target = source.convert("RGB")
        buffer = BytesIO()
        target.save(buffer, format="PNG", optimize=True)
    payload = buffer.getvalue()
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("webp conversion did not produce png")
    return payload


def strip_png_metadata(content: bytes) -> bytes:
    if not content.startswith(PNG_SIGNATURE):
        return content
    output = bytearray(PNG_SIGNATURE)
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(content):
            return content
        if chunk_type not in PNG_METADATA_CHUNKS:
            output.extend(content[offset:end])
        offset = end
        if chunk_type == b"IEND":
            break
    return bytes(output)


def strip_jpeg_metadata(content: bytes) -> bytes:
    if not content.startswith(b"\xff\xd8"):
        return content
    output = bytearray(b"\xff\xd8")
    offset = 2
    while offset + 2 <= len(content):
        if content[offset] != 0xFF:
            return content
        marker = content[offset + 1]
        if marker == 0xD9:
            output.extend(content[offset : offset + 2])
            break
        if marker == 0xDA:
            output.extend(content[offset:])
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            output.extend(content[offset : offset + 2])
            offset += 2
            continue
        if offset + 4 > len(content):
            return content
        length = int.from_bytes(content[offset + 2 : offset + 4], "big")
        end = offset + 2 + length
        if end > len(content):
            return content
        if marker not in JPEG_METADATA_MARKERS:
            output.extend(content[offset:end])
        offset = end
    return bytes(output)


def strip_image_metadata(content: bytes, content_type: str | None) -> bytes:
    mime = (content_type or "").lower()
    if mime == "image/png" or content.startswith(PNG_SIGNATURE):
        return strip_png_metadata(content)
    if mime in {"image/jpeg", "image/jpg"} or content.startswith(b"\xff\xd8"):
        return strip_jpeg_metadata(content)
    return content
