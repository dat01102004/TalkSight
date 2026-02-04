# app/security.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from jose import jwt, JWTError, ExpiredSignatureError
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===== JWT CONFIG =====
# Khi deploy thật: đưa vào .env
JWT_SECRET_KEY = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1 giờ


def _validate_password_for_bcrypt(password: str) -> None:
    # bcrypt giới hạn 72 bytes (không phải 72 ký tự)
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=422,
            detail="Mật khẩu quá dài (bcrypt giới hạn 72 bytes). Hãy dùng mật khẩu ngắn hơn.",
        )


def hash_password(password: str) -> str:
    password = (password or "").strip()
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="Mật khẩu tối thiểu 6 ký tự")
    _validate_password_for_bcrypt(password)
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    plain_password = (plain_password or "").strip()
    _validate_password_for_bcrypt(plain_password)
    try:
        return pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def create_access_token(
    subject: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    subject: thường là user_id dạng string, lưu vào claim "sub"
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)

    payload: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Trả payload nếu hợp lệ, raise 401 nếu invalid/expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Token không hợp lệ (thiếu sub)")
        return payload
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token đã hết hạn")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token không hợp lệ")
