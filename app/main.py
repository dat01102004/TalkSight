from datetime import datetime, timezone
from pathlib import Path
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db import engine, get_db
from app import models
from app.config import UPLOAD_DIR, MAX_UPLOAD_BYTES  # nếu bạn có
from app.schemas import (
    RegisterRequest,
    LoginRequest,
    AuthResponse,
    OCRResponse,
    CaptionResponse,
    ReadUrlRequest,
    ReadUrlResponse,
    HistoryListResponse,
    HistoryItem,
)
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_optional_user,
)

# Services
from app.services.gemini_service import gemini_ocr, gemini_caption, gemini_read_url

# Tạo bảng DB
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="TalkSight Backend", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": "TalkSight Backend",
        "time": datetime.now(timezone.utc),
    }


# -------------------------
# AUTH
# -------------------------
@app.post("/auth/register", response_model=AuthResponse)
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

        token = create_access_token({"sub": str(user.id)})
        return AuthResponse(user_id=user.id, access_token=token, token_type="bearer")

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email đã tồn tại")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Register lỗi: {type(e).__name__}: {e}")


@app.post("/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = (payload.password or "").strip()

    if not email or not password:
        raise HTTPException(status_code=422, detail="Thiếu email hoặc password")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Sai email hoặc mật khẩu")

    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Sai email hoặc mật khẩu")

    token = create_access_token({"sub": str(user.id)})
    return AuthResponse(user_id=user.id, access_token=token, token_type="bearer")


# -------------------------
# UPLOAD IMAGE (nếu bạn có endpoint upload)
# -------------------------
@app.post("/upload-image")
def upload_image(file: UploadFile = File(...)):
    # tạo thư mục upload
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # kiểm size (optional)
    content = file.file.read()
    if MAX_UPLOAD_BYTES and len(content) > int(MAX_UPLOAD_BYTES):
        raise HTTPException(status_code=413, detail="File quá lớn")
    file.file.seek(0)

    ext = Path(file.filename).suffix.lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = Path(UPLOAD_DIR) / filename

    with open(save_path, "wb") as f:
        f.write(content)

    return {"image_path": str(save_path).replace("\\", "/")}


# -------------------------
# OCR / CAPTION / READ_URL
# Nếu có token -> tự lưu history theo user
# Nếu không có token -> vẫn chạy, history.user_id = None
# -------------------------
@app.post("/ocr", response_model=OCRResponse)
def ocr_endpoint(
    image_path: str,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    text = gemini_ocr(image_path)

    history_id = None
    try:
        h = models.History(
            user_id=user.id if user else None,
            action_type="ocr",
            input_data=image_path,
            result_text=text,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id
    except Exception:
        db.rollback()

    return OCRResponse(text=text, history_id=history_id)


@app.post("/caption", response_model=CaptionResponse)
def caption_endpoint(
    image_path: str,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    text = gemini_caption(image_path)

    history_id = None
    try:
        h = models.History(
            user_id=user.id if user else None,
            action_type="caption",
            input_data=image_path,
            result_text=text,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id
    except Exception:
        db.rollback()

    return CaptionResponse(text=text, history_id=history_id)


@app.post("/read/url", response_model=ReadUrlResponse)
def read_url_endpoint(
    payload: ReadUrlRequest,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    # gemini_read_url nên trả dict: {"text":..., "summary":..., "title":...}
    result = gemini_read_url(payload.url, summary=payload.summary)

    text = result.get("text") or ""
    summary = result.get("summary")
    title = result.get("title")

    history_id = None
    try:
        h = models.History(
            user_id=user.id if user else None,
            action_type="read_url",
            input_data=payload.url,
            result_text=text if not summary else f"{text}\n\n---SUMMARY---\n{summary}",
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        history_id = h.id
    except Exception:
        db.rollback()

    return ReadUrlResponse(text=text, summary=summary, title=title, history_id=history_id)


# -------------------------
# HISTORY (BẮT BUỘC LOGIN)
# -------------------------
@app.get("/history", response_model=HistoryListResponse)
def get_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items = (
        db.query(models.History)
        .filter(models.History.user_id == current_user.id)
        .order_by(models.History.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return HistoryListResponse(items=items)


@app.delete("/history/{history_id}")
def delete_history(
    history_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    h = (
        db.query(models.History)
        .filter(models.History.id == history_id)
        .first()
    )
    if not h:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử")

    if h.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xoá mục này")

    db.delete(h)
    db.commit()
    return {"ok": True}
