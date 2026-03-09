from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_google_host(host: str) -> bool:
    host = (host or "").lower()
    return (
        host.endswith("news.google.com")
        or host.endswith(".news.google.com")
        or host == "news.google.com"
    )


def _is_google_related_host(host: str) -> bool:
    host = (host or "").lower()
    google_hosts = (
        "google.com",
        "news.google.com",
        "googleusercontent.com",
        "gstatic.com",
        "googlesyndication.com",
        "googleapis.com",
        "googleadservices.com",
        "lh3.googleusercontent.com",
    )
    return any(host == h or host.endswith("." + h) for h in google_hosts)


def _normalize_url(candidate: str, base_url: str) -> Optional[str]:
    if not candidate:
        return None

    candidate = candidate.strip()
    if not candidate:
        return None

    if candidate.startswith("//"):
        candidate = "https:" + candidate

    if candidate.startswith("/"):
        candidate = urljoin(base_url, candidate)

    if not candidate.startswith("http://") and not candidate.startswith("https://"):
        return None

    parsed = urlparse(candidate)
    if not parsed.netloc:
        return None

    return candidate


def _pick_non_google_url(candidates: list[str], base_url: str) -> Optional[str]:
    for raw in candidates:
        u = _normalize_url(raw, base_url)
        if not u:
            continue

        host = urlparse(u).netloc.lower()
        if _is_google_related_host(host):
            continue

        return u

    return None


def _extract_outbound_article_url(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    # 1) canonical
    canonical_candidates: list[str] = []
    for tag in soup.find_all("link", rel=True, href=True):
        rel_values = tag.get("rel") or []
        rel_joined = " ".join(rel_values).lower() if isinstance(rel_values, list) else str(rel_values).lower()
        if "canonical" in rel_joined:
            canonical_candidates.append(tag.get("href", ""))

    picked = _pick_non_google_url(canonical_candidates, base_url)
    if picked:
        return picked

    # 2) meta og:url / twitter:url
    meta_candidates: list[str] = []
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        if prop in {"og:url", "twitter:url"} and content:
            meta_candidates.append(content)

    picked = _pick_non_google_url(meta_candidates, base_url)
    if picked:
        return picked

    # 3) first external anchor not from Google
    anchor_candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        anchor_candidates.append(href)

    picked = _pick_non_google_url(anchor_candidates, base_url)
    if picked:
        return picked

    return None


def _extract_text_from_html(html: str, max_chars: int) -> Tuple[str, Optional[str]]:
    doc = Document(html)
    title = doc.short_title() or None

    article_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(article_html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    text = _clean_text(text)

    if not text:
        soup2 = BeautifulSoup(html, "html.parser")
        for tag in soup2(["script", "style", "noscript"]):
            tag.decompose()
        text = _clean_text(soup2.get_text("\n"))

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text, title


def _looks_like_google_news_boilerplate(text: str, title: Optional[str]) -> bool:
    hay = f"{title or ''}\n{text}".lower()

    signals = [
        "google news",
        "dịch vụ tập hợp",
        "hàng nghìn nguồn tin",
        "top stories",
        "personalized",
        "cập nhật liên tục",
    ]

    matched = sum(1 for s in signals if s in hay)
    return matched >= 2


def _fetch_html(client: httpx.Client, url: str) -> tuple[str, str]:
    r = client.get(url)
    r.raise_for_status()
    return r.text, str(r.url)


def extract_article_text(
    url: str,
    timeout_seconds: float = 15.0,
    max_chars: int = 25000,
) -> Tuple[str, Optional[str]]:
    """
    Trả về: (content_text, title)

    Logic:
    - Fetch URL đầu vào.
    - Nếu final URL vẫn là news.google.com thì cố resolve sang URL bài báo gốc.
    - Sau đó mới dùng readability để lấy main content.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers=headers,
    ) as client:
        html, final_url = _fetch_html(client, url)
        final_host = urlparse(final_url).netloc.lower()

        # Nếu vẫn là Google News, cố lấy link bài gốc
        if _looks_like_google_host(final_host):
            outbound_url = _extract_outbound_article_url(html, final_url)
            if outbound_url:
                try:
                    html2, final_url2 = _fetch_html(client, outbound_url)
                    final_host2 = urlparse(final_url2).netloc.lower()

                    if not _is_google_related_host(final_host2):
                        html = html2
                        final_url = final_url2
                except Exception:
                    # fallback lại html cũ nếu fetch link ngoài lỗi
                    pass

        text, title = _extract_text_from_html(html, max_chars=max_chars)

        # Nếu vẫn còn dính boilerplate Google News thì thử resolve thêm 1 lần
        if _looks_like_google_news_boilerplate(text, title):
            outbound_url = _extract_outbound_article_url(html, final_url)
            if outbound_url:
                html2, final_url2 = _fetch_html(client, outbound_url)
                text2, title2 = _extract_text_from_html(html2, max_chars=max_chars)

                if not _looks_like_google_news_boilerplate(text2, title2):
                    return text2, title2

        return text, title