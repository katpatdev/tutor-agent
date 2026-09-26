"""Per-session operational metrics collector and disconnect report."""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    MetricsData,
    ProcessingMetricsData,
    STTUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)


def percentile_nearest_rank(sorted_values: Sequence[float], pct: float) -> float:
    """Deterministic nearest-rank percentile.

    ``pct`` is in (0, 100]. For an empty series, raises ValueError.
    Index = ceil(pct/100 * n) - 1, clamped to [0, n-1].
    """
    if not sorted_values:
        raise ValueError("percentile requires a non-empty series")
    if pct <= 0 or pct > 100:
        raise ValueError("pct must be in (0, 100]")
    n = len(sorted_values)
    # ceil(pct/100 * n) without importing math for tiny helper clarity
    rank = int(-(-(pct / 100.0 * n) // 1))  # ceiling
    index = max(0, min(n - 1, rank - 1))
    return float(sorted_values[index])


@dataclass
class LatencySeries:
    values: List[float] = field(default_factory=list)

    def add(self, seconds: float) -> None:
        if seconds is None:
            return
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        self.values.append(value)

    def summary(self) -> Dict[str, Optional[float]]:
        if not self.values:
            return {
                "count": 0,
                "min": None,
                "mean": None,
                "p50": None,
                "p95": None,
                "max": None,
            }
        ordered = sorted(self.values)
        return {
            "count": len(ordered),
            "min": ordered[0],
            "mean": float(statistics.fmean(ordered)),
            "p50": percentile_nearest_rank(ordered, 50),
            "p95": percentile_nearest_rank(ordered, 95),
            "max": ordered[-1],
        }


@dataclass
class SessionMetricsCollector:
    """One collector per WebSocket session. Never stores transcript text."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    disconnect_reason: str = "unknown"
    lesson_mode: str = "IDLE"
    final_slide_index: int = 0
    slides_started: int = 0
    slides_completed: int = 0
    interruptions: int = 0
    answers: int = 0
    pauses: int = 0
    resumes: int = 0
    navigations: int = 0
    qa_entries: int = 0
    safety_redirects: int = 0
    safety_holds: int = 0
    moderation_requests: int = 0
    moderation_failures: int = 0
    moderation_timeouts: int = 0
    moderation_latency: LatencySeries = field(default_factory=LatencySeries)
    rag_queries: int = 0
    rag_hits: int = 0
    rag_misses: int = 0
    retrieved_chunk_count: int = 0
    embedding_latency: LatencySeries = field(default_factory=LatencySeries)
    retrieval_latency: LatencySeries = field(default_factory=LatencySeries)
    document_uploads: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    tts_characters: int = 0
    stt_audio_seconds: float = 0.0
    ttfb_latency: LatencySeries = field(default_factory=LatencySeries)
    processing_latency: LatencySeries = field(default_factory=LatencySeries)
    handled_errors: int = 0
    metric_parse_failures: int = 0
    transcript_consent: bool = False
    transcript_storage_active: bool = False
    transcript_saved: bool = False
    finalized: bool = False
    application_errors: int = 0
    narration_plans_created: int = 0
    narration_segments_generated: int = 0
    narration_segments_completed: int = 0
    narration_segment_interruptions: int = 0
    narration_segment_replays: int = 0
    narration_stale_completions_ignored: int = 0
    narration_cancelled_completions_suppressed: int = 0
    narration_errors: int = 0
    narration_segment_char_total: int = 0
    narration_resume_accuracy: str = "segment"
    answer_lifecycle_stages: dict = field(default_factory=dict)
    no_answer_continuations: int = 0
    tts_units_queued: int = 0
    tts_no_audio_failures: int = 0
    tts_first_audio_timeouts: int = 0
    tts_completion_timeouts: int = 0
    tts_retry_attempts: int = 0
    tts_retry_successes: int = 0
    tts_retry_exhaustion: int = 0
    tts_failed_narration_units: int = 0
    tts_failed_answer_units: int = 0
    tts_stale_failures_ignored: int = 0
    tts_inferred_completions: int = 0
    tts_retry_races_suppressed: int = 0
    voice_nav_commands: int = 0
    voice_nav_rejections: int = 0
    deterministic_continues: int = 0
    user_acknowledged_segments: int = 0
    deterministic_repeats: int = 0
    qa_reminders: int = 0
    qa_explicit_closes: int = 0
    qa_silence_closes: int = 0
    qa_closing_failures: int = 0
    sessions_finished_normally: int = 0
    control_intents_ambiguous: int = 0
    post_answer_holds: int = 0
    post_answer_reminders: int = 0
    post_answer_hold_exits: int = 0
    lesson_detours_created: int = 0
    lesson_detours_returned: int = 0
    classroom_control_matched: int = 0
    classroom_control_ambiguous: int = 0
    classroom_control_rejected_negation: int = 0
    stale_callbacks_discarded: int = 0
    lesson_context_injections: int = 0
    lesson_context_chars: int = 0

    def note_tts_delivery_snapshot(
        self,
        *,
        units_queued: int = 0,
        no_audio_failures: int = 0,
        first_audio_timeouts: int = 0,
        completion_timeouts: int = 0,
        retry_attempts: int = 0,
        retry_successes: int = 0,
        retry_exhaustion: int = 0,
        failed_narration_units: int = 0,
        failed_answer_units: int = 0,
        stale_failures_ignored: int = 0,
        inferred_completions: int = 0,
        retry_races_suppressed: int = 0,
        voice_nav_commands: int = 0,
        voice_nav_rejections: int = 0,
        deterministic_continues: int = 0,
        user_acknowledged_segments: int = 0,
        deterministic_repeats: int = 0,
        qa_reminders: int = 0,
        qa_explicit_closes: int = 0,
        qa_silence_closes: int = 0,
        qa_closing_failures: int = 0,
        sessions_finished_normally: int = 0,
        control_intents_ambiguous: int = 0,
        post_answer_holds: int = 0,
        post_answer_reminders: int = 0,
        post_answer_hold_exits: int = 0,
        lesson_detours_created: int = 0,
        lesson_detours_returned: int = 0,
        classroom_control_matched: int = 0,
        classroom_control_ambiguous: int = 0,
        classroom_control_rejected_negation: int = 0,
        stale_callbacks_discarded: int = 0,
        lesson_context_injections: int = 0,
        lesson_context_chars: int = 0,
    ) -> None:
        self.tts_units_queued = units_queued
        self.tts_no_audio_failures = no_audio_failures
        self.tts_first_audio_timeouts = first_audio_timeouts
        self.tts_completion_timeouts = completion_timeouts
        self.tts_retry_attempts = retry_attempts
        self.tts_retry_successes = retry_successes
        self.tts_retry_exhaustion = retry_exhaustion
        self.tts_failed_narration_units = failed_narration_units
        self.tts_failed_answer_units = failed_answer_units
        self.tts_stale_failures_ignored = stale_failures_ignored
        self.tts_inferred_completions = inferred_completions
        self.tts_retry_races_suppressed = retry_races_suppressed
        self.voice_nav_commands = voice_nav_commands
        self.voice_nav_rejections = voice_nav_rejections
        self.deterministic_continues = deterministic_continues
        self.user_acknowledged_segments = user_acknowledged_segments
        self.deterministic_repeats = deterministic_repeats
        self.qa_reminders = qa_reminders
        self.qa_explicit_closes = qa_explicit_closes
        self.qa_silence_closes = qa_silence_closes
        self.qa_closing_failures = qa_closing_failures
        self.sessions_finished_normally = sessions_finished_normally
        self.control_intents_ambiguous = control_intents_ambiguous
        self.post_answer_holds = post_answer_holds
        self.post_answer_reminders = post_answer_reminders
        self.post_answer_hold_exits = post_answer_hold_exits
        self.lesson_detours_created = lesson_detours_created
        self.lesson_detours_returned = lesson_detours_returned
        self.classroom_control_matched = classroom_control_matched
        self.classroom_control_ambiguous = classroom_control_ambiguous
        self.classroom_control_rejected_negation = classroom_control_rejected_negation
        self.stale_callbacks_discarded = stale_callbacks_discarded
        self.lesson_context_injections = lesson_context_injections
        self.lesson_context_chars = lesson_context_chars

    def note_narration_snapshot(
        self,
        *,
        plans_created: int,
        segments_generated: int,
        segments_completed: int,
        segment_interruptions: int,
        segment_replays: int,
        stale_completions_ignored: int,
        cancelled_completions_suppressed: int,
        narration_errors: int,
        segment_char_total: int,
        resume_accuracy: str = "segment",
    ) -> None:
        """Copy content-free narration counters (no segment text)."""
        self.narration_plans_created = plans_created
        self.narration_segments_generated = segments_generated
        self.narration_segments_completed = segments_completed
        self.narration_segment_interruptions = segment_interruptions
        self.narration_segment_replays = segment_replays
        self.narration_stale_completions_ignored = stale_completions_ignored
        self.narration_cancelled_completions_suppressed = cancelled_completions_suppressed
        self.narration_errors = narration_errors
        self.narration_segment_char_total = segment_char_total
        self.narration_resume_accuracy = resume_accuracy

    def note_lesson_state(self, *, mode: str, slide_index: int) -> None:
        self.lesson_mode = mode
        self.final_slide_index = slide_index

    def note_slide_started(self) -> None:
        self.slides_started += 1

    def note_slide_completed(self) -> None:
        self.slides_completed += 1

    def note_interruption(self) -> None:
        self.interruptions += 1

    def note_answer(self) -> None:
        self.answers += 1

    def note_pause(self) -> None:
        self.pauses += 1

    def note_resume(self) -> None:
        self.resumes += 1

    def note_navigation(self) -> None:
        self.navigations += 1

    def note_qa_entry(self) -> None:
        self.qa_entries += 1

    def note_safety_redirect(self) -> None:
        self.safety_redirects += 1

    def note_safety_hold(self) -> None:
        self.safety_holds += 1

    def note_moderation(
        self,
        *,
        latency_ms: Optional[float],
        failed: bool = False,
        timeout: bool = False,
    ) -> None:
        self.moderation_requests += 1
        if timeout:
            self.moderation_timeouts += 1
        if failed:
            self.moderation_failures += 1
        if latency_ms is not None:
            self.moderation_latency.add(latency_ms / 1000.0)

    def note_rag(
        self,
        *,
        hit_count: int,
        embedding_latency_ms: Optional[float],
        retrieval_latency_ms: Optional[float],
    ) -> None:
        self.rag_queries += 1
        if hit_count > 0:
            self.rag_hits += 1
            self.retrieved_chunk_count += hit_count
        else:
            self.rag_misses += 1
        if embedding_latency_ms is not None:
            self.embedding_latency.add(embedding_latency_ms / 1000.0)
        if retrieval_latency_ms is not None:
            self.retrieval_latency.add(retrieval_latency_ms / 1000.0)

    def note_document_upload(self) -> None:
        self.document_uploads += 1

    def note_handled_error(self) -> None:
        self.handled_errors += 1
        self.application_errors += 1

    def note_answer_stage(self, stage: str) -> None:
        """Content-free answer-pipeline stage counter (no transcript text)."""
        key = str(stage or "").strip()
        if not key:
            return
        self.answer_lifecycle_stages[key] = int(self.answer_lifecycle_stages.get(key, 0)) + 1
        if key == "no_answer_continue":
            self.no_answer_continuations += 1

    def ingest_metrics_frame(self, frame: Any) -> None:
        try:
            if not isinstance(frame, MetricsFrame):
                return
            for item in frame.data or []:
                self._ingest_metrics_datum(item)
        except Exception:  # noqa: BLE001
            self.metric_parse_failures += 1

    def _ingest_metrics_datum(self, item: Any) -> None:
        try:
            if isinstance(item, TTFBMetricsData):
                self.ttfb_latency.add(float(item.value))
            elif isinstance(item, ProcessingMetricsData):
                self.processing_latency.add(float(item.value))
            elif isinstance(item, LLMUsageMetricsData):
                usage = item.value
                self.llm_prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.llm_completion_tokens += int(
                    getattr(usage, "completion_tokens", 0) or 0
                )
                self.llm_total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
            elif isinstance(item, TTSUsageMetricsData):
                self.tts_characters += int(item.value or 0)
            elif isinstance(item, STTUsageMetricsData):
                usage = item.value
                self.stt_audio_seconds += float(
                    getattr(usage, "audio_seconds", 0.0) or 0.0
                )
            elif isinstance(item, MetricsData):
                # Other verified MetricsData subclasses exist but are unused here.
                return
            else:
                # Unknown / malformed entry — ignore without crashing.
                self.metric_parse_failures += 1
        except Exception:  # noqa: BLE001
            self.metric_parse_failures += 1

    def duration_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def finalize(
        self,
        *,
        disconnect_reason: str,
        lesson_mode: Optional[str] = None,
        slide_index: Optional[int] = None,
        transcript_saved: bool = False,
    ) -> bool:
        """Mark session ended. Returns True on first finalization only."""
        if self.finalized:
            return False
        self.finalized = True
        self.ended_at = time.time()
        self.disconnect_reason = disconnect_reason
        if lesson_mode is not None:
            self.lesson_mode = lesson_mode
        if slide_index is not None:
            self.final_slide_index = slide_index
        self.transcript_saved = transcript_saved
        return True

    def build_disconnect_report(self) -> Dict[str, Any]:
        """Content-free disconnect report suitable for console printing."""
        start = datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat()
        end_ts = self.ended_at if self.ended_at is not None else time.time()
        end = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
        return {
            "session_id": self.session_id,
            "started_at": start,
            "ended_at": end,
            "duration_seconds": round(self.duration_seconds(), 3),
            "disconnect_reason": self.disconnect_reason,
            "lesson_mode": self.lesson_mode,
            "last_slide_number": self.final_slide_index + 1,
            "slides_started": self.slides_started,
            "slides_completed": self.slides_completed,
            "interruptions": self.interruptions,
            "answers": self.answers,
            "pauses": self.pauses,
            "resumes": self.resumes,
            "navigations": self.navigations,
            "qa_entries": self.qa_entries,
            "safety_redirects": self.safety_redirects,
            "safety_holds": self.safety_holds,
            "moderation_requests": self.moderation_requests,
            "moderation_failures": self.moderation_failures,
            "moderation_timeouts": self.moderation_timeouts,
            "moderation_latency_seconds": self.moderation_latency.summary(),
            "rag_queries": self.rag_queries,
            "rag_hits": self.rag_hits,
            "rag_misses": self.rag_misses,
            "retrieved_chunk_count": self.retrieved_chunk_count,
            "embedding_latency_seconds": self.embedding_latency.summary(),
            "retrieval_latency_seconds": self.retrieval_latency.summary(),
            "document_uploads": self.document_uploads,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_total_tokens": self.llm_total_tokens,
            "tts_characters": self.tts_characters,
            "stt_audio_seconds": round(self.stt_audio_seconds, 3),
            "ttfb_latency_seconds": self.ttfb_latency.summary(),
            "processing_latency_seconds": self.processing_latency.summary(),
            "handled_errors": self.handled_errors,
            "metric_parse_failures": self.metric_parse_failures,
            "transcript_consent": self.transcript_consent,
            "transcript_storage_active": self.transcript_storage_active,
            "transcript_saved": self.transcript_saved,
            "narration_plans_created": self.narration_plans_created,
            "narration_segments_generated": self.narration_segments_generated,
            "narration_segments_completed": self.narration_segments_completed,
            "narration_segment_interruptions": self.narration_segment_interruptions,
            "narration_segment_replays": self.narration_segment_replays,
            "narration_stale_completions_ignored": self.narration_stale_completions_ignored,
            "narration_cancelled_completions_suppressed": self.narration_cancelled_completions_suppressed,
            "answer_lifecycle_stages": dict(self.answer_lifecycle_stages),
            "no_answer_continuations": self.no_answer_continuations,
            "narration_errors": self.narration_errors,
            "narration_average_segment_characters": (
                round(self.narration_segment_char_total / self.narration_segments_generated, 2)
                if self.narration_segments_generated
                else 0.0
            ),
            "narration_resume_accuracy": self.narration_resume_accuracy,
            "tts_units_queued": self.tts_units_queued,
            "tts_no_audio_failures": self.tts_no_audio_failures,
            "tts_first_audio_timeouts": self.tts_first_audio_timeouts,
            "tts_completion_timeouts": self.tts_completion_timeouts,
            "tts_retry_attempts": self.tts_retry_attempts,
            "tts_retry_successes": self.tts_retry_successes,
            "tts_retry_exhaustion": self.tts_retry_exhaustion,
            "tts_failed_narration_units": self.tts_failed_narration_units,
            "tts_failed_answer_units": self.tts_failed_answer_units,
            "tts_stale_failures_ignored": self.tts_stale_failures_ignored,
            "tts_inferred_completions": self.tts_inferred_completions,
            "tts_retry_races_suppressed": self.tts_retry_races_suppressed,
            "voice_nav_commands": self.voice_nav_commands,
            "voice_nav_rejections": self.voice_nav_rejections,
            "deterministic_continues": self.deterministic_continues,
            "user_acknowledged_segments": self.user_acknowledged_segments,
            "deterministic_repeats": self.deterministic_repeats,
            "qa_reminders": self.qa_reminders,
            "qa_explicit_closes": self.qa_explicit_closes,
            "qa_silence_closes": self.qa_silence_closes,
            "qa_closing_failures": self.qa_closing_failures,
            "sessions_finished_normally": self.sessions_finished_normally,
            "control_intents_ambiguous": self.control_intents_ambiguous,
            "post_answer_holds": self.post_answer_holds,
            "post_answer_reminders": self.post_answer_reminders,
            "post_answer_hold_exits": self.post_answer_hold_exits,
            "lesson_detours_created": self.lesson_detours_created,
            "lesson_detours_returned": self.lesson_detours_returned,
            "classroom_control_matched": self.classroom_control_matched,
            "classroom_control_ambiguous": self.classroom_control_ambiguous,
            "classroom_control_rejected_negation": self.classroom_control_rejected_negation,
            "stale_callbacks_discarded": self.stale_callbacks_discarded,
            "lesson_context_injections": self.lesson_context_injections,
            "lesson_context_chars": self.lesson_context_chars,
        }

    def print_disconnect_report(self) -> None:
        report = self.build_disconnect_report()
        # Structured single-block report; never includes transcript text.
        lines = ["===== SESSION METRICS REPORT ====="]
        for key, value in report.items():
            lines.append(f"{key}: {value}")
        lines.append("===== END SESSION METRICS REPORT =====")
        print("\n".join(lines))
