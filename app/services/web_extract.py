from __future__ import annotations

import re
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from readability import Document


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_article_text(
    url: str,
    timeout_seconds: float = 15.0,
    max_chars: int = 25000,
) -> Tuple[str, Optional[str]]:
    """
    Trả về: (content_text, title)
    - Dùng readability-lxml để lấy phần "main content" tốt hơn soup thường.
    - Giới hạn max_chars để không quá dài (đỡ tốn token khi summary).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    with httpx.Client(follow_redirects=True, timeout=timeout_seconds, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        html = r.text

    doc = Document(html)
    title = doc.short_title() or None
    article_html = doc.summary(html_partial=True)

    soup = BeautifulSoup(article_html, "html.parser")

    # remove script/style/noscript
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    text = _clean_text(text)

    if not text:
        # fallback: lấy toàn bộ html nếu readability fail
        soup2 = BeautifulSoup(html, "html.parser")
        for tag in soup2(["script", "style", "noscript"]):
            tag.decompose()
        text = _clean_text(soup2.get_text("\n"))

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text, title
