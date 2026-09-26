"""Application-owned TTS speech-unit tracking (content-free public surface).

Private ``text`` stays inside PresentationRuntime only — never in metrics or
lesson.state. Detection of Pipecat 1.11.0 no-audio errors uses the TTS
processor identity plus the pinned error message prefix.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Mapping, Optional


# Pinned to pipecat-ai==1.11.0 TTSService._record_context_audio_outcome wording.
TTS_NO_AUDIO_ERROR_PREFIX = "TTS context "
TTS_NO_AUDIO_ERROR_SUFFIX = "completed with no audio"
TTS_CONSECUTIVE_NO_AUDIO_MARKER = "consecutive TTS contexts"


class SpeechUnitKind(str, Enum):
    NARRATION = "narration"
    ANSWER = "answer"
    QA = "qa"
    OTHER = "other"


@dataclass
class TtsRecoveryConfig:
    max_retries: int = 1
    first_audio_timeout_s: float = 8.0
    completion_timeout_base_s: float = 12.0
    completion_seconds_per_char: float = 0.06
    completion_timeout_max_s: float = 90.0
    max_consecutive_failures: int = 3

    def completion_timeout_for_chars(self, char_count: int) -> float:
        estimated = self.completion_timeout_base_s + max(0, char_count) * self.completion_seconds_per_char
        return min(self.completion_timeout_max_s, max(self.completion_timeout_base_s, estimated))


def load_tts_recovery_config(
    environ: Optional[Mapping[str, str]] = None,
) -> TtsRecoveryConfig:
    env = environ if environ is not None else os.environ

    def _int(name: str, default: int, *, minimum: int = 0) -> int:
        raw = env.get(name)
        if raw is None or not str(raw).strip():
            return default
        value = int(raw)
        if value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return value

    def _float(name: str, default: float, *, minimum: float = 0.1) -> float:
        raw = env.get(name)
        if raw is None or not str(raw).strip():
            return default
        value = float(raw)
        if value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return value

    return TtsRecoveryConfig(
        max_retries=_int("TTS_NO_AUDIO_MAX_RETRIES", 1, minimum=0),
        first_audio_timeout_s=_float("TTS_FIRST_AUDIO_TIMEOUT_SECONDS", 8.0),
        completion_timeout_base_s=_float("TTS_COMPLETION_TIMEOUT_BASE_SECONDS", 12.0),
        completion_seconds_per_char=_float("TTS_COMPLETION_SECONDS_PER_CHAR", 0.06, minimum=0.01),
        completion_timeout_max_s=_float("TTS_COMPLETION_TIMEOUT_MAX_SECONDS", 90.0),
        max_consecutive_failures=_int("TTS_MAX_CONSECUTIVE_FAILURES", 3, minimum=1),
    )


@dataclass
class OwnedSpeechUnit:
    """One application-owned TTSSpeakFrame attempt chain."""

    unit_id: str
    kind: SpeechUnitKind
    purpose_name: str
    utterance_id: int
    text: str
    # Stable across no-audio retries (unit_id rotates for watchdogs).
    logical_id: str = ""
    slide_index: Optional[int] = None
    narration_generation_id: Optional[str] = None
    segment_index: Optional[int] = None
    answer_unit_index: Optional[int] = None
    answer_unit_total: Optional[int] = None
    attempt: int = 1
    queued_at: float = field(default_factory=time.monotonic)
    tts_audio_started: bool = False
    audible_playback_started: bool = False
    normal_completion: bool = False
    inferred_completion: bool = False
    cancelled: bool = False
    failed: bool = False
    stale: bool = False
    start_watchdog_task: Any = None
    completion_watchdog_task: Any = None
    recovery_gate: bool = False
    ignore_error_frames: bool = False

    def __post_init__(self) -> None:
        if not self.logical_id:
            self.logical_id = self.unit_id

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())


_CONTEXT_ID_RE = re.compile(
    r"TTS context ([0-9a-fA-F-]{36}) completed with no audio"
)


def is_tts_no_audio_error(frame: Any, *, tts_processor: Any = None) -> bool:
    """True only for verified Pipecat 1.11.0 TTS silent-context errors."""
    error = getattr(frame, "error", None)
    if not isinstance(error, str):
        return False
    processor = getattr(frame, "processor", None)
    if tts_processor is not None:
        if processor is not tts_processor:
            # Allow type-name match for offline fakes / observer copies.
            proc_name = type(processor).__name__ if processor is not None else ""
            tts_name = type(tts_processor).__name__
            if proc_name != tts_name or "TTS" not in proc_name:
                return False
    else:
        if processor is None:
            return False
        name = type(processor).__name__
        if "TTS" not in name:
            return False
    if TTS_CONSECUTIVE_NO_AUDIO_MARKER in error:
        return True
    if error.startswith(TTS_NO_AUDIO_ERROR_PREFIX) and TTS_NO_AUDIO_ERROR_SUFFIX in error:
        return True
    return False


def extract_tts_context_id(error_message: str) -> Optional[str]:
    match = _CONTEXT_ID_RE.search(error_message or "")
    return match.group(1) if match else None
