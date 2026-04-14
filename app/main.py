from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text as sa_text
from sqlalchemy.orm import Session

from . import models, schemas
from .config import (
    IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE,
    MAX_UPLOAD_BYTES,
    UPLOAD_DIR as UPLOAD_DIR_SETTING,
)
from .db import Base, engine, get_db
from .routers.news import router as news_router
from .security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from .services.gemini_service import (
    GeminiQuotaError,
    gemini_caption,
    gemini_ocr,
    gemini_summarize_vi,
)
from .services.image_cache import (
    build_image_fingerprints,
    cache_image_result,
    get_cached_image_result,
    hamming_distance_hex,
)
from .services.text_clean import clean_tts_text
from .services.web_extract import extract_article_text_with_meta

app = FastAPI(title="TalkSight API")
app.include_router(news_router)

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(UPLOAD_DIR_SETTING)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

bearer_scheme = HTTPBearer(auto_error=False)

_HISTORY_LOCKS: dict[str, threading.Lock] = {}
_HISTORY_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class HistoryDuplicateMatch:
    history: models.History
    match_type: str  # exact | near


@dataclass(frozen=True)
class HistorySaveResult:
    history_id: Optional[int]
    deduplicated: bool
    match_type: Optional[str]
    saved_to_history: bool
    existing_text: Optional[str] = None


def _ensure_schema_columns() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "users" in table_names:
            user_cols = {col["name"] for col in inspector.get_columns("users")}
            if "full_name" not in user_cols:
                conn.execute(sa_text("ALTER TABLE users ADD COLUMN full_name VARCHAR(120)"))
            if "phone" not in user_cols:
                conn.execute(sa_text("ALTER TABLE users ADD COLUMN phone VARCHAR(20)"))

        if "histories" in table_names:
            history_cols = {col["name"] for col in inspector.get_columns("histories")}
            if "image_sha256" not in history_cols:
                conn.execute(sa_text("ALTER TABLE histories ADD COLUMN image_sha256 VARCHAR(64)"))
            if "image_dhash" not in history_cols:
                conn.execute(sa_text("ALTER TABLE histories ADD COLUMN image_dhash VARCHAR(32)"))


_ensure_schema_columns()


def _history_lock_for(user_id: int, action_type: str) -> threading.Lock:
    key = f"{user_id}:{action_type}"
    with _HISTORY_LOCKS_GUARD:
        lock = _HISTORY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _HISTORY_LOCKS[key] = lock
        return lock


