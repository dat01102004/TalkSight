from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: int
    access_token: str
    token_type: str = "bearer"


class HistoryItem(BaseModel):
    id: int
    user_id: Optional[int]
    action_type: str
    input_data: str
    result_text: str
    created_at: datetime

    class Config:
        from_attributes = True


class HistoryListResponse(BaseModel):
    items: List[HistoryItem]


# (Nếu bạn đang dùng các response khác: OCRResponse/CaptionResponse/ReadUrlResponse
# thì giữ nguyên hoặc bổ sung bên dưới theo code main.py của bạn.)
class OCRResponse(BaseModel):
    text: str
    history_id: Optional[int] = None


class CaptionResponse(BaseModel):
    text: str
    history_id: Optional[int] = None


class ReadUrlRequest(BaseModel):
    url: str
    summary: bool = True


class ReadUrlResponse(BaseModel):
    text: str
    summary: Optional[str] = None
    title: Optional[str] = None
    history_id: Optional[int] = None
