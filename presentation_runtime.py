"""Pipecat lesson runtime adapter with deterministic segment-level narration."""

from __future__ import annotations

import asyncio
import itertools
from enum import Enum, auto
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Sequence

from lesson_controller import (
    InvalidLessonTransition,
    LessonController,
    LessonEffect,
    LessonEvent,
    LessonEventType,
    LessonMode,
    LessonState,
    NarrationCursor,
    TransitionResult,
)
from narration_plan import (
    DEFAULT_MAX_SEGMENT_CHARACTERS,
    NarrationGenerationId,
    NarrationPlan,
    SegmentStatus,
    build_narration_plan,
    invalidate_plan,
    new_generation_id,
)
from prompt_registry import LoadedPrompt, load_active_tutor_prompt
from safety_policy import (
    PolicyDecision,
    SafetyDecision,
    SafetyEvent,
    SafetyStatus,
    TEMPLATES,
    template_text,
)

# Active tutor system prompt is loaded from the version-controlled registry.
_ACTIVE_TUTOR: LoadedPrompt = load_active_tutor_prompt()
BASE_TUTOR_PROMPT = _ACTIVE_TUTOR.text
ACTIVE_TUTOR_PROMPT_VERSION = _ACTIVE_TUTOR.record.version
ACTIVE_TUTOR_PROMPT_HASH = _ACTIVE_TUTOR.content_hash

QA_TRANSITION_PROMPT = (
    "The formal slide presentation is complete. You are now in open Q&A mode. "
    "Warmly invite the students to ask questions about any part of the natural "
    "disasters lesson. Do not say goodbye or end the session. Wait for questions "
    "and answer them helpfully."
)

TTS_INSTRUCTIONS = (
    "You are a calm, clear science tutor speaking to students. "
    "Use a moderately paced, friendly voice. Enunciate clearly."
)


class OutputPurpose(Enum):
    """Explicit purpose of the current bot audio/output generation."""

    NONE = auto()
    SLIDE_NARRATION = auto()
    RESUMED_NARRATION = auto()
    INTERRUPTION_ANSWER = auto()
    QA_TRANSITION = auto()
    QA_RESPONSE = auto()
    SAFETY_REDIRECT = auto()
    SAFETY_MESSAGE = auto()


class FrameSink(Protocol):
    """Narrow async adapter around PipelineTask.queue_frames (and fakes)."""

    async def queue_frames(self, frames: Sequence[Any]) -> None: ...


StateChangedCallback = Callable[[], Awaitable[None]]
_NARRATION_PURPOSES = frozenset(
    {OutputPurpose.SLIDE_NARRATION, OutputPurpose.RESUMED_NARRATION}
)
_STOPPABLE_PURPOSES = frozenset(
    {
        OutputPurpose.SLIDE_NARRATION,
        OutputPurpose.RESUMED_NARRATION,
        OutputPurpose.INTERRUPTION_ANSWER,
        OutputPurpose.QA_TRANSITION,
        OutputPurpose.QA_RESPONSE,
    }
)


