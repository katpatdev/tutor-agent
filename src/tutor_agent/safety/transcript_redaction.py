"""Deterministic local redaction for consented transcript text."""

from __future__ import annotations

import re
from dataclasses import dataclass


class RedactionError(Exception):
    """Raised when text cannot be safely prepared for persistence."""


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.IGNORECASE)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-.]?\d{4}(?!\w)"
)
_API_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b"
)
_LONG_ID = re.compile(r"\b\d{10,}\b")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    character_count: int
    truncated: bool


def redact_text(text: str, *, max_characters: int) -> RedactionResult:
    if max_characters < 1:
        raise RedactionError("max_characters must be >= 1")
    if not isinstance(text, str):
        raise RedactionError("text must be a string")

    redacted = text
    redacted = _URL.sub("[URL]", redacted)
    redacted = _EMAIL.sub("[EMAIL]", redacted)
    redacted = _IPV6.sub("[IP_ADDRESS]", redacted)
    redacted = _IPV4.sub("[IP_ADDRESS]", redacted)
    redacted = _API_KEY.sub("[SECRET]", redacted)
    # Long digit runs before phone patterns so 12+ digit IDs are not phone-shaped.
    redacted = _LONG_ID.sub("[IDENTIFIER]", redacted)
    redacted = _PHONE.sub("[PHONE]", redacted)

    truncated = False
    if len(redacted) > max_characters:
        redacted = redacted[:max_characters]
        truncated = True

    return RedactionResult(
        text=redacted,
        character_count=len(redacted),
        truncated=truncated,
    )
