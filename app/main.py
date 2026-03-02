# app/main.py
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from . import models, schemas
from .security import hash_password, verify_password, create_access_token, decode_access_token

from .services.gemini_service import gemini_ocr, gemini_caption, gemini_summarize_vi
from .services.web_extract import extract_article_text
from .services.text_clean import clean_tts_text

app = FastAPI(title="TalkSight API")

# ===== DB init =====
Base.metadata.create_all(bind=engine)

# ===== CORS =====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Uploads =====
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

MAX_UPLOAD_MB = 10
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

    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File quá lớn (>{MAX_UPLOAD_MB}MB)")

    dest.write_bytes(content)
    return str(dest), content


@app.get("/health", response_model=schemas.HealthResponse)
def health():
    return schemas.HealthResponse()


# ===== AUTH =====
@app.post("/auth/register", response_model=schemas.AuthTokenResponse)
def register(req: schemas.RegisterRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email đã tồn tại")

    user = models.User(email=email, password_hash=hash_password(req.password))
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
    return schemas.MeResponse(id=user.id, email=user.email, created_at=user.created_at)


# ===== CORE: OCR (Guest OK, Login -> save history) =====
@app.post("/ocr", response_model=schemas.UploadImageResponse)
async def ocr_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    saved_path, image_bytes = await save_upload_file(file)

    mime_type = file.content_type or "image/jpeg"
    text_raw = gemini_ocr(image_bytes, mime_type=mime_type)
    text = clean_tts_text(text_raw)  # ✅ clean để TTS đọc mượt

    history_id = None
    if user:
        h = models.History(
            user_id=user.id,
            action_type="ocr",
            input_data=saved_path,
            result_text=text,  # lưu bản clean luôn
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id

    return schemas.UploadImageResponse(text=text, history_id=history_id)


# ===== CORE: CAPTION (Guest OK, Login -> save history) =====
@app.post("/caption", response_model=schemas.CaptionResponse)
async def caption_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(get_current_user_optional),
):
    saved_path, image_bytes = await save_upload_file(file)

    mime_type = file.content_type or "image/jpeg"
    caption_raw = gemini_caption(image_bytes, mime_type=mime_type)
    caption = clean_tts_text(caption_raw)  # ✅ clean

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


# ===== CORE: READ URL (Guest OK, Login -> save history) =====
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
        text_raw, title = extract_article_text(url=url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không đọc được URL: {e}")

    summary_raw = gemini_summarize_vi(text_raw) if req.summary else None

    # ✅ clean cho TTS
    tts_text = clean_tts_text(text_raw)
    summary_tts = clean_tts_text(summary_raw) if summary_raw else None

    history_id = None
    if user:
        # lưu text TTS hoặc summary_tts để đọc cho dễ
        to_save = summary_tts or tts_text
        h = models.History(
            user_id=user.id,
            action_type="read_url",
            input_data=url,
            result_text=to_save,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id

    return schemas.ReadUrlResponse(
        title=title,
        text=text_raw,
        tts_text=tts_text,
        summary=summary_raw,
        summary_tts=summary_tts,
        history_id=history_id,
    )


# ===== HISTORY: chỉ login mới được xem/xóa =====
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