class PresentationRuntime:
    """Per-session controller adapter and owner of narration playback progress.

    The runtime can resume only at segment boundaries because Pipecat's
    ``BotStoppedSpeakingFrame`` does not report an exact playback offset.
    """

    def __init__(
        self,
        *,
        slide_prompts: Sequence[str],
        frame_sink: FrameSink,
        slide_count: Optional[int] = None,
        controller: Optional[LessonController] = None,
        interruption_frame_factory: Optional[Any] = None,
        messages_append_frame_factory: Optional[Any] = None,
        on_state_changed: Optional[StateChangedCallback] = None,
        narration_max_characters: int = DEFAULT_MAX_SEGMENT_CHARACTERS,
    ) -> None:
        if not slide_prompts:
            raise ValueError("slide_prompts must be non-empty")
        count = slide_count if slide_count is not None else len(slide_prompts)
        if count != len(slide_prompts):
            raise ValueError("slide_count must match len(slide_prompts)")
        if narration_max_characters < 40:
            raise ValueError("narration_max_characters must be >= 40")

        self._slide_prompts: List[str] = list(slide_prompts)
        self._frame_sink = frame_sink
        self._controller = controller or LessonController(slide_count=count)
        self._narration_max_characters = narration_max_characters
        self._id_counter = itertools.count(1)
        self._lesson_started = False
        self._session_ended = False
        self._output_purpose = OutputPurpose.NONE
        self._bot_speaking = False
        self._utterance_audible = False
        self._suppress_next_bot_stopped = False
        self._active_utterance_id = 0
        self._qa_transition_queued = False
        self._presented_slide_instructions: set[int] = set()
        self._lock = asyncio.Lock()
        self._on_state_changed = on_state_changed
        self._safety_status = SafetyStatus.NORMAL
        self._safety_notice: Optional[str] = None
        self._safety_events: List[SafetyEvent] = []
        self._observability = None

        self._narration_plan: Optional[NarrationPlan] = None
        self._active_generation_id: Optional[NarrationGenerationId] = None
        self._queued_generation_id: Optional[NarrationGenerationId] = None
        self._awaiting_narration_llm = False
        self._resume_segment_pending = False
        self._narration_error: Optional[str] = None

        # Public counters intentionally remain simple integers for offline tests.
        self.plans_created = 0
        self.segments_generated = 0
        self.segments_completed = 0
        self.segment_interruptions = 0
        self.segment_replays = 0
        self.stale_completions_ignored = 0
        self.cancelled_completions_suppressed = 0
        self.narration_errors = 0
        self.segment_char_total = 0

        # Factories allow offline tests without importing Pipecat frames.
        self._interruption_frame_factory = interruption_frame_factory
        self._messages_append_frame_factory = messages_append_frame_factory
        self._tts_speak_frame_factory = None

    def set_observability(self, observability) -> None:
        self._observability = observability

    def set_tts_speak_frame_factory(self, factory) -> None:
        self._tts_speak_frame_factory = factory

    @property
    def safety_status(self) -> SafetyStatus:
        return self._safety_status

    @property
    def safety_notice(self) -> Optional[str]:
        return self._safety_notice

    @property
    def safety_events(self) -> List[SafetyEvent]:
        return list(self._safety_events)

    @property
    def session_ended(self) -> bool:
        return self._session_ended

    @property
    def state(self) -> LessonState:
        return self._controller.state

    @property
    def output_purpose(self) -> OutputPurpose:
        return self._output_purpose

    @property
    def controller(self) -> LessonController:
        return self._controller

    @property
    def narration_progress(self) -> Optional[dict]:
        plan = self._narration_plan
        return None if plan is None else plan.progress().to_public_dict()

    @property
    def narration_plan(self) -> Optional[NarrationPlan]:
        return self._narration_plan

    @property
    def narration_error(self) -> Optional[str]:
        return self._narration_error

    def record_safety_event(self, event: SafetyEvent) -> None:
        self._safety_events.append(event)

    def set_on_state_changed(self, callback: Optional[StateChangedCallback]) -> None:
        self._on_state_changed = callback

    def should_segment_approved_output(self) -> bool:
        """Return whether an approved LLM response belongs to slide narration."""
        return (
            self._output_purpose in _NARRATION_PURPOSES
            and self._awaiting_narration_llm
        )

    async def accept_approved_narration(self, text: str) -> None:
        """Build and begin a segment plan from safety-approved narration text."""
        async with self._lock:
            if not self.should_segment_approved_output():
                return

            cleaned = (text or "").strip()
            if not cleaned:
                self._set_narration_error("empty_narration")
                self._awaiting_narration_llm = False
                await self._notify_state_changed()
                return

            generation_id = self._active_generation_id or new_generation_id()
            try:
                plan = build_narration_plan(
                    slide_index=self._controller.state.cursor.slide_index,
                    text=cleaned,
                    max_characters=self._narration_max_characters,
                    generation_id=generation_id,
                )
            except (TypeError, ValueError):
                self._set_narration_error("narration_plan_failed")
                self._awaiting_narration_llm = False
                await self._notify_state_changed()
                return

            if not plan.segments:
                self._set_narration_error("empty_narration_plan")
                self._awaiting_narration_llm = False
                await self._notify_state_changed()
                return

            self._narration_plan = plan
            self._active_generation_id = plan.generation_id
            self._awaiting_narration_llm = False
            self._resume_segment_pending = False
            self._narration_error = None
            self.plans_created += 1
            self.segments_generated += plan.total_segments
            self.segment_char_total += sum(len(segment.text) for segment in plan.segments)
            await self._queue_active_segment()
            await self._notify_state_changed()

    def _set_narration_error(self, code: str) -> None:
        self._narration_error = code
        self.narration_errors += 1

    def _invalidate_narration_plan(self) -> None:
        invalidate_plan(self._narration_plan)
        self._active_generation_id = new_generation_id()
        self._queued_generation_id = None
        self._awaiting_narration_llm = False
        self._resume_segment_pending = False
        if self._narration_plan is not None:
            self._narration_plan.segment_queued = False

    async def _queue_active_segment(self, replay: bool = False) -> None:
        plan = self._narration_plan
        if (
            plan is None
            or plan.invalidated
            or plan.is_complete
            or plan.generation_id != self._active_generation_id
            or plan.segment_queued
        ):
            return
        text = plan.active_text()
        if text is None:
            return

        plan.active_status = SegmentStatus.ACTIVE
        plan.segment_queued = True
        self._queued_generation_id = plan.generation_id
        self._utterance_audible = False
        self._suppress_next_bot_stopped = False
        self._resume_segment_pending = False
        if replay:
            self.segment_replays += 1
        await self._queue_tts_speak(text)

    def _mark_active_segment_interrupted(self) -> None:
        plan = self._narration_plan
        if (
            plan is None
            or plan.invalidated
            or plan.generation_id != self._active_generation_id
            or plan.active_status is not SegmentStatus.ACTIVE
        ):
            return
        plan.active_status = SegmentStatus.INTERRUPTED
        plan.segment_queued = False
        self._queued_generation_id = None
        self._resume_segment_pending = True
        self.segment_interruptions += 1

    async def _notify_state_changed(self) -> None:
        if self._on_state_changed is not None:
            await self._on_state_changed()

    async def _queue_tts_speak(self, text: str) -> None:
        if self._tts_speak_frame_factory is not None:
            frame = self._tts_speak_frame_factory(text=text)
        else:
            from pipecat.frames.frames import TTSSpeakFrame

            frame = TTSSpeakFrame(text=text)
        await self._frame_sink.queue_frames([frame])

    def _new_event_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._id_counter)}"

    def _logical_cursor(self) -> NarrationCursor:
        cursor = self._controller.state.cursor
        plan = self._narration_plan
        if (
            plan is not None
            and not plan.invalidated
            and plan.slide_index == cursor.slide_index
        ):
            return NarrationCursor(
                slide_index=cursor.slide_index,
                segment_index=plan.active_segment_index,
                word_index=0,
            )
        return cursor

    async def _queue_system_messages(
        self, contents: Sequence[str], *, run_llm: bool = True
    ) -> None:
        messages = [{"role": "system", "content": content} for content in contents]
        if self._messages_append_frame_factory is not None:
            frame = self._messages_append_frame_factory(messages=messages, run_llm=run_llm)
        else:
            from pipecat.frames.frames import LLMMessagesAppendFrame

            frame = LLMMessagesAppendFrame(messages=messages, run_llm=run_llm)
        await self._frame_sink.queue_frames([frame])

    async def _queue_interruption(self) -> None:
        if self._interruption_frame_factory is not None:
            frame = self._interruption_frame_factory()
        else:
            from pipecat.frames.frames import InterruptionFrame

            frame = InterruptionFrame()
        await self._frame_sink.queue_frames([frame])

    async def _dispatch(self, event: LessonEvent) -> TransitionResult:
        previous_mode = self._controller.state.mode
        previous_cursor = self._controller.state.cursor
        result = self._controller.apply(event)
        await self._interpret_effect(result.effect)
        self._note_observability(event, result)
        if (
            result.state.mode is not previous_mode
            or result.state.cursor != previous_cursor
            or result.effect is not LessonEffect.NO_ACTION
        ):
            await self._notify_state_changed()
        return result

    def _note_observability(self, event: LessonEvent, result: TransitionResult) -> None:
        obs = self._observability
        if obs is None:
            return
        try:
            obs.collector.note_lesson_state(
                mode=result.state.mode.name,
                slide_index=result.state.cursor.slide_index,
            )
            if event.type is LessonEventType.USER_INTERRUPTED:
                obs.collector.note_interruption()
            elif event.type is LessonEventType.ANSWER_COMPLETED:
                obs.collector.note_answer()
            elif event.type is LessonEventType.SLIDE_COMPLETED:
                obs.collector.note_slide_completed()
            elif result.effect is LessonEffect.PRESENT_SLIDE:
                obs.collector.note_slide_started()
            elif result.effect is LessonEffect.ENTER_QA:
                obs.collector.note_qa_entry()
        except Exception:  # noqa: BLE001
            obs.collector.note_handled_error()
        try:
            obs.collector.note_narration_snapshot(
                plans_created=self.plans_created,
                segments_generated=self.segments_generated,
                segments_completed=self.segments_completed,
                segment_interruptions=self.segment_interruptions,
                segment_replays=self.segment_replays,
                stale_completions_ignored=self.stale_completions_ignored,
                cancelled_completions_suppressed=self.cancelled_completions_suppressed,
                narration_errors=self.narration_errors,
                segment_char_total=self.segment_char_total,
                resume_accuracy="segment",
            )
        except Exception:  # noqa: BLE001
            pass

    async def _stop_active_narration_for_navigation(self) -> None:
        """Stop audible output before a slide jump; do not complete the prior slide."""
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._suppress_next_bot_stopped = True
            self._utterance_audible = False
            self._output_purpose = OutputPurpose.NONE
            await self._queue_interruption()

    async def _interpret_effect(self, effect: LessonEffect) -> None:
        if effect is LessonEffect.PRESENT_SLIDE:
            await self._begin_slide_narration(resuming=False)
        elif effect is LessonEffect.RESUME_NARRATION:
            await self._begin_slide_narration(resuming=True)
        elif effect is LessonEffect.STOP_NARRATION:
            active_purpose = self._output_purpose
            self._suppress_next_bot_stopped = (
                self._bot_speaking or active_purpose in _STOPPABLE_PURPOSES
            )
            if active_purpose in _NARRATION_PURPOSES:
                self._mark_active_segment_interrupted()
                plan = self._narration_plan
                if plan is not None:
                    plan.segment_queued = False
            self._utterance_audible = False
            self._output_purpose = OutputPurpose.NONE
            await self._queue_interruption()
        elif effect is LessonEffect.BEGIN_ANSWER:
            self._output_purpose = OutputPurpose.INTERRUPTION_ANSWER
        elif effect is LessonEffect.ENTER_QA:
            await self._queue_qa_transition_once()
        elif effect is LessonEffect.SESSION_FINISHED:
            self._output_purpose = OutputPurpose.NONE
            self._session_ended = True
        elif effect is LessonEffect.NO_ACTION:
            return
        else:
            raise RuntimeError(f"Unhandled lesson effect: {effect}")

    async def _begin_slide_narration(self, *, resuming: bool) -> None:
        slide_index = self._controller.state.cursor.slide_index
        self._active_utterance_id += 1
        self._utterance_audible = False
        self._narration_error = None

        if resuming:
            plan = self._narration_plan
            if (
                plan is not None
                and not plan.invalidated
                and not plan.is_complete
                and plan.slide_index == slide_index
                and plan.generation_id == self._active_generation_id
            ):
                self._output_purpose = OutputPurpose.RESUMED_NARRATION
                self._awaiting_narration_llm = False
                replay = plan.active_status is SegmentStatus.INTERRUPTED
                await self._queue_active_segment(replay=replay)
                return

            # Degraded fallback: without a valid plan, regenerate this slide once.
            self._invalidate_narration_plan()
            self._output_purpose = OutputPurpose.RESUMED_NARRATION
            self._awaiting_narration_llm = True
            self._presented_slide_instructions.add(slide_index)
            await self._queue_system_messages([self._slide_prompts[slide_index]])
            return

        self._invalidate_narration_plan()
        self._narration_plan = None
        self._output_purpose = OutputPurpose.SLIDE_NARRATION
        self._awaiting_narration_llm = True
        self._presented_slide_instructions.add(slide_index)
        await self._queue_system_messages([self._slide_prompts[slide_index]])

    async def _queue_qa_transition_once(self) -> None:
        if self._qa_transition_queued:
            return
        self._qa_transition_queued = True
        self._active_utterance_id += 1
        self._utterance_audible = False
        self._output_purpose = OutputPurpose.QA_TRANSITION
        await self._queue_system_messages([QA_TRANSITION_PROMPT])

    async def start_session(self) -> Optional[TransitionResult]:
        """Start the lesson once when the session is ready."""
        async with self._lock:
            if self._lesson_started or self._session_ended:
                return None
            self._lesson_started = True
            return await self._dispatch(
                LessonEvent(
                    type=LessonEventType.START_LESSON,
                    event_id=self._new_event_id("start-lesson"),
                )
            )

    async def on_bot_started_speaking(self) -> None:
        async with self._lock:
            self._bot_speaking = True
            self._utterance_audible = True
            if (
                self._controller.state.mode is LessonMode.QA_MODE
                and self._output_purpose is OutputPurpose.NONE
            ):
                self._output_purpose = OutputPurpose.QA_RESPONSE

    async def on_bot_stopped_speaking(self) -> None:
        async with self._lock:
            self._bot_speaking = False
            if self._suppress_next_bot_stopped:
                self.cancelled_completions_suppressed += 1
                self._suppress_next_bot_stopped = False
                self._utterance_audible = False
                return
            if not self._utterance_audible:
                return
            self._utterance_audible = False

            purpose = self._output_purpose
            if purpose is OutputPurpose.SAFETY_REDIRECT:
                self._output_purpose = OutputPurpose.NONE
                self._safety_status = SafetyStatus.NORMAL
                self._safety_notice = None
                if self._controller.state.mode is LessonMode.ANSWERING:
                    await self._dispatch(
                        LessonEvent(
                            type=LessonEventType.ANSWER_COMPLETED,
                            event_id=self._new_event_id("safety-redirect-done"),
                        )
                    )
                else:
                    await self._notify_state_changed()
                return
            if purpose is OutputPurpose.SAFETY_MESSAGE:
                self._output_purpose = OutputPurpose.NONE
                await self._notify_state_changed()
                return
            if purpose in _NARRATION_PURPOSES:
                await self._complete_active_segment()
            elif purpose is OutputPurpose.INTERRUPTION_ANSWER:
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.ANSWER_COMPLETED,
                        event_id=self._new_event_id(
                            f"answer-complete-u{self._active_utterance_id}"
                        ),
                    )
                )
            elif purpose in {OutputPurpose.QA_TRANSITION, OutputPurpose.QA_RESPONSE}:
                self._output_purpose = OutputPurpose.NONE

    async def _complete_active_segment(self) -> None:
        plan = self._narration_plan
        if (
            plan is None
            or plan.invalidated
            or plan.generation_id != self._active_generation_id
            or self._queued_generation_id != self._active_generation_id
        ):
            self.stale_completions_ignored += 1
            self._output_purpose = OutputPurpose.NONE
            return

        index = plan.active_segment_index
        if index in plan.completed_indexes or plan.active_status is not SegmentStatus.ACTIVE:
            return

        plan.completed_indexes = plan.completed_indexes | {index}
        plan.segment_queued = False
        plan.active_status = SegmentStatus.COMPLETED
        self._queued_generation_id = None
        self.segments_completed += 1

        if plan.is_complete:
            slide = plan.slide_index
            generation = plan.generation_id
            await self._dispatch(
                LessonEvent(
                    type=LessonEventType.SLIDE_COMPLETED,
                    event_id=f"slide-complete-{slide}-g{generation}-final",
                )
            )
            return

        plan.active_segment_index += 1
        plan.active_status = SegmentStatus.PENDING
        self._resume_segment_pending = True
        if self._controller.state.mode is LessonMode.PRESENTING:
            await self._queue_active_segment()
        await self._notify_state_changed()

    async def on_user_started_speaking(self) -> None:
        async with self._lock:
            if self._safety_status is SafetyStatus.HOLD:
                return
            if self._controller.state.mode is LessonMode.ANSWERING:
                self._suppress_next_bot_stopped = True
                self._utterance_audible = False
                await self._queue_interruption()
                self._output_purpose = OutputPurpose.INTERRUPTION_ANSWER
                return
            if self._controller.state.mode is not LessonMode.PRESENTING:
                return
            if self._output_purpose not in {
                OutputPurpose.SLIDE_NARRATION,
                OutputPurpose.RESUMED_NARRATION,
                OutputPurpose.NONE,
            }:
                return

            self._mark_active_segment_interrupted()
            cursor = self._logical_cursor()
            await self._dispatch(
                LessonEvent(
                    type=LessonEventType.USER_INTERRUPTED,
                    event_id=self._new_event_id(
                        f"interrupt-u{self._active_utterance_id}"
                    ),
                    cursor=cursor,
                )
            )
            await self._dispatch(
                LessonEvent(
                    type=LessonEventType.ANSWER_STARTED,
                    event_id=self._new_event_id(
                        f"answer-start-u{self._active_utterance_id}"
                    ),
                )
            )

    async def handle_blocked_user_input(self, decision: PolicyDecision) -> None:
        """Handle REDIRECT or SAFETY_HOLD after input moderation (no transcript release)."""
        async with self._lock:
            text = (
                template_text(decision.template_key)
                if decision.template_key
                else TEMPLATES["moderation_unavailable"]
            )
            notice = decision.notice or text
            self._suppress_next_bot_stopped = self._bot_speaking or (
                self._output_purpose is not OutputPurpose.NONE
            )
            if self._suppress_next_bot_stopped:
                await self._queue_interruption()

            if decision.decision is SafetyDecision.SAFETY_HOLD:
                self._safety_status = SafetyStatus.HOLD
                self._safety_notice = notice
                if self._controller.state.mode is not LessonMode.PAUSED:
                    if self._controller.state.mode in {
                        LessonMode.PRESENTING,
                        LessonMode.INTERRUPTED,
                        LessonMode.ANSWERING,
                        LessonMode.QA_MODE,
                    }:
                        await self._dispatch(
                            LessonEvent(
                                type=LessonEventType.PAUSE_REQUESTED,
                                event_id=self._new_event_id("safety-hold-pause"),
                                cursor=self._logical_cursor(),
                            )
                        )
                self._output_purpose = OutputPurpose.SAFETY_MESSAGE
                self._utterance_audible = False
                await self._queue_tts_speak(text)
                await self._notify_state_changed()
                return

            self._safety_status = SafetyStatus.REDIRECTING
            self._safety_notice = notice
            self._output_purpose = OutputPurpose.SAFETY_REDIRECT
            self._active_utterance_id += 1
            self._utterance_audible = False
            await self._queue_tts_speak(text)
            await self._notify_state_changed()

    async def pause(self, cursor: Optional[NarrationCursor] = None) -> TransitionResult:
        async with self._lock:
            if self._safety_status is SafetyStatus.HOLD:
                raise InvalidLessonTransition(
                    "Pause is not available during a safety hold",
                    mode=self._controller.state.mode,
                    event_type=LessonEventType.PAUSE_REQUESTED,
                )
            return await self._dispatch(
                LessonEvent(
                    type=LessonEventType.PAUSE_REQUESTED,
                    event_id=self._new_event_id("pause"),
                    cursor=cursor or self._logical_cursor(),
                )
            )

    async def resume(self) -> TransitionResult:
        async with self._lock:
            if self._safety_status is SafetyStatus.HOLD:
                raise InvalidLessonTransition(
                    "Resume is not available during a safety hold",
                    mode=self._controller.state.mode,
                    event_type=LessonEventType.RESUME_REQUESTED,
                )
            return await self._dispatch(
                LessonEvent(
                    type=LessonEventType.RESUME_REQUESTED,
                    event_id=self._new_event_id("resume"),
                )
            )

    async def go_to_slide(self, slide_index: int) -> TransitionResult:
        async with self._lock:
            if self._safety_status is SafetyStatus.HOLD:
                raise InvalidLessonTransition(
                    "Slide navigation is not available during a safety hold",
                    mode=self._controller.state.mode,
                    event_type=LessonEventType.GOTO_SLIDE_REQUESTED,
                )
            state = self._controller.state
            if state.mode not in {LessonMode.PRESENTING, LessonMode.QA_MODE}:
                raise InvalidLessonTransition(
                    "GOTO_SLIDE_REQUESTED is only valid from PRESENTING or QA_MODE",
                    mode=state.mode,
                    event_type=LessonEventType.GOTO_SLIDE_REQUESTED,
                )
            if slide_index < 0 or slide_index >= state.slide_count:
                raise InvalidLessonTransition(
                    f"slide_index {slide_index} is outside valid range "
                    f"[0, {state.slide_count})",
                    mode=state.mode,
                    event_type=LessonEventType.GOTO_SLIDE_REQUESTED,
                )
            self._invalidate_narration_plan()
            await self._stop_active_narration_for_navigation()
            self._presented_slide_instructions.discard(slide_index)
            return await self._dispatch(
                LessonEvent(
                    type=LessonEventType.GOTO_SLIDE_REQUESTED,
                    event_id=self._new_event_id(f"goto-{slide_index}"),
                    slide_index=slide_index,
                )
            )

    async def end_session(self) -> Optional[TransitionResult]:
        async with self._lock:
            self._invalidate_narration_plan()
            if self._session_ended or self._controller.state.mode is LessonMode.FINISHED:
                return None
            if self._controller.state.mode is LessonMode.IDLE:
                self._session_ended = True
                return None
            try:
                return await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.END_SESSION,
                        event_id=self._new_event_id("end-session"),
                    )
                )
            except InvalidLessonTransition:
                self._session_ended = True
                return None


