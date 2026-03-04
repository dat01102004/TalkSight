from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query

from app import schemas  # dùng schemas.py của bạn

router = APIRouter(prefix="/news", tags=["news"])

# cache nhẹ để tránh gọi Google RSS liên tục
_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL = 60  # seconds


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
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()

        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""

        if title and link:
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                    "published": pub,
                }
            )

    _set_cached(cache_key, items)
    return items


@router.get("/top", response_model=schemas.NewsResponse)
def top_news(limit: int = Query(8, ge=1, le=20)):
    # Top news VN
    url = "https://news.google.com/rss?hl=vi&gl=VN&ceid=VN:vi"
    return {"items": _fetch_rss(url, limit=limit)}


@router.get("/search", response_model=schemas.NewsResponse)
def search_news(q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=20)):
    # Search tin trong ~24h
    qq = quote(f"{q} when:1d")
    url = f"https://news.google.com/rss/search?q={qq}&hl=vi&gl=VN&ceid=VN:vi"
    return {"items": _fetch_rss(url, limit=limit)}