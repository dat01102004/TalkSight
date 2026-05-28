from __future__ import annotations

import base64
from typing import Any, Optional

import httpx

from app.config import AI_TIMEOUT_SECONDS, OPENAI_API_KEY, OPENAI_FALLBACK_MODEL


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_IMAGE_PROMPT = (
    "Bạn là trợ lý hỗ trợ người khiếm thị. Hãy mô tả nội dung ảnh bằng tiếng Việt "
    "rõ ràng, ngắn gọn, dễ nghe khi chuyển thành giọng nói. Tập trung vào vật thể "
    "chính, bối cảnh, chữ nếu nhìn thấy, và thông tin quan trọng."
)


def _require_openai_api_key() -> None:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")


def _extract_text_from_content(content: Any) -> Optional[str]:
    if isinstance(content, str):
        return content.strip() or None

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip() or None

    return None


def _parse_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output")
    if isinstance(output, list):
        collected: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue

            content_text = _extract_text_from_content(item.get("content"))
            if content_text:
                collected.append(content_text)
                continue

            text = item.get("text")
            if isinstance(text, str) and text.strip():
                collected.append(text.strip())

        if collected:
            return "\n".join(collected).strip()

    raise RuntimeError("OpenAI response did not contain text output")


async def _post_responses(payload: dict[str, Any]) -> str:
    _require_openai_api_key()

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=AI_TIMEOUT_SECONDS) as client:
            response = await client.post(OPENAI_RESPONSES_URL, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        detail = exc.response.text[:500] if exc.response is not None else ""
        raise RuntimeError(f"OpenAI Responses API error {status_code}: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError("OpenAI Responses API timeout") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"OpenAI Responses API connection error: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("OpenAI Responses API returned invalid JSON") from exc

    return _parse_response_text(data)


async def describe_image_with_openai(
    image_bytes: bytes,
    mime_type: str,
    prompt: str | None = None,
) -> str:
    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    image_url = f"data:{mime_type};base64,{encoded_image}"

    payload = {
        "model": OPENAI_FALLBACK_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt or DEFAULT_IMAGE_PROMPT,
                    },
                    {
                        "type": "input_image",
                        "image_url": image_url,
                    },
                ],
            }
        ],
    }
    return await _post_responses(payload)


async def generate_text_with_openai(prompt: str) -> str:
    payload = {
        "model": OPENAI_FALLBACK_MODEL,
        "input": prompt,
    }
    return await _post_responses(payload)
