from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import UPLOAD_DIR
from app.db import get_db
from app.deps import get_current_user_optional
from app.services.tts_service import synthesize_speech_to_mp3


router = APIRouter(prefix="/tts", tags=["tts"])


def _settings_for_user(db: Session, user: models.User | None) -> models.Setting | None:
    if user is None:
        return None
    return db.query(models.Setting).filter(models.Setting.user_id == user.id).first()


def _audio_url_for_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path(UPLOAD_DIR).resolve())
        return f"/uploads/{rel.as_posix()}"
    except Exception:
        return f"/uploads/tts/{path.name}"


@router.post("/speak", response_model=schemas.TtsSpeakResponse)
async def speak_text(
    req: schemas.TtsSpeakRequest,
    db: Session = Depends(get_db),
    user: models.User | None = Depends(get_current_user_optional),
):
    settings = _settings_for_user(db, user)

    voice = req.voice if req.voice is not None else (settings.voice if settings else None)
    rate = req.rate if req.rate is not None else (settings.rate if settings else None)
    volume = req.volume if req.volume is not None else (settings.volume if settings else None)
    language = req.language if req.language is not None else (settings.language if settings else None)

    try:
        output_path, final_voice, final_rate, final_volume, final_language = await synthesize_speech_to_mp3(
            text=req.text,
            voice=voice,
            rate=rate,
            volume=volume,
            language=language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS lỗi: {exc}")

    return schemas.TtsSpeakResponse(
        audio_url=_audio_url_for_path(output_path),
        file_path=str(output_path),
        voice=final_voice,
        rate=final_rate,
        volume=final_volume,
        language=final_language,
    )
