from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import models, schemas
from .config import MAX_UPLOAD_BYTES, UPLOAD_DIR as UPLOAD_DIR_SETTING
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
from .services.image_cache import cache_image_result, get_cached_image_result
from .services.text_clean import clean_tts_text
from .services.web_extract import extract_article_text_with_meta

app = FastAPI(title="TalkSight API")
app.include_router(news_router)

# ===== DB init =====
Base.metadata.create_all(bind=engine)

# ===== CORS =====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Uploads =====
UPLOAD_DIR = Path(UPLOAD_DIR_SETTING)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

bearer_scheme = HTTPBearer(auto_error=False)


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


async def save_upload_file(file: UploadFile) -> tuple[str, bytes]:
    """
    Lưu file vào uploads/ và trả về (saved_path, bytes)
    """
    suffix = Path(file.filename or "").suffix or ".jpg"
    name = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / name

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="File rỗng")

    if len(content) > MAX_UPLOAD_BYTES:
        max_mb = round(MAX_UPLOAD_BYTES / (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"File quá lớn (>{max_mb}MB)")

    dest.write_bytes(content)
    return str(dest), content


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
    matched = sum(1 for s in signals if s in hay)
    return matched >= 2


def _should_drop_generic_title(title: Optional[str]) -> bool:
    if not title or not title.strip():
        return True

    t = title.strip().lower()
    generic = {
        "google news",
        "news",
        "bài báo",
        "bai bao",
        "tin tức",
        "tin tuc",
    }

    if t in generic:
        return True
    if "google news" in t:
        return True
    return False


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


@app.get("/health", response_model=schemas.HealthResponse)
def health():
    return schemas.HealthResponse()


# ===== AUTH =====
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


# ===== CORE: OCR =====
@app.post("/ocr", response_model=schemas.UploadImageResponse)
async def ocr_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    saved_path, image_bytes = await save_upload_file(file)
    mime_type = file.content_type or "image/jpeg"

    try:
        text = _run_ocr_with_cache(image_bytes=image_bytes, mime_type=mime_type)
    except GeminiQuotaError as qe:
        _raise_quota_http(qe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR lỗi: {e}")

    history_id = None
    if user:
        h = models.History(
            user_id=user.id,
            action_type="ocr",
            input_data=saved_path,
            result_text=text,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id

    return schemas.UploadImageResponse(text=text, history_id=history_id)


# ===== CORE: CAPTION =====
@app.post("/caption", response_model=schemas.CaptionResponse)
async def caption_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    saved_path, image_bytes = await save_upload_file(file)
    mime_type = file.content_type or "image/jpeg"

    try:
        caption = _run_caption_with_cache(image_bytes=image_bytes, mime_type=mime_type)
    except GeminiQuotaError as qe:
        _raise_quota_http(qe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Caption lỗi: {e}")

    history_id = None
    if user:
        h = models.History(
            user_id=user.id,
            action_type="caption",
            input_data=saved_path,
            result_text=caption,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id

    return schemas.CaptionResponse(caption=caption, history_id=history_id)


# ===== CORE: READ URL =====
@app.post("/read/url", response_model=schemas.ReadUrlResponse)
def read_url(
    req: schemas.ReadUrlRequest,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL không hợp lệ")

    # 1) extract + resolve Google News -> bài gốc
    try:
        text_raw, title, resolved_url = extract_article_text_with_meta(url=url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không đọc được URL: {e}")

    text_raw = (text_raw or "").strip()
    if not text_raw:
        raise HTTPException(status_code=422, detail="Không trích xuất được nội dung bài báo")

    if _looks_like_google_news_boilerplate(title, text_raw):
        raise HTTPException(
            status_code=422,
            detail="Không lấy được nội dung bài báo gốc từ Google News",
        )

    final_title: Optional[str] = None if _should_drop_generic_title(title) else title

    # 2) summarize
    summary_raw: Optional[str] = None
    if req.summary:
        try:
            summary_raw = gemini_summarize_vi(text_raw)
        except GeminiQuotaError as qe:
            _raise_quota_http(qe)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Summarize lỗi: {e}")

        if summary_raw and _looks_like_google_news_boilerplate(final_title, summary_raw):
            raise HTTPException(
                status_code=422,
                detail="Tóm tắt trả về chưa đúng nội dung bài báo gốc",
            )

    # 3) clean for TTS
    tts_text = clean_tts_text(text_raw)
    summary_tts = clean_tts_text(summary_raw) if summary_raw else None

    # 4) save history
    history_id = None
    if user:
        to_save = summary_tts or tts_text
        h = models.History(
            user_id=user.id,
            action_type="read_url",
            input_data=resolved_url or url,
            result_text=to_save,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id

    return schemas.ReadUrlResponse(
        title=final_title,
        text=text_raw,
        tts_text=tts_text,
        summary=summary_raw,
        summary_tts=summary_tts,
        history_id=history_id,
    )


# ===== HISTORY =====
@app.get("/history", response_model=schemas.HistoryResponse)
def get_history(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
    type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    q = db.query(models.History).filter(models.History.user_id == user.id)

    if type:
        q = q.filter(models.History.action_type == type)

    items = q.order_by(models.History.created_at.desc()).limit(limit).all()
    return schemas.HistoryResponse(items=items)


@app.delete("/history/{history_id}")
def delete_history_item(
    history_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
):
    h = (
        db.query(models.History)
        .filter(models.History.id == history_id, models.History.user_id == user.id)
        .first()
    )
    if not h:
        raise HTTPException(status_code=404, detail="Không tìm thấy history")

    db.delete(h)
    db.commit()
    return {"ok": True}