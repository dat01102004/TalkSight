# app/services/gemini_service.py
from __future__ import annotations

from typing import Optional, Tuple
import re

from app.config import GEMINI_API_KEY, GEMINI_MODEL, MOCK_AI
from app.services.web_extract import extract_article_text


class GeminiQuotaError(Exception):
    """
    Dùng cho các tình huống provider quá tải / rate limit.
    main.py sẽ map exception này -> HTTP 503/429 + Retry-After.
    """
    def __init__(self, message: str, retry_after: Optional[int] = None, status_code: int = 503):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.status_code = status_code


def _parse_retry_after(msg: str) -> Optional[int]:
    # parse thô nếu message có kiểu "retry after: 10"
    m = re.search(r"retry(?:\s|-)?after[:=]?\s*(\d+)", msg, flags=re.I)
    return int(m.group(1)) if m else None


def _raise_if_overload(e: Exception) -> None:
    """
    Phân loại lỗi từ google genai:
    - 503 UNAVAILABLE / high demand => GeminiQuotaError(status=503)
    - 429 RESOURCE_EXHAUSTED / quota => GeminiQuotaError(status=429)
    """
    msg = str(e)

    retry_after = _parse_retry_after(msg) or 5

    # 429 / quota
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        raise GeminiQuotaError(
            message="AI đang bị giới hạn (429). Vui lòng thử lại sau.",
            retry_after=retry_after,
            status_code=429,
        )

    # 503 / overload
    if "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower():
        raise GeminiQuotaError(
            message="AI đang quá tải (503). Vui lòng thử lại sau.",
            retry_after=retry_after,
            status_code=503,
        )


def _require_api_key():
    if MOCK_AI:
        return
    if not GEMINI_API_KEY:
        raise RuntimeError("Thiếu GEMINI_API_KEY")


# ---------- OCR ----------
def gemini_ocr(image_bytes: bytes, mime_type: str) -> str:
    if MOCK_AI:
        return "MOCK_OCR: Văn bản OCR giả lập."

    _require_api_key()

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = (
            "Bạn là hệ thống OCR. "
            "Hãy trích xuất TOÀN BỘ văn bản trong ảnh. "
            "Giữ nguyên xuống dòng nếu có."
        )

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part],
        )

        return (resp.text or "").strip() or "Không phát hiện văn bản"

    except Exception as e:
        _raise_if_overload(e)
        raise RuntimeError(f"Lỗi Gemini OCR: {e}")


# ---------- CAPTION ----------
def gemini_caption(image_bytes: bytes, mime_type: str) -> str:
    if MOCK_AI:
        return "MOCK_CAPTION: Mô tả ảnh giả lập."

    _require_api_key()

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = (
            "Bạn là trợ lý mô tả ảnh cho người khiếm thị. "
            "Mô tả rõ ràng, tự nhiên, 2–5 câu."
        )

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part],
        )

        return (resp.text or "").strip() or "Chưa mô tả được ảnh này."

    except Exception as e:
        _raise_if_overload(e)
        raise RuntimeError(f"Lỗi Gemini Caption: {e}")


# ---------- SUMMARY ----------
def gemini_summarize_vi(text: str, max_bullets: int = 6) -> str:
    if MOCK_AI:
        return "MOCK_SUMMARY: Tóm tắt nội dung bài viết."

    _require_api_key()

    prompt = f"""
Bạn là trợ lý đọc nội dung cho người khiếm thị.
Hãy tóm tắt văn bản sau bằng tiếng Việt:

- Ngắn gọn, dễ nghe
- {max_bullets} ý chính
- Không thêm thông tin ngoài văn bản

Văn bản:
{text}
""".strip()

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return (resp.text or "").strip()

    except Exception as e:
        _raise_if_overload(e)
        raise RuntimeError(f"Lỗi Gemini Summarize: {e}")


# ---------- READ URL ----------
def gemini_read_url(
    url: str,
    want_summary: bool = False,
    summary: Optional[bool] = None,   # alias
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Return: (text, summary_text, title)
    """
    if summary is not None:
        want_summary = bool(summary)

    text, title = extract_article_text(url=url)

    summary_text: Optional[str] = None
    if want_summary:
        summary_text = gemini_summarize_vi(text)

    return text, summary_text, title