"""Non-secret voice/runtime tuning loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VadRuntimeConfig:
    confidence: float
    start_secs: float
    stop_secs: float
    min_volume: float


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_no_answer_timeout_seconds(default: float = 10.0) -> float:
    """Seconds to wait for student speech after a tutor narration question (5–30)."""
    value = _env_float("NO_ANSWER_TIMEOUT_SECONDS", default)
    return max(5.0, min(30.0, value))


def load_qa_silence_timeout_seconds(default: float = 12.0) -> float:
    """Seconds for each Q&A wind-down silence stage (5–60)."""
    value = _env_float("QA_SILENCE_TIMEOUT_SECONDS", default)
    return max(5.0, min(60.0, value))


def load_lesson_followup_wait_seconds(default: float = 12.0) -> float:
    """Seconds before a one-shot post-answer hold reminder during the lesson (5–60)."""
    value = _env_float("LESSON_FOLLOWUP_WAIT_SECONDS", default)
    return max(5.0, min(60.0, value))


def load_tts_speech_speed(default: float = 1.05) -> float:
    """OpenAI TTS speed (API range 0.25–4.0). Default slightly above 1.0 for classroom pace."""
    value = _env_float("TTS_SPEECH_SPEED", default)
    return max(0.25, min(4.0, value))


def load_vad_runtime_config() -> VadRuntimeConfig:
    """Silero VAD params. Defaults match prior classroom tuning; stop_secs lightly tightened."""
    return VadRuntimeConfig(
        confidence=max(0.1, min(1.0, _env_float("VAD_CONFIDENCE", 0.85))),
        start_secs=max(0.05, min(2.0, _env_float("VAD_START_SECS", 0.45))),
        # Was 0.35; slight reduction shortens endpoint wait without aggressive cutoffs.
        stop_secs=max(0.1, min(2.0, _env_float("VAD_STOP_SECS", 0.28))),
        min_volume=max(0.1, min(1.0, _env_float("VAD_MIN_VOLUME", 0.7))),
    )
