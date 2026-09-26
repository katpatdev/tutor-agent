"""Deterministic narration segmentation and plan structures.

Resume accuracy is segment-level unless verified browser playback offsets exist.
Installed Pipecat 1.11.0 / client-js 1.6.0 do not provide played-byte or word
offsets back to the server for the FastAPI WebSocket + Protobuf path.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Mapping, Optional, Sequence, Tuple


class NarrationConfigError(Exception):
    """Invalid narration configuration."""


class ResumeAccuracy(str, Enum):
    SEGMENT = "segment"
    # Exact would require verified playback-offset APIs (not available in this stack).
    EXACT_PLAYBACK = "exact_playback"


class SegmentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    USER_ACKNOWLEDGED = "user_acknowledged"


@dataclass(frozen=True)
class NarrationGenerationId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class NarrationSegment:
    index: int
    text: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("segment index must be non-negative")
        if not self.text.strip():
            raise ValueError("segment text must be non-empty")


@dataclass
class NarrationProgress:
    """Zero-based internal indexes; UI converts to one-based."""

    slide_index: int
    active_segment_index: int
    total_segments: int
    resume_accuracy: ResumeAccuracy
    invalidated: bool = False

    def to_public_dict(self) -> dict:
        # One-based display fields for frontend; no narration text.
        current = (
            self.active_segment_index + 1
            if self.total_segments > 0 and not self.invalidated
            else 0
        )
        return {
            "current_segment": current,
            "total_segments": self.total_segments,
            "resume_accuracy": self.resume_accuracy.value,
        }


@dataclass
class NarrationPlan:
    slide_index: int
    generation_id: NarrationGenerationId
    segments: Tuple[NarrationSegment, ...]
    active_segment_index: int = 0
    completed_indexes: frozenset[int] = field(default_factory=frozenset)
    user_acknowledged_indexes: frozenset[int] = field(default_factory=frozenset)
    active_status: SegmentStatus = SegmentStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resume_accuracy: ResumeAccuracy = ResumeAccuracy.SEGMENT
    invalidated: bool = False
    segment_queued: bool = False

    @property
    def total_segments(self) -> int:
        return len(self.segments)

    @property
    def is_complete(self) -> bool:
        return (
            not self.invalidated
            and self.total_segments > 0
            and len(self.completed_indexes) == self.total_segments
        )

    def progress(self) -> NarrationProgress:
        return NarrationProgress(
            slide_index=self.slide_index,
            active_segment_index=self.active_segment_index,
            total_segments=self.total_segments,
            resume_accuracy=self.resume_accuracy,
            invalidated=self.invalidated,
        )

    def active_text(self) -> Optional[str]:
        if self.invalidated or not self.segments:
            return None
        if self.active_segment_index < 0 or self.active_segment_index >= len(self.segments):
            return None
        return self.segments[self.active_segment_index].text


DEFAULT_MAX_SEGMENT_CHARACTERS = 320
# Pack adjacent short sentences into one resume/TTS unit so tiny phrases
# (e.g. "Hello everyone!") are not separate network round-trips. Larger units
# replay more speech after an interruption — keep this bounded.
DEFAULT_MIN_SEGMENT_CHARACTERS = 110

# Decimal / numeric protection: do not split after digit before digit.
_ABBREV = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "u.s",
    "u.k",
}


def load_narration_max_characters(
    environ: Optional[Mapping[str, str]] = None,
) -> int:
    env = environ if environ is not None else os.environ
    raw = env.get("NARRATION_SEGMENT_MAX_CHARACTERS", str(DEFAULT_MAX_SEGMENT_CHARACTERS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise NarrationConfigError(
            "NARRATION_SEGMENT_MAX_CHARACTERS must be an integer"
        ) from exc
    if value < 40:
        raise NarrationConfigError(
            "NARRATION_SEGMENT_MAX_CHARACTERS must be >= 40"
        )
    return value


def load_narration_min_characters(
    environ: Optional[Mapping[str, str]] = None,
) -> int:
    env = environ if environ is not None else os.environ
    raw = env.get("NARRATION_SEGMENT_MIN_CHARACTERS", str(DEFAULT_MIN_SEGMENT_CHARACTERS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise NarrationConfigError(
            "NARRATION_SEGMENT_MIN_CHARACTERS must be an integer"
        ) from exc
    if value < 1:
        raise NarrationConfigError(
            "NARRATION_SEGMENT_MIN_CHARACTERS must be >= 1"
        )
    return value


def new_generation_id() -> NarrationGenerationId:
    return NarrationGenerationId(value=str(uuid.uuid4()))


def _is_abbrev_end(text: str, punct_index: int) -> bool:
    # Look at token before the period.
    start = punct_index - 1
    while start >= 0 and text[start].isalpha():
        start -= 1
    token = text[start + 1 : punct_index].lower()
    return token in _ABBREV


def _sentence_end_positions(text: str) -> List[int]:
    """Return indexes of sentence-ending punctuation that may split (inclusive end)."""
    ends: List[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ".?!":
            # Avoid decimals: digit . digit
            if (
                ch == "."
                and i > 0
                and text[i - 1].isdigit()
                and i + 1 < n
                and text[i + 1].isdigit()
            ):
                i += 1
                continue
            if ch == "." and _is_abbrev_end(text, i):
                i += 1
                continue
            # Consume consecutive closing punctuation
            j = i
            while j + 1 < n and text[j + 1] in ".?!\"'”’)":
                j += 1
            ends.append(j)
            i = j + 1
            continue
        i += 1
    return ends


def _split_long_clause(text: str, max_chars: int) -> List[str]:
    """Split an over-long sentence at safe clause boundaries."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    parts: List[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        # Prefer comma / semicolon / colon / em-dash within window
        cut = -1
        for sep in ("; ", ": ", " — ", " - ", ", "):
            idx = window.rfind(sep)
            if idx >= max_chars // 3:
                cut = idx + len(sep)
                break
        if cut < 0:
            # Fall back to last whitespace
            idx = window.rfind(" ")
            cut = idx if idx >= max_chars // 3 else max_chars
        chunk = remaining[:cut].strip()
        if chunk:
            parts.append(chunk)
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def segment_narration_text(
    text: str,
    *,
    max_characters: int = DEFAULT_MAX_SEGMENT_CHARACTERS,
    min_characters: int = DEFAULT_MIN_SEGMENT_CHARACTERS,
) -> Tuple[NarrationSegment, ...]:
    """Split approved narration into deterministic voice-friendly segments.

    Short adjacent sentences are packed into bounded resume/TTS units so a
    tiny phrase is not alone. Oversized sentences are clause-split. Preserves
    word order and factual content. Identical inputs → identical outputs.
    """
    if max_characters < 40:
        raise ValueError("max_characters must be >= 40")
    if min_characters < 1:
        raise ValueError("min_characters must be >= 1")
    # When callers tighten max below the default min (tests), pack to max only.
    pack_min = min(min_characters, max_characters)
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return tuple()

    ends = _sentence_end_positions(cleaned)
    sentences: List[str] = []
    cursor = 0
    for end in ends:
        piece = cleaned[cursor : end + 1].strip()
        if piece and not all(ch in ".?!\"'”’) " for ch in piece):
            sentences.append(piece)
        cursor = end + 1
    tail = cleaned[cursor:].strip()
    if tail and not all(ch in ".?!\"'”’) " for ch in tail):
        sentences.append(tail)

    if not sentences:
        # No sentence punctuation — treat whole text as one unit (then clause-split).
        sentences = [cleaned]

    pieces: List[str] = []
    for sentence in sentences:
        pieces.extend(_split_long_clause(sentence, max_characters))

    packed: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        packed.append(" ".join(buf))
        buf = []
        buf_len = 0

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        piece_len = len(piece)
        if not buf:
            buf = [piece]
            buf_len = piece_len
            if buf_len >= pack_min:
                flush()
            continue
        projected = buf_len + 1 + piece_len
        if projected <= max_characters:
            buf.append(piece)
            buf_len = projected
            if buf_len >= pack_min:
                flush()
            continue
        flush()
        buf = [piece]
        buf_len = piece_len
        if buf_len >= pack_min:
            flush()
    flush()

    segments: List[NarrationSegment] = []
    for chunk in packed:
        piece = chunk.strip()
        if not piece or all(ch in ".?!\"'”’) " for ch in piece):
            continue
        segments.append(NarrationSegment(index=len(segments), text=piece))
    return tuple(segments)


def build_narration_plan(
    *,
    slide_index: int,
    text: str,
    max_characters: int = DEFAULT_MAX_SEGMENT_CHARACTERS,
    min_characters: int = DEFAULT_MIN_SEGMENT_CHARACTERS,
    generation_id: Optional[NarrationGenerationId] = None,
    resume_accuracy: ResumeAccuracy = ResumeAccuracy.SEGMENT,
) -> NarrationPlan:
    segments = segment_narration_text(
        text,
        max_characters=max_characters,
        min_characters=min_characters,
    )
    return NarrationPlan(
        slide_index=slide_index,
        generation_id=generation_id or new_generation_id(),
        segments=segments,
        active_segment_index=0,
        completed_indexes=frozenset(),
        active_status=SegmentStatus.PENDING,
        resume_accuracy=resume_accuracy,
        invalidated=False,
        segment_queued=False,
    )


def invalidate_plan(plan: Optional[NarrationPlan]) -> Optional[NarrationPlan]:
    if plan is None:
        return None
    plan.invalidated = True
    plan.active_status = SegmentStatus.PENDING
    plan.segment_queued = False
    return plan
