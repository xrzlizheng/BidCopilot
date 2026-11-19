from app.config import get_settings
from app.modules.tender.ocr import OcrProvider, build_ocr_provider


def get_ocr_provider() -> OcrProvider | None:
    return build_ocr_provider(get_settings())