class RecordingFrameSink:
    """Test double that records queued frames without Pipecat or network I/O."""

    def __init__(self) -> None:
        self.frames: List[Any] = []

    async def queue_frames(self, frames: Sequence[Any]) -> None:
        self.frames.extend(frames)

    @property
    def system_messages(self) -> List[str]:
        messages: List[str] = []
        for frame in self.frames:
            frame_messages = getattr(frame, "messages", None)
            if not frame_messages:
                continue
            for message in frame_messages:
                if message.get("role") == "system":
                    messages.append(message["content"])
        return messages

    @property
    def tts_texts(self) -> List[str]:
        texts: List[str] = []
        for frame in self.frames:
            if (
                type(frame).__name__ == "TTSSpeakFrame"
                or getattr(frame, "kind", None) == "tts_speak"
            ):
                text = getattr(frame, "text", None)
                if isinstance(text, str):
                    texts.append(text)
        return texts

    @property
    def interruption_count(self) -> int:
        return sum(
            1
            for frame in self.frames
            if type(frame).__name__ == "InterruptionFrame"
            or getattr(frame, "kind", None) == "interruption"
        )


def make_test_append_frame(*, messages: list, run_llm: bool = True) -> Any:
    """Minimal stand-in for LLMMessagesAppendFrame used by offline tests."""

    class _Append:
        def __init__(self) -> None:
            self.messages = messages
            self.run_llm = run_llm

    return _Append()


def make_test_interruption_frame() -> Any:
    class _Interruption:
        kind = "interruption"

    return _Interruption()
