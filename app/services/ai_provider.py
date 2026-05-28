from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import HTTPException
from httpx import RequestError, TimeoutException

from app.config import AI_FALLBACK_ENABLED, AI_PROVIDER, OPENAI_API_KEY
from app.services.gemini_service import GeminiQuotaError, gemini_caption, gemini_generate_text
from app.services.openai_service import describe_image_with_openai, generate_text_with_openai


logger = logging.getLogger(__name__)

FRIENDLY_AI_UNAVAILABLE_MESSAGE = "Dịch vụ AI hiện không phản hồi, vui lòng thử lại sau."


def is_retryable_ai_error(error: Exception) -> bool:
    if isinstance(error, (GeminiQuotaError, TimeoutError, TimeoutException, RequestError, ConnectionError)):
        return True

    msg = str(error).lower()
    retryable_signals = (
        "quota exceeded",
        "rate limit",
        "429",
        "resourceexhausted",
        "resource_exhausted",
        "toomanyrequests",
        "too many requests",
        "timeout",
        "timed out",
        "connection error",
        "connection reset",
        "500",
        "502",
        "503",
        "504",
        "internal server error",
        "service unavailable",
        "unavailable",
        "high demand",
    )
    return any(signal in msg for signal in retryable_signals)


def _fallback_allowed(error: Exception) -> bool:
    return AI_FALLBACK_ENABLED and is_retryable_ai_error(error)


def _log_final_provider(provider: str) -> None:
    logger.info("AI provider used: %s", provider)


async def describe_image_with_fallback(
    image_bytes: bytes,
    mime_type: str,
    prompt: Optional[str] = None,
) -> str:
    if AI_PROVIDER == "openai":
        try:
            result = await describe_image_with_openai(image_bytes, mime_type, prompt)
            _log_final_provider("openai")
            return result
        except Exception as exc:
            logger.exception("OpenAI image description failed")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from exc

    try:
        result = await asyncio.to_thread(gemini_caption, image_bytes, mime_type)
        _log_final_provider("gemini")
        return result
    except Exception as gemini_error:
        if not _fallback_allowed(gemini_error):
            logger.exception("Gemini image description failed without retryable fallback")
            raise

        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is missing, fallback disabled at runtime")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from gemini_error

        logger.warning(
            "AI fallback triggered: provider_first=gemini provider_fallback=openai reason=%s",
            gemini_error,
        )
        try:
            result = await describe_image_with_openai(image_bytes, mime_type, prompt)
            _log_final_provider("openai")
            return result
        except Exception as openai_error:
            logger.exception("OpenAI image description fallback failed")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from openai_error


async def generate_text_with_fallback(prompt: str) -> str:
    if AI_PROVIDER == "openai":
        try:
            result = await generate_text_with_openai(prompt)
            _log_final_provider("openai")
            return result
        except Exception as exc:
            logger.exception("OpenAI text generation failed")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from exc

    try:
        result = await asyncio.to_thread(gemini_generate_text, prompt)
        _log_final_provider("gemini")
        return result
    except Exception as gemini_error:
        if not _fallback_allowed(gemini_error):
            logger.exception("Gemini text generation failed without retryable fallback")
            raise

        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is missing, fallback disabled at runtime")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from gemini_error

        logger.warning(
            "AI fallback triggered: provider_first=gemini provider_fallback=openai reason=%s",
            gemini_error,
        )
        try:
            result = await generate_text_with_openai(prompt)
            _log_final_provider("openai")
            return result
        except Exception as openai_error:
            logger.exception("OpenAI text generation fallback failed")
            raise HTTPException(status_code=503, detail=FRIENDLY_AI_UNAVAILABLE_MESSAGE) from openai_error
