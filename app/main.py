
# app/main.py
from __future__ import annotations

from datetime import datetime
import inspect
import uuid

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app import models
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)
from app.services.gemini_service import gemini_ocr, gemini_caption, gemini_read_url
from app.schemas import (
    RegisterRequest,
    LoginRequest,
    AuthTokenResponse,
    HealthResponse,
    UploadImageResponse,
    CaptionResponse,
    ReadUrlRequest,
    ReadUrlResponse,
    HistoryItem,
    HistoryResponse,
)

app = FastAPI(title="TalkSight Backend", version="0.3.0")

# CORS (cho Flutter / Web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Swagger "Authorize" dạng dán Bearer token =====
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """
    Lấy user từ Bearer token:
    Header: Authorization: Bearer <access_token>
    """
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Thiếu Bearer token")

    token = creds.credentials.strip()
    payload = decode_access_token(token)  # phải raise 401 nếu invalid/expired

    # payload nên chứa "sub" = user_id (chuẩn JWT)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token không hợp lệ (thiếu sub)")

    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User không tồn tại")
    return user


# =========================
# Health
# =========================
@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


# =========================
# Auth
# =========================
@app.post("/auth/register", response_model=AuthTokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = (payload.password or "").strip()

    if "@" not in email or "." not in email:
        raise HTTPException(status_code=422, detail="Email không hợp lệ (vd: abc@gmail.com)")
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="Mật khẩu tối thiểu 6 ký tự")

    try:
        existing = db.query(models.User).filter(models.User.email == email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email đã tồn tại")

        user = models.User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)

        # ✅ trả token luôn (đỡ phải login lần nữa)
        access_token = create_access_token(subject=str(user.id))
        return AuthTokenResponse(user_id=user.id, access_token=access_token, token_type="bearer")

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email đã tồn tại")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Register lỗi: {type(e).__name__}: {e}")


@app.post("/auth/login", response_model=AuthTokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = (payload.password or "").strip()

    if not email or not password:
        raise HTTPException(status_code=422, detail="Thiếu email hoặc mật khẩu")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Sai email hoặc mật khẩu")

    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Sai email hoặc mật khẩu")

    access_token = create_access_token(subject=str(user.id))
    return AuthTokenResponse(user_id=user.id, access_token=access_token, token_type="bearer")


# =========================
# OCR / Caption
# =========================
@app.post("/ocr", response_model=UploadImageResponse)
async def ocr_image(
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
):
    # tuỳ bạn xử lý lưu file hay đọc bytes luôn
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="File rỗng")

    result_text = gemini_ocr(content)  # bạn đang có sẵn
    return UploadImageResponse(text=result_text)


@app.post("/caption", response_model=CaptionResponse)
async def caption_image(
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="File rỗng")

    caption = gemini_caption(content)
    return CaptionResponse(caption=caption)


# =========================
# Read URL + History
# =========================
@app.post("/read/url", response_model=ReadUrlResponse)
def read_url(
    payload: ReadUrlRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    url = (payload.url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=422, detail="URL không hợp lệ (phải bắt đầu bằng http/https)")

    try:
        text, summ, title = gemini_read_url(url=url, want_summary=payload.summary)
    except Exception as e:
        # fallback: vẫn trả được text (đỡ 500)
        try:
            text, title = extract_article_text(url=url)
            summ = None
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Lỗi đọc URL: {type(e2).__name__}: {e2}")

    h = models.History(
        user_id=user.id,
        action_type="read_url",
        input_data=url,
        result_text=(summ if (payload.summary and summ) else text),
    )
    db.add(h)
    db.commit()
    db.refresh(h)

    return ReadUrlResponse(title=title, text=text, summary=summ, history_id=h.id)


@app.get("/history", response_model=HistoryResponse)
def get_history(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    limit: int = 50,
):
    limit = max(1, min(limit, 200))
    rows = (
        db.query(models.History)
        .filter(models.History.user_id == user.id)
        .order_by(models.History.created_at.desc())
        .limit(limit)
        .all()
    )

    items = [
        HistoryItem(
            id=r.id,
            user_id=r.user_id,
            action_type=r.action_type,
            input_data=r.input_data,
            result_text=r.result_text,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return HistoryResponse(items=items)


@app.delete("/history/{history_id}")
def delete_history(
    history_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = (
        db.query(models.History)
        .filter(models.History.id == history_id, models.History.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy history hoặc không có quyền xoá")

    db.delete(row)
    db.commit()
    return {"deleted": True, "id": history_id}
