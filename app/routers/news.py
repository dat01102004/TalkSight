from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query

from app import schemas

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None


router = APIRouter(prefix="/news", tags=["news"])

_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL = 60  # seconds

_LINK_CACHE: Dict[str, tuple[float, str]] = {}
_LINK_CACHE_TTL = 600  # seconds


def _get_cached(key: str) -> Optional[List[Dict[str, Any]]]:
    v = _CACHE.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return data


def _set_cached(key: str, data: List[Dict[str, Any]]) -> None:
    _CACHE[key] = (time.time(), data)


def _get_link_cached(key: str) -> Optional[str]:
    v = _LINK_CACHE.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > _LINK_CACHE_TTL:
        _LINK_CACHE.pop(key, None)
        return None
    return data


def _set_link_cached(key: str, data: str) -> None:
    _LINK_CACHE[key] = (time.time(), data)


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_google_news_url(url: str) -> bool:
    h = _host(url)
    return h == "news.google.com" or h.endswith(".news.google.com")


def _looks_like_valid_article_url(url: str) -> bool:
    if not url:
        return False

    h = _host(url)
    if not h:
        return False

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

    if any(h == x or h.endswith("." + x) for x in blocked_hosts):
        return False

    low = url.lower()
    bad_parts = [
        "/search?",
        "/tag/",
        "/tags/",
        "/topic/",
        "/category/",
    ]
    if any(x in low for x in bad_parts):
        return False

    return url.startswith("http://") or url.startswith("https://")


def _resolve_google_news_link(url: str) -> str:
    if not url:
        return url

    cached = _get_link_cached(url)
    if cached:
        return cached

    if not _is_google_news_url(url):
        _set_link_cached(url, url)
        return url

    # Ưu tiên package chuyên dụng
    if gnewsdecoder is not None:
        try:
            result = gnewsdecoder(url, interval=1)
            if isinstance(result, dict):
                decoded = (result.get("decoded_url") or "").strip()
                status = bool(result.get("status"))
                if status and _looks_like_valid_article_url(decoded):
                    _set_link_cached(url, decoded)
                    return decoded
        except Exception:
            pass

    # Fallback: giữ link gốc nếu decode thất bại
    _set_link_cached(url, url)
    return url


def _fetch_rss(url: str, limit: int = 10) -> List[Dict[str, Any]]:
    cache_key = f"{url}|{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": "TalkSight/1.0"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            xml_text = r.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Không lấy được RSS: {e}")

    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RSS parse lỗi: {e}")

    items: List[Dict[str, Any]] = []
    for item in channel.findall("item")[:limit]:
        title = (item.findtext("title") or "").strip()
        raw_link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()

        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""

        if not title or not raw_link:
            continue

        resolved_link = _resolve_google_news_link(raw_link)

        items.append(
            {
                "title": title,
                "link": resolved_link,
                "source": source,
                "published": pub,
            }
        )

    _set_cached(cache_key, items)
    return items


@router.get("/top", response_model=schemas.NewsResponse)
def top_news(limit: int = Query(8, ge=1, le=20)):
    url = "https://news.google.com/rss?hl=vi&gl=VN&ceid=VN:vi"
    return {"items": _fetch_rss(url, limit=limit)}


@router.get("/search", response_model=schemas.NewsResponse)
def search_news(q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=20)):
    qq = quote(f"{q} when:1d")
    url = f"https://news.google.com/rss/search?q={qq}&hl=vi&gl=VN&ceid=VN:vi"
    return {"items": _fetch_rss(url, limit=limit)}