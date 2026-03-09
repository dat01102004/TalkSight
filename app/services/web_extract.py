from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
}


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_markdown(text: str) -> str:
    text = text or ""
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"[*_`#>]", "", text)
    text = re.sub(r"^\s*[-•]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_google_news_url(url: str) -> bool:
    h = _host(url)
    return h == "news.google.com" or h.endswith(".news.google.com")


def _is_google_related_url(url: str) -> bool:
    h = _host(url)

    blocked_hosts = (
        "google.com",
        "news.google.com",
        "googleusercontent.com",
        "gstatic.com",
        "googleapis.com",
        "googleadservices.com",
        "googlesyndication.com",
        "withgoogle.com",
        "blog.google",
        "about.google",
        "safety.google",
        "store.google.com",
        "one.google.com",
        "lens.google",
        "maps.google.com",
        "accounts.google.com",
        "play.google.com",
        "android.com",
        "www.android.com",
    )

    return any(h == g or h.endswith("." + g) for g in blocked_hosts)


def _normalize_url(candidate: str, base_url: str) -> Optional[str]:
    if not candidate:
        return None

    candidate = candidate.strip()
    if not candidate:
        return None

    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif candidate.startswith("/"):
        candidate = urljoin(base_url, candidate)

    if not candidate.startswith(("http://", "https://")):
        return None

    return candidate


def _pick_best_external_url(candidates: list[str], base_url: str) -> Optional[str]:
    for raw in candidates:
        url = _normalize_url(raw, base_url)
        if not url:
            continue
        if _is_google_related_url(url):
            continue
        return url
    return None


def _extract_outbound_article_url(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    canonical_candidates: list[str] = []
    for tag in soup.find_all("link", href=True):
        rel = tag.get("rel") or []
        rel_joined = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
        if "canonical" in rel_joined:
            canonical_candidates.append(tag.get("href", ""))

    picked = _pick_best_external_url(canonical_candidates, base_url)
    if picked:
        return picked

    meta_candidates: list[str] = []
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        if prop in {"og:url", "twitter:url"} and content:
            meta_candidates.append(content)

    picked = _pick_best_external_url(meta_candidates, base_url)
    if picked:
        return picked

    anchor_candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        anchor_candidates.append(href)

    picked = _pick_best_external_url(anchor_candidates, base_url)
    if picked:
        return picked

    return None


def _extract_main_text_from_html(html: str, max_chars: int = 25000) -> Tuple[str, Optional[str]]:
    doc = Document(html)
    title = doc.short_title() or None

    try:
        article_html = doc.summary(html_partial=True)
    except Exception:
        article_html = html

    soup = BeautifulSoup(article_html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = _clean_text(soup.get_text("\n"))

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
        "top news",
        "cập nhật liên tục",
        "cá nhân hóa",
        "personalized",
    ]
    matched = sum(1 for s in signals if s in hay)
    return matched >= 2


def _looks_like_google_product_page(text: str, title: Optional[str], resolved_url: str) -> bool:
    hay = f"{title or ''}\n{text}\n{resolved_url}".lower()

    signals = [
        "google's products and services",
        "all things android",
        "see what’s new",
        "see what's new",
        "work better together",
        "google products",
        "android",
        "pixel",
        "gemini",
    ]

    matched = sum(1 for s in signals if s in hay)
    return matched >= 2


def _fetch_html(client: httpx.Client, url: str) -> tuple[str, str]:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text, str(resp.url)


def extract_article_text_with_meta(
    url: str,
    timeout_seconds: float = 15.0,
    max_chars: int = 25000,
) -> Tuple[str, Optional[str], str]:
    """
    Returns:
        (text, title, resolved_url)

    Flow:
    1) Fetch URL đầu vào
    2) Nếu vẫn là Google News thì resolve sang bài gốc qua canonical / og:url / external link
    3) Dùng readability lấy main text
    """
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers=DEFAULT_HEADERS,
    ) as client:
        html, resolved_url = _fetch_html(client, url)

        if _is_google_news_url(resolved_url):
            outbound = _extract_outbound_article_url(html, resolved_url)
            if outbound:
                try:
                    html2, resolved_url2 = _fetch_html(client, outbound)
                    if not _is_google_related_url(resolved_url2):
                        html = html2
                        resolved_url = resolved_url2
                except Exception:
                    pass

        text, title = _extract_main_text_from_html(html, max_chars=max_chars)

        if _looks_like_google_news_boilerplate(text, title) or _looks_like_google_product_page(text, title, resolved_url):
            outbound = _extract_outbound_article_url(html, resolved_url)
            if outbound:
                try:
                    html2, resolved_url2 = _fetch_html(client, outbound)
                    text2, title2 = _extract_main_text_from_html(html2, max_chars=max_chars)

                    bad_google_news = _looks_like_google_news_boilerplate(text2, title2)
                    bad_google_product = _looks_like_google_product_page(text2, title2, resolved_url2)

                    if not bad_google_news and not bad_google_product:
                        return _strip_markdown(text2), title2, resolved_url2
                except Exception:
                    pass

        return _strip_markdown(text), title, resolved_url


def extract_article_text(
    url: str,
    timeout_seconds: float = 15.0,
    max_chars: int = 25000,
) -> Tuple[str, Optional[str]]:
    """
    Backward-compatible wrapper.
    """
    text, title, _ = extract_article_text_with_meta(
        url=url,
        timeout_seconds=timeout_seconds,
        max_chars=max_chars,
    )
    return text, title