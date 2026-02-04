# app/schemas.py
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ✅ Đồng bộ main.py: register/login đều trả token
class AuthTokenResponse(BaseModel):
    user_id: int
    access_token: str
    token_type: str = "bearer"


class UploadImageResponse(BaseModel):
    text: str


class CaptionResponse(BaseModel):
    caption: str


class ReadUrlRequest(BaseModel):
    url: str
    summary: bool = True


class ReadUrlResponse(BaseModel):
    title: Optional[str] = None
    text: str
    summary: Optional[str] = None
    history_id: Optional[int] = None


class HistoryItem(BaseModel):
    id: int
    user_id: int
    action_type: str
    input_data: str
    result_text: str
    created_at: datetime

    class Config:
        from_attributes = True  # pydantic v2
        orm_mode = True         # fallback pydantic v1


class HistoryResponse(BaseModel):
    items: List[HistoryItem] = Field(default_factory=list)
