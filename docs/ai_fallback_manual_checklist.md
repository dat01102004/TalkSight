# AI fallback manual checklist

- Gemini works normally: set `AI_PROVIDER=gemini`, valid `GEMINI_API_KEY`, valid `OPENAI_API_KEY`; call `/caption` and `/read/url`; verify logs show `AI provider used: gemini` and OpenAI is not called.
- Gemini quota/rate limit: force Gemini to raise a 429/quota/`ResourceExhausted` error; verify logs show `provider_first=gemini provider_fallback=openai reason=...` and the API response schema stays unchanged.
- Gemini timeout: force a timeout/connection error from Gemini; verify OpenAI fallback returns the final caption or summary.
- Both providers fail: use invalid/missing OpenAI key after forcing Gemini retryable failure; verify the API returns `503` with `Dịch vụ AI hiện không phản hồi, vui lòng thử lại sau.` and no API key appears in logs.
- Flutter compatibility: call `/caption` and confirm the response still uses `caption`; call `/read/url` and confirm `title`, `text`, `tts_text`, `summary`, `summary_tts`, and `history_id` are unchanged.
- URL summary path: call `/read/url` with `{"summary": true}` and verify fallback applies only to summary generation, not article extraction.
