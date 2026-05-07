from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    email: str
    phone: str = Field(..., min_length=8, max_length=20)
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateMeRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    email: str
    phone: str = Field(..., min_length=8, max_length=20)


class AuthTokenResponse(BaseModel):
    user_id: int
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime


class SettingBase(BaseModel):
    voice: Optional[str] = None
    rate: float = 1.0
    volume: float = 1.0
    language: str = "vi-VN"


class SettingCreate(SettingBase):
    pass


class SettingUpdate(BaseModel):
    voice: Optional[str] = None
    rate: Optional[float] = None
    volume: Optional[float] = None
    language: Optional[str] = None


class SettingOut(SettingBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


SettingResponse = SettingOut


class TtsSpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=12000)
    voice: Optional[str] = None
    rate: Optional[float] = None
    volume: Optional[float] = None
    language: Optional[str] = None


class TtsSpeakResponse(BaseModel):
    audio_url: str
    file_path: str
    voice: str
    rate: float
    volume: float
    language: str


class UploadImageResponse(BaseModel):
    text: str
    history_id: Optional[int] = None
    deduplicated: bool = False
    match_type: Optional[str] = None
    saved_to_history: bool = False


class CaptionResponse(BaseModel):
    caption: str
    history_id: Optional[int] = None
    deduplicated: bool = False
    match_type: Optional[str] = None
    saved_to_history: bool = False


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


class NewsItem(BaseModel):
    title: str
    link: str
    source: Optional[str] = None
    published: Optional[str] = None


class NewsResponse(BaseModel):
    items: List[NewsItem] = Field(default_factory=list)


class HistoryResponse(BaseModel):
    items: List[HistoryItem] = Field(default_factory=list)
