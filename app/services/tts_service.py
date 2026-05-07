from __future__ import annotations

import uuid
from pathlib import Path

import edge_tts

from app.config import (
    TTS_DEFAULT_FEMALE_VOICE,
    TTS_DEFAULT_LANGUAGE,
    TTS_OUTPUT_DIR,
)


DEFAULT_TTS_RATE = 1.0
DEFAULT_TTS_VOLUME = 1.0


def normalize_tts_voice(voice: str | None, language: str | None = None) -> str:
    value = (voice or "").strip()
    if value:
        return value

    lang = (language or TTS_DEFAULT_LANGUAGE).strip()
    if lang.lower().startswith("vi"):
        return TTS_DEFAULT_FEMALE_VOICE

    return TTS_DEFAULT_FEMALE_VOICE


def normalize_tts_language(language: str | None) -> str:
    value = (language or "").strip()
    return value or TTS_DEFAULT_LANGUAGE


def normalize_tts_rate(rate: float | None) -> float:
    if rate is None:
        return DEFAULT_TTS_RATE
    return min(max(float(rate), 0.5), 2.0)


def normalize_tts_volume(volume: float | None) -> float:
    if volume is None:
        return DEFAULT_TTS_VOLUME
    return min(max(float(volume), 0.0), 1.0)


def edge_rate_value(rate: float) -> str:
    percent = round((rate - 1.0) * 100)
    return f"{percent:+d}%"


def edge_volume_value(volume: float) -> str:
    percent = round((volume - 1.0) * 100)
    return f"{percent:+d}%"


async def synthesize_speech_to_mp3(
    *,
    text: str,
    voice: str | None = None,
    rate: float | None = None,
    volume: float | None = None,
    language: str | None = None,
) -> tuple[Path, str, float, float, str]:
    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Text không được để trống")

    final_language = normalize_tts_language(language)
    final_voice = normalize_tts_voice(voice, final_language)
    final_rate = normalize_tts_rate(rate)
    final_volume = normalize_tts_volume(volume)

    output_dir = Path(TTS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{uuid.uuid4().hex}.mp3"

    communicate = edge_tts.Communicate(
        clean_text,
        voice=final_voice,
        rate=edge_rate_value(final_rate),
        volume=edge_volume_value(final_volume),
    )
    await communicate.save(str(output_path))

    return output_path, final_voice, final_rate, final_volume, final_language
