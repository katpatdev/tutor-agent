"""Pipecat processors for input/output student safety moderation."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional, Set

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    SystemFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from tutor_agent.safety.moderation_service import ModerationClient, SafetyConfig
from tutor_agent.audio.presentation_runtime import OutputPurpose, PresentationRuntime
from tutor_agent.safety.safety_policy import (
    SafetyDecision,
    SafetySource,
    decision_from_failure,
    evaluate_moderation,
    local_distress_or_danger,
    make_safety_event,
    oversized_decision,
    template_text,
)
from tutor_agent.narration.speech_chunking import pack_spoken_units


def _decision_label(decision: SafetyDecision) -> str:
    if decision is SafetyDecision.ALLOW:
        return "allow"
    if decision is SafetyDecision.REDIRECT:
        return "redirect"
    return "hold"


class InputSafetyProcessor(FrameProcessor):
    """Moderate final TranscriptionFrame before the user context aggregator."""

    def __init__(
        self,
        *,
        runtime: PresentationRuntime,
        moderation_client: ModerationClient,
        config: SafetyConfig,
        observability=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._runtime = runtime
        self._client = moderation_client
        self._config = config
        self._observability = observability
        self._seen_fingerprints: Set[str] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame):
            # Do not moderate partials; pass through without treating as final.
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return

            fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if fingerprint in self._seen_fingerprints:
                return
            self._seen_fingerprints.add(fingerprint)

            if len(text) > self._config.max_characters:
                decision = oversized_decision()
                self._runtime.record_safety_event(
                    make_safety_event(
                        decision=decision.decision,
                        source=SafetySource.USER_INPUT,
                        reason=decision.reason,
                        latency_ms=None,
                        fallback_used=True,
                    )
                )
                if self._observability is not None:
                    try:
                        self._observability.record_safety_event(
                            decision="hold",
                            reason_code=decision.reason.value,
                            source="user_input",
                            latency_ms=None,
                            fallback_used=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                await self._runtime.handle_blocked_user_input(decision)
                return

            if self._runtime.session_ended:
                return

            local = local_distress_or_danger(text)
            started = time.perf_counter()
            try:
                result = await self._client.moderate(text)
                latency_ms = (time.perf_counter() - started) * 1000.0
                decision = evaluate_moderation(
                    result, source=SafetySource.USER_INPUT, local_reason=local
                )
                fallback = False
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - started) * 1000.0
                decision = decision_from_failure(exc)
                fallback = True

            if self._runtime.session_ended:
                return

            self._runtime.record_safety_event(
                make_safety_event(
                    decision=decision.decision,
                    source=SafetySource.USER_INPUT,
                    reason=decision.reason,
                    latency_ms=latency_ms,
                    fallback_used=fallback,
                )
            )
            if self._observability is not None:
                try:
                    self._observability.collector.note_answer_stage(
                        "input_moderation_complete"
                    )
                    self._observability.record_safety_event(
                        decision=_decision_label(decision.decision),
                        reason_code=decision.reason.value,
                        source="user_input",
                        latency_ms=latency_ms,
                        fallback_used=fallback,
                    )
                except Exception:  # noqa: BLE001
                    pass

            if decision.decision is SafetyDecision.ALLOW:
                if self._observability is not None:
                    try:
                        self._observability.record_user_utterance(
                            text,
                            lesson_mode=self._runtime.state.mode.name,
                            slide_index=self._runtime.state.cursor.slide_index,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    await self._runtime.record_user_utterance(text)
                except Exception:  # noqa: BLE001
                    pass
                await self.push_frame(frame, direction)
                return

            # Blocked: do not release transcript to aggregator/LLM.
            await self._runtime.handle_blocked_user_input(decision)
            return

        await self.push_frame(frame, direction)


class OutputSafetyProcessor(FrameProcessor):
    """Buffer one full LLM response, moderate, then release to TTS or replace."""

    def __init__(
        self,
        *,
        runtime: PresentationRuntime,
        moderation_client: ModerationClient,
        config: SafetyConfig,
        observability=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._runtime = runtime
        self._client = moderation_client
        self._config = config
        self._observability = observability
        self._buffering = False
        self._parts: list[str] = []
        self._held_control: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # Trusted application templates bypass LLM output gating.
        if isinstance(frame, TTSSpeakFrame):
            if self._observability is not None:
                try:
                    text = getattr(frame, "text", "") or ""
                    if text:
                        self._observability.record_assistant_utterance(
                            text,
                            lesson_mode=self._runtime.state.mode.name,
                            slide_index=self._runtime.state.cursor.slide_index,
                            is_safety_template=True,
                            playback_status="playback_completion_unknown",
                        )
                except Exception:  # noqa: BLE001
                    pass
            await self.push_frame(frame, direction)
            return

        # Interruption must drop any in-flight buffer so a cancelled narration
        # generation cannot later release stale text after an answer starts.
        if isinstance(frame, InterruptionFrame):
            self._buffering = False
            self._parts = []
            self._held_control = []
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._parts = []
            self._held_control = [frame]
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            self._parts.append(frame.text or "")
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._parts)
            start_frame = self._held_control[0] if self._held_control else None
            self._buffering = False
            self._parts = []
            self._held_control = []
            await self._release_or_replace(text, start_frame, frame, direction)
            return

        if self._buffering and isinstance(frame, SystemFrame):
            # Preserve system frames without releasing unmoderated text.
            await self.push_frame(frame, direction)
            return

        if self._buffering:
            # Hold unknown control/data until response completes to avoid leaking text.
            self._held_control.append(frame)
            return

        await self.push_frame(frame, direction)

    async def _release_or_replace(
        self,
        text: str,
        start_frame: Optional[Frame],
        end_frame: Frame,
        direction: FrameDirection,
    ) -> None:
        if len(text) > self._config.max_characters:
            decision = oversized_decision()
            self._runtime.record_safety_event(
                make_safety_event(
                    decision=decision.decision,
                    source=SafetySource.ASSISTANT_OUTPUT,
                    reason=decision.reason,
                    latency_ms=None,
                    fallback_used=True,
                )
            )
            await self.push_frame(
                TTSSpeakFrame(text=template_text("unsafe_output_replacement")),
                direction,
            )
            return

        if self._runtime.session_ended:
            return

        started = time.perf_counter()
        try:
            result = await self._client.moderate(text)
            latency_ms = (time.perf_counter() - started) * 1000.0
            decision = evaluate_moderation(result, source=SafetySource.ASSISTANT_OUTPUT)
            fallback = False
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000.0
            decision = decision_from_failure(exc)
            fallback = True

        if self._runtime.session_ended:
            return

        self._runtime.record_safety_event(
            make_safety_event(
                decision=decision.decision,
                source=SafetySource.ASSISTANT_OUTPUT,
                reason=decision.reason,
                latency_ms=latency_ms,
                fallback_used=fallback,
            )
        )
        if self._observability is not None and decision.decision is not SafetyDecision.ALLOW:
            try:
                self._observability.record_safety_event(
                    decision=_decision_label(decision.decision),
                    reason_code=decision.reason.value,
                    source="assistant_output",
                    latency_ms=latency_ms,
                    fallback_used=fallback,
                )
            except Exception:  # noqa: BLE001
                pass

        if decision.decision is SafetyDecision.ALLOW:
            if self._observability is not None:
                try:
                    self._observability.record_assistant_utterance(
                        text,
                        lesson_mode=self._runtime.state.mode.name,
                        slide_index=self._runtime.state.cursor.slide_index,
                        is_safety_template=False,
                        playback_status="approved_for_tts",
                    )
                except Exception:  # noqa: BLE001
                    pass
            # Segment slide narration through the runtime; do not send the full
            # unsegmented text to TTS. Answers/Q&A release as packed TTSSpeakFrame
            # units (fewer sequential OpenAI TTS round-trips than per-sentence TextFrame).
            if self._runtime.should_segment_approved_output():
                await self._runtime.accept_approved_narration(text)
                return
            # Drop late answer text after Pause→Resume returned to narration.
            if not self._runtime.should_accept_llm_spoken_answer():
                if self._observability is not None:
                    try:
                        self._observability.collector.note_answer_stage(
                            "stale_answer_dropped"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return
            if self._observability is not None:
                try:
                    self._observability.collector.note_answer_stage(
                        "output_moderation_complete"
                    )
                    self._observability.collector.note_answer_stage("tts_start")
                except Exception:  # noqa: BLE001
                    pass
            units = pack_spoken_units(text) if text else []
            if not units and text:
                units = [text]
            await self._runtime.play_moderated_answer_units(units)
            return

        # Replace unsafe LLM output; do not push original text to TTS or assistant path.
        replacement = template_text(
            decision.template_key or "unsafe_output_replacement"
        )
        await self.push_frame(TTSSpeakFrame(text=replacement), direction)
