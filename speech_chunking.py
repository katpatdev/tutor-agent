"""Pack moderated spoken text into TTS-friendly units.

Short adjacent sentences are merged so OpenAI TTS does not pay a separate
network round-trip per tiny phrase. NarrationPlan segments are unaffected —
callers pack only already-moderated answer / Q&A text.
"""

from __future__ import annotations

import re
from typing import List, Sequence

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_spoken_sentences(text: str) -> List[str]:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(cleaned) if p.strip()]
    return parts or [cleaned]


def pack_spoken_units(
    text: str,
    *,
    target_min_characters: int = 80,
    target_max_characters: int = 250,
) -> List[str]:
    """Merge short sentences into TTS units within approximate character bounds.

    Preserves sentence text and punctuation. Never drops content. A single
    oversize sentence is emitted alone (not hard-split mid-word).
    """
    if target_min_characters < 1 or target_max_characters < target_min_characters:
        raise ValueError("invalid spoken-unit character bounds")

    sentences = split_spoken_sentences(text)
    if not sentences:
        return []

    units: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        units.append(" ".join(buf))
        buf = []
        buf_len = 0

    for sentence in sentences:
        sentence_len = len(sentence)
        if not buf:
            buf = [sentence]
            buf_len = sentence_len
            if buf_len >= target_min_characters:
                flush()
            continue

        # Space joiner costs 1 character.
        projected = buf_len + 1 + sentence_len
        if projected <= target_max_characters:
            buf.append(sentence)
            buf_len = projected
            if buf_len >= target_min_characters:
                flush()
            continue

        flush()
        buf = [sentence]
        buf_len = sentence_len
        if buf_len >= target_min_characters:
            flush()

    flush()
    return units


def count_tts_units_for_text(text: str) -> int:
    return len(pack_spoken_units(text))