def get_current_user_required(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Thiếu token")

    payload = decode_access_token(creds.credentials.strip())
    user_id = payload.get("sub")
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()

    if not user:
        raise HTTPException(status_code=401, detail="User không tồn tại")

    return user


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not creds or not creds.credentials:
        return None

    try:
        payload = decode_access_token(creds.credentials.strip())
        user_id = payload.get("sub")
        if not user_id:
            return None
        return db.query(models.User).filter(models.User.id == int(user_id)).first()
    except Exception:
        return None


async def read_upload_file(file: UploadFile) -> tuple[str, str, bytes]:
    suffix = Path(file.filename or "").suffix or ".jpg"
    mime_type = file.content_type or "image/jpeg"
    content = await file.read()

    if not content:
        raise HTTPException(status_code=422, detail="File rỗng")

    if len(content) > MAX_UPLOAD_BYTES:
        max_mb = round(MAX_UPLOAD_BYTES / (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"File quá lớn (>{max_mb}MB)")

    return suffix, mime_type, content


def save_upload_bytes(content: bytes, suffix: str) -> str:
    name = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(content)
    return str(dest)


def _raise_quota_http(e: GeminiQuotaError) -> None:
    headers = {}
    if e.retry_after is not None:
        headers["Retry-After"] = str(e.retry_after)
    raise HTTPException(status_code=e.status_code, detail=e.message, headers=headers)


def _looks_like_google_news_boilerplate(title: Optional[str], text: str) -> bool:
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
    matched = sum(1 for signal in signals if signal in hay)
    return matched >= 2


def _should_drop_generic_title(title: Optional[str]) -> bool:
    if not title or not title.strip():
        return True

    lowered = title.strip().lower()
    generic = {
        "google news",
        "news",
        "bài báo",
        "bai bao",
        "tin tức",
        "tin tuc",
    }
    return lowered in generic or "google news" in lowered


def _run_ocr_with_cache(image_bytes: bytes, mime_type: str) -> str:
    cached = get_cached_image_result(mode="ocr", image_bytes=image_bytes)
    if cached:
        return cached.result_text

    text_raw = gemini_ocr(image_bytes, mime_type=mime_type)
    text = clean_tts_text(text_raw)
    cache_image_result(mode="ocr", image_bytes=image_bytes, result_text=text)
    return text


def _run_caption_with_cache(image_bytes: bytes, mime_type: str) -> str:
    cached = get_cached_image_result(mode="caption", image_bytes=image_bytes)
    if cached:
        return cached.result_text

    caption_raw = gemini_caption(image_bytes, mime_type=mime_type)
    caption = clean_tts_text(caption_raw)
    cache_image_result(mode="caption", image_bytes=image_bytes, result_text=caption)
    return caption


def _find_duplicate_history(
    db: Session,
    *,
    user_id: int,
    action_type: str,
    image_sha256: str,
    image_dhash: str,
    limit: int = 40,
) -> Optional[HistoryDuplicateMatch]:
    candidates = (
        db.query(models.History)
        .filter(
            models.History.user_id == user_id,
            models.History.action_type == action_type,
        )
        .order_by(models.History.created_at.desc())
        .limit(limit)
        .all()
    )

    best_near: Optional[HistoryDuplicateMatch] = None
    best_distance: Optional[int] = None

    for item in candidates:
        if item.image_sha256 and item.image_sha256 == image_sha256:
            return HistoryDuplicateMatch(history=item, match_type="exact")

        if not item.image_dhash:
            continue

        distance = hamming_distance_hex(image_dhash, item.image_dhash)
        if distance > IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE:
            continue

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_near = HistoryDuplicateMatch(history=item, match_type="near")

    return best_near


def _save_history_once(
    db: Session,
    *,
    user: Optional[models.User],
    action_type: str,
    image_bytes: bytes,
    suffix: str,
    image_sha256: str,
    image_dhash: str,
    result_text: str,
) -> HistorySaveResult:
    if user is None:
        return HistorySaveResult(
            history_id=None,
            deduplicated=False,
            match_type=None,
            saved_to_history=False,
        )

    lock = _history_lock_for(user.id, action_type)

    with lock:
        duplicate = _find_duplicate_history(
            db,
            user_id=user.id,
            action_type=action_type,
            image_sha256=image_sha256,
            image_dhash=image_dhash,
        )
        if duplicate is not None:
            return HistorySaveResult(
                history_id=duplicate.history.id,
                deduplicated=True,
                match_type=duplicate.match_type,
                saved_to_history=False,
                existing_text=duplicate.history.result_text,
            )

        saved_path = save_upload_bytes(image_bytes, suffix)
        history = models.History(
            user_id=user.id,
            action_type=action_type,
            input_data=saved_path,
            result_text=result_text,
            image_sha256=image_sha256,
            image_dhash=image_dhash,
        )

        try:
            db.add(history)
            db.commit()
            db.refresh(history)
        except Exception:
            db.rollback()
            try:
                Path(saved_path).unlink(missing_ok=True)
            except Exception:
                pass
            raise

        return HistorySaveResult(
            history_id=history.id,
            deduplicated=False,
            match_type=None,
            saved_to_history=True,
        )


@app.get("/health", response_model=schemas.HealthResponse)
def health():
    return schemas.HealthResponse()


@app.post("/auth/register", response_model=schemas.AuthTokenResponse)
def register(req: schemas.RegisterRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    full_name = req.full_name.strip()
    phone = req.phone.strip()

    if not full_name:
        raise HTTPException(status_code=422, detail="Họ và tên không được để trống")

    if not phone:
        raise HTTPException(status_code=422, detail="Số điện thoại không được để trống")

    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email đã tồn tại")

    if db.query(models.User).filter(models.User.phone == phone).first():
        raise HTTPException(status_code=409, detail="Số điện thoại đã tồn tại")

    user = models.User(
        email=email,
        password_hash=hash_password(req.password),
        full_name=full_name,
        phone=phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=str(user.id))
    return schemas.AuthTokenResponse(user_id=user.id, access_token=token)


@app.post("/auth/login", response_model=schemas.AuthTokenResponse)
def login(req: schemas.LoginRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Sai email hoặc mật khẩu")

    token = create_access_token(subject=str(user.id))
    return schemas.AuthTokenResponse(user_id=user.id, access_token=token)


@app.get("/me", response_model=schemas.MeResponse)
def me(user: models.User = Depends(get_current_user_required)):
    return schemas.MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        created_at=user.created_at,
    )


@app.post("/ocr", response_model=schemas.UploadImageResponse)
async def ocr_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    suffix, mime_type, image_bytes = await read_upload_file(file)
    fingerprints = build_image_fingerprints(image_bytes)

    if user is not None:
      duplicate = _find_duplicate_history(
          db,
          user_id=user.id,
          action_type="ocr",
          image_sha256=fingerprints.canonical_sha256,
          image_dhash=fingerprints.dhash_hex,
      )
      if duplicate is not None:
          return schemas.UploadImageResponse(
              text=duplicate.history.result_text,
              history_id=duplicate.history.id,
              deduplicated=True,
              match_type=duplicate.match_type,
              saved_to_history=False,
          )

    try:
        text = _run_ocr_with_cache(image_bytes=image_bytes, mime_type=mime_type)
    except GeminiQuotaError as qe:
        _raise_quota_http(qe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR lỗi: {exc}")

    save_result = _save_history_once(
        db,
        user=user,
        action_type="ocr",
        image_bytes=image_bytes,
        suffix=suffix,
        image_sha256=fingerprints.canonical_sha256,
        image_dhash=fingerprints.dhash_hex,
        result_text=text,
    )

    final_text = save_result.existing_text or text
    return schemas.UploadImageResponse(
        text=final_text,
        history_id=save_result.history_id,
        deduplicated=save_result.deduplicated,
        match_type=save_result.match_type,
        saved_to_history=save_result.saved_to_history,
    )


@app.post("/caption", response_model=schemas.CaptionResponse)
async def caption_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    suffix, mime_type, image_bytes = await read_upload_file(file)
    fingerprints = build_image_fingerprints(image_bytes)

    if user is not None:
        duplicate = _find_duplicate_history(
            db,
            user_id=user.id,
            action_type="caption",
            image_sha256=fingerprints.canonical_sha256,
            image_dhash=fingerprints.dhash_hex,
        )
        if duplicate is not None:
            return schemas.CaptionResponse(
                caption=duplicate.history.result_text,
                history_id=duplicate.history.id,
                deduplicated=True,
                match_type=duplicate.match_type,
                saved_to_history=False,
            )

    try:
        caption = _run_caption_with_cache(image_bytes=image_bytes, mime_type=mime_type)
    except GeminiQuotaError as qe:
        _raise_quota_http(qe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Caption lỗi: {exc}")

    save_result = _save_history_once(
        db,
        user=user,
        action_type="caption",
        image_bytes=image_bytes,
        suffix=suffix,
        image_sha256=fingerprints.canonical_sha256,
        image_dhash=fingerprints.dhash_hex,
        result_text=caption,
    )

    final_caption = save_result.existing_text or caption
    return schemas.CaptionResponse(
        caption=final_caption,
        history_id=save_result.history_id,
        deduplicated=save_result.deduplicated,
        match_type=save_result.match_type,
        saved_to_history=save_result.saved_to_history,
    )


@app.post("/read/url", response_model=schemas.ReadUrlResponse)
def read_url(
    req: schemas.ReadUrlRequest,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL không hợp lệ")

    try:
        text_raw, title, resolved_url = extract_article_text_with_meta(url=url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không đọc được URL: {exc}")

    text_raw = (text_raw or "").strip()
    if not text_raw:
        raise HTTPException(status_code=422, detail="Không trích xuất được nội dung bài báo")

    if _looks_like_google_news_boilerplate(title, text_raw):
        raise HTTPException(
            status_code=422,
            detail="Không lấy được nội dung bài báo gốc từ Google News",
        )

    final_title: Optional[str] = None if _should_drop_generic_title(title) else title

    summary_raw: Optional[str] = None
    if req.summary:
        try:
            summary_raw = gemini_summarize_vi(text_raw)
        except GeminiQuotaError as qe:
            _raise_quota_http(qe)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Summarize lỗi: {exc}")

        if summary_raw and _looks_like_google_news_boilerplate(final_title, summary_raw):
            raise HTTPException(
                status_code=422,
                detail="Tóm tắt trả về chưa đúng nội dung bài báo gốc",
            )

    tts_text = clean_tts_text(text_raw)
    summary_tts = clean_tts_text(summary_raw) if summary_raw else None

    history_id = None
    if user:
        to_save = summary_tts or tts_text
        history = models.History(
            user_id=user.id,
            action_type="read_url",
            input_data=resolved_url or url,
            result_text=to_save,
        )
        db.add(history)
        db.commit()
        db.refresh(history)
        history_id = history.id

    return schemas.ReadUrlResponse(
        title=final_title,
        text=text_raw,
        tts_text=tts_text,
        summary=summary_raw,
        summary_tts=summary_tts,
        history_id=history_id,
    )


@app.get("/history", response_model=schemas.HistoryResponse)
def get_history(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
    type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    query = db.query(models.History).filter(models.History.user_id == user.id)

    if type:
        query = query.filter(models.History.action_type == type)

    items = query.order_by(models.History.created_at.desc()).limit(limit).all()
    return schemas.HistoryResponse(items=items)


@app.delete("/history/{history_id}")
def delete_history_item(
    history_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
):
    history = (
        db.query(models.History)
        .filter(models.History.id == history_id, models.History.user_id == user.id)
        .first()
    )
    if not history:
        raise HTTPException(status_code=404, detail="Không tìm thấy history")

    image_path = history.input_data if history.action_type in {"ocr", "caption"} else None

    db.delete(history)
    db.commit()

    if image_path:
        try:
            normalized = image_path.replace("\\", "/")
            if normalized.startswith("uploads/"):
                Path(normalized).unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True}