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


class AuthTokenResponse(BaseModel):
    user_id: int
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    email: str
    created_at: datetime


class UploadImageResponse(BaseModel):
    text: str
    history_id: Optional[int] = None


class CaptionResponse(BaseModel):
    caption: str
    history_id: Optional[int] = None


class ReadUrlRequest(BaseModel):
    url: str
    summary: bool = True


class ReadUrlResponse(BaseModel):
    title: Optional[str] = None
    text: str
    tts_text: Optional[str] = None
    summary: Optional[str] = None
    summary_tts: Optional[str] = None
    history_id: Optional[int] = None


class HistoryItem(BaseModel):
    id: int
    user_id: int
    action_type: str
    input_data: str
    result_text: str
    created_at: datetime

    model_config = {"from_attributes": True}




class HistoryResponse(BaseModel):
    items: List[HistoryItem] = Field(default_factory=list)