"""TTS delivery ownership, watchdogs, and bounded no-audio recovery.

Used only by PresentationRuntime. Never stores text in metrics or public state.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, List, Optional

from tutor_agent.audio.tts_unit import (
    OwnedSpeechUnit,
    SpeechUnitKind,
    TtsRecoveryConfig,
    is_tts_no_audio_error,
    load_tts_recovery_config,
)


CreateTask = Callable[[Awaitable[Any]], Any]
TimeFn = Callable[[], float]


class TtsDeliveryController:
    """Tracks the in-flight application TTS unit and drives retry/watchdogs."""

    def __init__(
        self,
        host: Any,
        *,
        config: Optional[TtsRecoveryConfig] = None,
        time_fn: Optional[TimeFn] = None,
        create_task: Optional[CreateTask] = None,
        sleep_fn: Optional[Any] = None,
    ) -> None:
        self._host = host
        self.config = config or load_tts_recovery_config()
        self._time = time_fn or time.monotonic
        self._create_task = create_task or asyncio.create_task
        self._sleep = sleep_fn or asyncio.sleep
        self.pending: Optional[OwnedSpeechUnit] = None
        self.consecutive_failures = 0
        self.audio_error_state = False
        self.audio_warning: Optional[str] = None
        # Content-free counters (mirrored onto host for tests/metrics).
        self.units_queued = 0
        self.no_audio_failures = 0
        self.first_audio_timeouts = 0
        self.completion_timeouts = 0
        self.retry_attempts = 0
        self.retry_successes = 0
        self.retry_exhaustion = 0
        self.failed_narration_units = 0
        self.failed_answer_units = 0
        self.stale_failures_ignored = 0
        self.inferred_completions = 0
        self.retry_races_suppressed = 0

    def sync_counters_to_host(self) -> None:
        h = self._host
        h.tts_units_queued = self.units_queued
        h.tts_no_audio_failures = self.no_audio_failures
        h.tts_first_audio_timeouts = self.first_audio_timeouts
        h.tts_completion_timeouts = self.completion_timeouts
        h.tts_retry_attempts = self.retry_attempts
        h.tts_retry_successes = self.retry_successes
        h.tts_retry_exhaustion = self.retry_exhaustion
        h.tts_failed_narration_units = self.failed_narration_units
        h.tts_failed_answer_units = self.failed_answer_units
        h.tts_stale_failures_ignored = self.stale_failures_ignored
        h.tts_inferred_completions = self.inferred_completions
        h.tts_retry_races_suppressed = self.retry_races_suppressed

    def cancel_watchdogs(self, unit: Optional[OwnedSpeechUnit] = None) -> None:
        target = unit or self.pending
        if target is None:
            return
        for attr in ("start_watchdog_task", "completion_watchdog_task"):
            task = getattr(target, attr, None)
            setattr(target, attr, None)
            if task is not None and not getattr(task, "done", lambda: True)():
                task.cancel()

    def invalidate_pending(self, *, reason: str = "cancelled") -> None:
        unit = self.pending
        if unit is None:
            return
        self.cancel_watchdogs(unit)
        unit.cancelled = True
        unit.stale = True
        self.pending = None

    async def queue_unit(
        self,
        text: str,
        *,
        kind: SpeechUnitKind,
        purpose_name: str,
        utterance_id: int,
        slide_index: Optional[int] = None,
        narration_generation_id: Optional[str] = None,
        segment_index: Optional[int] = None,
        answer_unit_index: Optional[int] = None,
        answer_unit_total: Optional[int] = None,
        attempt: int = 1,
        logical_id: Optional[str] = None,
    ) -> OwnedSpeechUnit:
        if self.pending is not None and not self.pending.cancelled:
            self.invalidate_pending(reason="replaced")
        unit = OwnedSpeechUnit(
            unit_id=OwnedSpeechUnit.new_id(),
            kind=kind,
            purpose_name=purpose_name,
            utterance_id=utterance_id,
            text=text,
            logical_id=logical_id or "",
            slide_index=slide_index,
            narration_generation_id=narration_generation_id,
            segment_index=segment_index,
            answer_unit_index=answer_unit_index,
            answer_unit_total=answer_unit_total,
            attempt=attempt,
            queued_at=self._time(),
        )
        self.pending = unit
        self.units_queued += 1
        self.sync_counters_to_host()
        self._arm_start_watchdog(unit)
        await self._host._emit_tts_speak_frame(text)
        return unit

    def on_audible_start(self) -> None:
        unit = self.pending
        if unit is None or unit.cancelled or unit.stale:
            return
        unit.tts_audio_started = True
        unit.audible_playback_started = True
        unit.ignore_error_frames = False
        unit.recovery_gate = False
        self.cancel_watchdogs(unit)
        unit.start_watchdog_task = None
        self._arm_completion_watchdog(unit)

    def on_normal_completion(self) -> None:
        unit = self.pending
        if unit is None:
            return
        self.cancel_watchdogs(unit)
        unit.normal_completion = True
        if unit.attempt > 1 and not unit.failed:
            self.retry_successes += 1
            self.sync_counters_to_host()
        self.consecutive_failures = 0
        self.pending = None

    async def on_error_frame(self, frame: Any, *, tts_processor: Any = None) -> bool:
        """Handle a verified TTS no-audio error. Returns True if consumed."""
        if not is_tts_no_audio_error(frame, tts_processor=tts_processor):
            return False
        unit = self.pending
        if unit is None or unit.cancelled or unit.stale or unit.normal_completion:
            self.stale_failures_ignored += 1
            self.sync_counters_to_host()
            return True
        if unit.audible_playback_started and unit.tts_audio_started:
            # Already heard audio for this unit — never retry as no-audio.
            self.stale_failures_ignored += 1
            self.sync_counters_to_host()
            return True
        if unit.ignore_error_frames or unit.recovery_gate:
            self.retry_races_suppressed += 1
            self.sync_counters_to_host()
            return True
        if not self._host._speech_unit_still_current(unit):
            unit.stale = True
            self.stale_failures_ignored += 1
            if hasattr(self._host, "stale_callbacks_discarded"):
                self._host.stale_callbacks_discarded += 1
                if hasattr(self._host, "_note_diag"):
                    self._host._note_diag(
                        "stale_callback_discarded", cause="tts_callback"
                    )
            self.sync_counters_to_host()
            self.pending = None
            return True

        self.no_audio_failures += 1
        self.consecutive_failures += 1
        self.sync_counters_to_host()
        await self._recover_failed_unit(unit, cause="no_audio")
        return True

    async def _recover_failed_unit(self, unit: OwnedSpeechUnit, *, cause: str) -> None:
        if unit.failed or unit.cancelled or unit.stale or unit.normal_completion:
            self.retry_races_suppressed += 1
            self.sync_counters_to_host()
            return
        if unit.recovery_gate:
            self.retry_races_suppressed += 1
            self.sync_counters_to_host()
            return
        unit.recovery_gate = True
        self.cancel_watchdogs(unit)
        if self.audio_error_state or (
            self.consecutive_failures >= self.config.max_consecutive_failures
        ):
            self.audio_error_state = True
            self.audio_warning = (
                "Audio delivery is unavailable. Please reconnect or try again."
            )
            unit.failed = True
            unit.recovery_gate = False
            self.retry_exhaustion += 1
            self.sync_counters_to_host()
            await self._host._on_tts_unit_exhausted(unit, cause=cause)
            return

        if unit.attempt <= self.config.max_retries:
            self.retry_attempts += 1
            self.sync_counters_to_host()
            await self._host._flush_tts_before_retry()
            unit.unit_id = OwnedSpeechUnit.new_id()
            unit.attempt += 1
            unit.tts_audio_started = False
            unit.audible_playback_started = False
            unit.failed = False
            unit.queued_at = self._time()
            # Ignore late ErrorFrames from the prior silent context; the start
            # watchdog covers silence on this new attempt.
            unit.ignore_error_frames = True
            unit.recovery_gate = False
            self.pending = unit
            self._arm_start_watchdog(unit)
            await self._host._emit_tts_speak_frame(unit.text)
            return

        unit.failed = True
        unit.recovery_gate = False
        self.retry_exhaustion += 1
        if unit.kind is SpeechUnitKind.NARRATION:
            self.failed_narration_units += 1
            self.audio_warning = "Some lesson audio could not be played; continuing."
        else:
            self.failed_answer_units += 1
            self.audio_warning = "Answer audio could not be played."
        self.sync_counters_to_host()
        await self._host._on_tts_unit_exhausted(unit, cause=cause)

    def _arm_start_watchdog(self, unit: OwnedSpeechUnit) -> None:
        timeout = self.config.first_audio_timeout_s
        unit_id = unit.unit_id
        attempt = unit.attempt

        async def _fire() -> None:
            try:
                await self._sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._host._on_tts_start_timeout(unit_id, attempt)

        unit.start_watchdog_task = self._create_task(_fire())

    def _arm_completion_watchdog(self, unit: OwnedSpeechUnit) -> None:
        timeout = self.config.completion_timeout_for_chars(len(unit.text))
        unit_id = unit.unit_id
        attempt = unit.attempt

        async def _fire() -> None:
            try:
                await self._sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._host._on_tts_completion_timeout(unit_id, attempt)

        unit.completion_watchdog_task = self._create_task(_fire())
