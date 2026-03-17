from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageOps

from app.config import (
    IMAGE_CACHE_DIR,
    IMAGE_CACHE_ENABLED,
    IMAGE_CACHE_MAX_ENTRIES,
    IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE,
    IMAGE_CACHE_TTL_SECONDS,
)


@dataclass(frozen=True)
class ImageFingerprints:
    canonical_sha256: str
    dhash_hex: str


@dataclass(frozen=True)
class ImageCacheHit:
    result_text: str
    match_type: str  # exact | near


class ImageHashCache:
    def __init__(
        self,
        *,
        enabled: bool,
        cache_dir: str,
        ttl_seconds: int,
        near_dup_max_distance: int,
        max_entries: int,
    ) -> None:
        self.enabled = enabled
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "image_cache_index.json"
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.near_dup_max_distance = max(0, int(near_dup_max_distance))
        self.max_entries = max(100, int(max_entries))
        self._lock = threading.RLock()
        self._index: Dict[str, Dict[str, Any]] = self._load_index()

    def get(self, mode: str, image_bytes: bytes) -> Optional[ImageCacheHit]:
        if not self.enabled:
            return None

        fingerprints = build_image_fingerprints(image_bytes)
        now = utc_now()

        with self._lock:
            changed = self._purge_expired_unlocked(now)

            exact_key = self._make_key(mode=mode, canonical_sha256=fingerprints.canonical_sha256)
            entry = self._index.get(exact_key)
            if self._is_valid_entry(entry, now):
                entry["last_hit_at"] = now.isoformat()
                self._save_index_unlocked()
                return ImageCacheHit(result_text=entry["result_text"], match_type="exact")

            best_key: Optional[str] = None
            best_entry: Optional[Dict[str, Any]] = None
            best_distance: Optional[int] = None

            for key, candidate in self._index.items():
                if candidate.get("mode") != mode:
                    continue
                if not candidate.get("allow_near_match", False):
                    continue
                if not self._is_valid_entry(candidate, now):
                    changed = True
                    continue

                candidate_dhash = candidate.get("dhash_hex") or ""
                if not candidate_dhash:
                    continue

                distance = hamming_distance_hex(fingerprints.dhash_hex, candidate_dhash)
                if distance > self.near_dup_max_distance:
                    continue

                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_key = key
                    best_entry = candidate

            if changed:
                self._save_index_unlocked()

            if best_key and best_entry:
                best_entry["last_hit_at"] = now.isoformat()
                self._save_index_unlocked()
                return ImageCacheHit(result_text=best_entry["result_text"], match_type="near")

            return None

    def set(self, mode: str, image_bytes: bytes, result_text: str) -> None:
        if not self.enabled:
            return

        text = (result_text or "").strip()
        if not text:
            return

        fingerprints = build_image_fingerprints(image_bytes)
        now = utc_now()
        key = self._make_key(mode=mode, canonical_sha256=fingerprints.canonical_sha256)

        with self._lock:
            self._purge_expired_unlocked(now)
            self._index[key] = {
                "mode": mode,
                "canonical_sha256": fingerprints.canonical_sha256,
                "dhash_hex": fingerprints.dhash_hex,
                "result_text": text,
                "allow_near_match": should_allow_near_match(mode=mode, result_text=text),
                "created_at": now.isoformat(),
                "last_hit_at": now.isoformat(),
            }
            self._trim_unlocked()
            self._save_index_unlocked()

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        if not self.enabled or not self.index_path.exists():
            return {}

        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {}

    def _save_index_unlocked(self) -> None:
        temp_path = self.index_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.index_path)

    def _purge_expired_unlocked(self, now: datetime) -> bool:
        changed = False
        expired_keys = []

        for key, entry in self._index.items():
            if not self._is_valid_entry(entry, now):
                expired_keys.append(key)

        for key in expired_keys:
            self._index.pop(key, None)
            changed = True

        return changed

    def _trim_unlocked(self) -> None:
        if len(self._index) <= self.max_entries:
            return

        def sort_key(item: tuple[str, Dict[str, Any]]) -> tuple[str, str]:
            entry = item[1]
            return (
                entry.get("last_hit_at") or entry.get("created_at") or "",
                entry.get("created_at") or "",
            )

        items = sorted(self._index.items(), key=sort_key, reverse=True)
        kept = dict(items[: self.max_entries])
        self._index = kept

    def _is_valid_entry(self, entry: Optional[Dict[str, Any]], now: datetime) -> bool:
        if not entry:
            return False
        created_at = parse_iso_datetime(entry.get("created_at"))
        if created_at is None:
            return False
        return created_at + timedelta(seconds=self.ttl_seconds) >= now

    @staticmethod
    def _make_key(*, mode: str, canonical_sha256: str) -> str:
        return f"{mode}:{canonical_sha256}"


image_hash_cache = ImageHashCache(
    enabled=IMAGE_CACHE_ENABLED,
    cache_dir=IMAGE_CACHE_DIR,
    ttl_seconds=IMAGE_CACHE_TTL_SECONDS,
    near_dup_max_distance=IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE,
    max_entries=IMAGE_CACHE_MAX_ENTRIES,
)


def get_cached_image_result(mode: str, image_bytes: bytes) -> Optional[ImageCacheHit]:
    return image_hash_cache.get(mode=mode, image_bytes=image_bytes)


def cache_image_result(mode: str, image_bytes: bytes, result_text: str) -> None:
    image_hash_cache.set(mode=mode, image_bytes=image_bytes, result_text=result_text)


def build_image_fingerprints(image_bytes: bytes) -> ImageFingerprints:
    try:
        image = Image.open(BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))

        canonical_buffer = BytesIO()
        image.save(canonical_buffer, format="JPEG", quality=72, optimize=True)
        canonical_bytes = canonical_buffer.getvalue()

        return ImageFingerprints(
            canonical_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            dhash_hex=compute_dhash_hex(image),
        )
    except Exception:
        raw_sha = hashlib.sha256(image_bytes).hexdigest()
        fallback_dhash = raw_sha[:16]
        return ImageFingerprints(canonical_sha256=raw_sha, dhash_hex=fallback_dhash)


def compute_dhash_hex(image: Image.Image, hash_size: int = 8) -> str:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())

    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits = (bits << 1) | int(left > right)

    return f"{bits:0{hash_size * hash_size // 4}x}"


def hamming_distance_hex(a: str, b: str) -> int:
    if not a or not b:
        return 999
    width = max(len(a), len(b))
    ax = int(a.ljust(width, "0"), 16)
    bx = int(b.ljust(width, "0"), 16)
    return (ax ^ bx).bit_count()


def should_allow_near_match(*, mode: str, result_text: str) -> bool:
    text = (result_text or "").strip().lower()
    if len(text) < 12:
        return False

    generic = {
        "không phát hiện văn bản",
        "chưa mô tả được ảnh này.",
        "mình chưa mô tả rõ được khung hình này.",
        "chưa thấy chữ rõ để đọc.",
    }
    if text in generic:
        return False

    if mode == "ocr" and "không phát hiện văn bản" in text:
        return False
    if mode == "caption" and "chưa mô tả" in text:
        return False

    return True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None