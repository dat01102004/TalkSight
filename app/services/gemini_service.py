# ✅ DÒNG NÀY PHẢI Ở TRÊN CÙNG
from __future__ import annotations

from typing import Optional, Tuple
import re

from fastapi import HTTPException

from app.config import GEMINI_API_KEY, GEMINI_MODEL, MOCK_AI
from app.services.web_extract import extract_article_text


class GeminiQuotaError(Exception):
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


# ---------- OCR ----------
def gemini_ocr(image_bytes: bytes, mime_type: str) -> str:
    if MOCK_AI:
        return "MOCK_OCR: Văn bản OCR giả lập."

    if not GEMINI_API_KEY:
        raise RuntimeError("Thiếu GEMINI_API_KEY")

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
        raise RuntimeError(f"Lỗi Gemini OCR: {e}")


# ---------- CAPTION ----------
def gemini_caption(image_bytes: bytes, mime_type: str) -> str:
    if MOCK_AI:
        return "MOCK_CAPTION: Mô tả ảnh giả lập."

    if not GEMINI_API_KEY:
        raise RuntimeError("Thiếu GEMINI_API_KEY")

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
        raise RuntimeError(f"Lỗi Gemini Caption: {e}")


# ---------- SUMMARY ----------
def gemini_summarize_vi(text: str, max_bullets: int = 6) -> str:
    if MOCK_AI:
        return "MOCK_SUMMARY: Tóm tắt nội dung bài viết."

    prompt = f"""
Bạn là trợ lý đọc nội dung cho người khiếm thị.
Hãy tóm tắt văn bản sau bằng tiếng Việt:

- Ngắn gọn, dễ nghe
- {max_bullets} ý chính
- Không thêm thông tin ngoài văn bản

Văn bản:
{text}
""".strip()

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return (resp.text or "").strip()


# ---------- READ URL (BƯỚC 3) ----------
def gemini_read_url(
    url: str,
    want_summary: bool = False,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Trả về:
    - text: nội dung bài viết
    - summary: tóm tắt (nếu có)
    - title: tiêu đề
    """

    text, title = extract_article_text(url)

    summary: Optional[str] = None
    if want_summary:
        summary = gemini_summarize_vi(text)

    return text, summary, title
