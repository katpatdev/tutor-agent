"""Narrow session observability interface (no SQLite details leaked to callers)."""

from __future__ import annotations

import time
from typing import List, Optional

from loguru import logger

from tutor_agent.observability.session_config import SessionDataConfig
from tutor_agent.observability.session_metrics import SessionMetricsCollector
from tutor_agent.observability.session_store import (
    RagEventRecord,
    SafetyEventRecord,
    SessionStore,
    SessionStoreError,
    TranscriptEventRecord,
)
from tutor_agent.safety.transcript_redaction import RedactionError, redact_text


class SessionObservability:
    """Per-WebSocket-session facade for metrics + optional persistence."""

    def __init__(
        self,
        *,
        config: SessionDataConfig,
        store: Optional[SessionStore] = None,
        collector: Optional[SessionMetricsCollector] = None,
        tutor_prompt_version: Optional[str] = None,
        tutor_prompt_hash: Optional[str] = None,
        curriculum_version: Optional[str] = None,
    ) -> None:
        self.config = config
        self.store = store
        self.collector = collector or SessionMetricsCollector()
        self.tutor_prompt_version = tutor_prompt_version
        self.tutor_prompt_hash = tutor_prompt_hash
        self.curriculum_version = curriculum_version
        self._transcript_events: List[TranscriptEventRecord] = []
        self._safety_events: List[SafetyEventRecord] = []
        self._rag_events: List[RagEventRecord] = []
        self._transcript_sequence = 0
        self._transcript_writes_disabled = False
        self._configured = False
        self._lesson_started = False
        self.store_unavailable = False

    @property
    def session_id(self) -> str:
        return self.collector.session_id

    def mark_configured(self, *, transcript_consent: bool) -> str:
        """Apply consent once. Returns reason: enabled|declined|server_disabled."""
        if self._configured:
            return (
                "enabled"
                if self.collector.transcript_storage_active
                else (
                    "server_disabled"
                    if not self.config.transcript_persistence_enabled
                    else "declined"
                )
            )
        self._configured = True
        consent = bool(transcript_consent)
        self.collector.transcript_consent = consent
        if not self.config.transcript_persistence_enabled:
            self.collector.transcript_storage_active = False
            return "server_disabled"
        if not consent:
            self.collector.transcript_storage_active = False
            return "declined"
        self.collector.transcript_storage_active = True
        return "enabled"

    def mark_lesson_started(self) -> None:
        self._lesson_started = True
        if not self._configured:
            self.mark_configured(transcript_consent=False)

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def lesson_started(self) -> bool:
        return self._lesson_started

    def record_user_utterance(
        self,
        text: str,
        *,
        lesson_mode: str,
        slide_index: int,
    ) -> None:
        if not self.collector.transcript_storage_active or self._transcript_writes_disabled:
            return
        if not self._configured:
            return
        try:
            redacted = redact_text(
                text, max_characters=self.config.transcript_max_event_characters
            )
        except RedactionError:
            self._transcript_writes_disabled = True
            logger.warning("Transcript redaction failed; disabling further transcript writes")
            return
        self._transcript_sequence += 1
        self._transcript_events.append(
            TranscriptEventRecord(
                sequence=self._transcript_sequence,
                timestamp=time.time(),
                role="user",
                event_kind="final_utterance",
                lesson_mode=lesson_mode,
                slide_index=slide_index,
                redacted_text=redacted.text,
                text_character_count=redacted.character_count,
                is_safety_template=False,
                playback_status="approved_for_tts",
            )
        )

    def record_assistant_utterance(
        self,
        text: str,
        *,
        lesson_mode: str,
        slide_index: int,
        is_safety_template: bool = False,
        playback_status: str = "approved_for_tts",
    ) -> None:
        if not self.collector.transcript_storage_active or self._transcript_writes_disabled:
            return
        if not self._configured:
            return
        try:
            redacted = redact_text(
                text, max_characters=self.config.transcript_max_event_characters
            )
        except RedactionError:
            self._transcript_writes_disabled = True
            logger.warning("Transcript redaction failed; disabling further transcript writes")
            return
        self._transcript_sequence += 1
        self._transcript_events.append(
            TranscriptEventRecord(
                sequence=self._transcript_sequence,
                timestamp=time.time(),
                role="assistant",
                event_kind="safety_template" if is_safety_template else "llm_response",
                lesson_mode=lesson_mode,
                slide_index=slide_index,
                redacted_text=redacted.text,
                text_character_count=redacted.character_count,
                is_safety_template=is_safety_template,
                playback_status=playback_status,
            )
        )

    def record_safety_event(
        self,
        *,
        decision: str,
        reason_code: str,
        source: str,
        latency_ms: Optional[float],
        fallback_used: bool,
    ) -> None:
        self._safety_events.append(
            SafetyEventRecord(
                timestamp=time.time(),
                decision=decision,
                reason_code=reason_code,
                source=source,
                latency_ms=latency_ms,
                fallback_used=fallback_used,
            )
        )
        if decision == "redirect":
            self.collector.note_safety_redirect()
        elif decision == "hold":
            self.collector.note_safety_hold()
        failed = decision == "hold" and reason_code in {
            "moderation_timeout",
            "moderation_unavailable",
        }
        timeout = reason_code == "moderation_timeout"
        self.collector.note_moderation(
            latency_ms=latency_ms, failed=failed, timeout=timeout
        )

    def record_rag_event(
        self,
        *,
        attempted: bool,
        hit_count: int,
        source_ids: tuple[str, ...],
        embedding_latency_ms: Optional[float],
        retrieval_latency_ms: Optional[float],
        fallback_reason: Optional[str],
    ) -> None:
        self._rag_events.append(
            RagEventRecord(
                timestamp=time.time(),
                attempted=attempted,
                hit_count=hit_count,
                source_ids=source_ids,
                embedding_latency_ms=embedding_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
                fallback_reason=fallback_reason,
            )
        )
        if attempted:
            self.collector.note_rag(
                hit_count=hit_count,
                embedding_latency_ms=embedding_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
            )

    async def finalize(
        self,
        *,
        disconnect_reason: str,
        lesson_mode: str,
        slide_index: int,
    ) -> None:
        transcript_saved = bool(
            self.collector.transcript_storage_active and self._transcript_events
        )
        first = self.collector.finalize(
            disconnect_reason=disconnect_reason,
            lesson_mode=lesson_mode,
            slide_index=slide_index,
            transcript_saved=transcript_saved,
        )
        if not first:
            return

        try:
            self.collector.print_disconnect_report()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to print disconnect metrics report")

        if self.store is None or self.store_unavailable:
            return
        if not self.config.metrics_persistence_enabled and not (
            self.config.transcript_persistence_enabled
            and self.collector.transcript_storage_active
        ):
            return

        try:
            await self.store.persist_finalized_session_async(
                self.collector,
                transcript_events=self._transcript_events,
                safety_events=self._safety_events,
                rag_events=self._rag_events,
                persist_metrics=self.config.metrics_persistence_enabled,
                persist_transcripts=self.collector.transcript_storage_active,
                tutor_prompt_version=self.tutor_prompt_version,
                tutor_prompt_hash=self.tutor_prompt_hash,
                curriculum_version=self.curriculum_version,
            )
        except SessionStoreError:
            self.store_unavailable = True
            self._transcript_writes_disabled = True
            logger.warning("Session store unavailable during finalize")
        except Exception:  # noqa: BLE001
            self.store_unavailable = True
            self._transcript_writes_disabled = True
            logger.warning("Session persistence failed during finalize")
