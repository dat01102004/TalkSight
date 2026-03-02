import re
from html import unescape


_VI_CHARS = r"0-9A-Za-zÀ-ỹ"


def clean_tts_text(s: str) -> str:
    if not s:
        return ""

    s = unescape(s)

    # 1) literal escapes -> real chars
    s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")

    # 2) normalize newlines
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # 3) remove zero-width / bidi chars
    s = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]", "", s)

    # 4) remove common markdown noise
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)  # **bold**
    s = re.sub(r"\*(.*?)\*", r"\1", s)      # *italic*
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)  # [text](url)

    # 5) remove news labels / image credits
    s = re.sub(r"^\s*\([A-Za-z0-9._-]{2,10}\)\s*-\s*", "", s)  # (PLO)- ...
    s = re.sub(r"^\s*Ảnh\s*:\s*.*$", "", s, flags=re.IGNORECASE | re.MULTILINE)

    # 6) IMPORTANT: collapse spaces around newlines
    # " \n \n  " -> "\n\n"
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n[ \t]+", "\n", s)

    # 7) Fix broken words/phrases where newline comes after a space: "đo \nnồng" -> "đo nồng"
    # We convert " \n" between letters to a space.
    s = re.sub(rf"(?<=[{_VI_CHARS}])\s*\n\s*(?=[{_VI_CHARS}])", " ", s)

    # 8) Collapse too many blank lines
    s = re.sub(r"\n{3,}", "\n\n", s)

    # 9) Normalize spaces (keep \n for now)
    s = re.sub(r"[ \t\f\v]+", " ", s)

    # 10) Turn newlines into TTS-friendly pauses
    # - paragraph break -> ". "
    # - single newline -> " "
    s = s.replace("\n\n", ". ")
    s = s.replace("\n", " ")

    # 11) Fix punctuation spacing
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)            # no space before punct
    s = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", s)     # ensure space after punct

    # 12) Clean extra spaces
    s = re.sub(r"\s{2,}", " ", s).strip()

    return s