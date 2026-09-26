"""Pipecat lesson runtime adapter with deterministic segment-level narration."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Sequence, Set

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
    DEFAULT_MIN_SEGMENT_CHARACTERS,
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
from slide_narration_prompt import (
    format_slide_narration_instruction,
    strip_slide_narration_instructions,
)
from presentation_tts_delivery import TtsDeliveryController
from tts_unit import SpeechUnitKind, TtsRecoveryConfig, load_tts_recovery_config
from curriculum import slide_title
from classroom_control import (
    ClassroomControlIntent,
    ClassroomControlKind,
)
from lesson_context import (
    build_lesson_context_snapshot,
    lesson_context_message_dict,
)
from narration_prefetch import NarrationPrefetchCache, PrefetchState
from voice_navigation import VoiceNavAction, VoiceNavIntent

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
    "Speak at a natural, lively, friendly conversational pace. "
    "Keep explanations concise. Enunciate clearly without dragging words."
)

NO_ANSWER_CONTINUE_TEXT = "No problem — let's continue."
DEFAULT_NO_ANSWER_TIMEOUT_SECONDS = 10.0
DEFAULT_QA_SILENCE_TIMEOUT_SECONDS = 12.0
QA_FOLLOWUP_TEXT = "Do you have any other questions?"
QA_REMINDER_TEXT = (
    "I didn't hear anything. If you have another question, you can ask now. "
    "Otherwise, we can finish here."
)
QA_CLOSING_TEXT = (
    "It sounds like there are no more questions. Thank you for learning with me "
    "today. You can return anytime if you would like to continue."
)
REPEAT_UNAVAILABLE_TEXT = (
    "I don't have a section to replay right now. Please ask your question, "
    "or say continue when you are ready."
)
POST_ANSWER_INVITE_MID_SLIDE = (
    "Is that clear, or do you have another question? Otherwise, I can continue."
)
POST_ANSWER_INVITE_CHECKPOINT = (
    "Is everything clear, or do you have another question? Otherwise, we can move on."
)
# Back-compat alias used by older tests.
POST_ANSWER_INVITE_TEXT = POST_ANSWER_INVITE_MID_SLIDE
POST_ANSWER_REMINDER_TEXT = (
    "Say continue when you're ready, or ask another question."
)
CHECKPOINT_REMINDER_TEXT = (
    "When you're ready, say continue to move on, or ask a question."
)
DEFAULT_LESSON_FOLLOWUP_WAIT_SECONDS = 12.0
RETURN_ORIGIN_CLARIFY_TEXT = (
    "I don't have a saved place to return to. Which slide number should I go to?"
)
STAY_ACK_TEXT = "Okay, we'll stay on this slide."
QUESTION_PREAMBLE_TEXT = "Of course. What would you like to ask?"
SLIDE1_TO_SLIDE2_TRANSITION = (
    "Let's begin with what natural disasters are."
)


class OutputPurpose(Enum):
    """Explicit purpose of the current bot audio/output generation."""

    NONE = auto()
    SLIDE_NARRATION = auto()
    RESUMED_NARRATION = auto()
    INTERRUPTION_ANSWER = auto()
    QA_TRANSITION = auto()
    QA_RESPONSE = auto()
    QA_FOLLOWUP = auto()
    QA_REMINDER = auto()
    QA_CLOSING = auto()
    SAFETY_REDIRECT = auto()
    SAFETY_MESSAGE = auto()
    NO_ANSWER_CONTINUE = auto()
    CONTROL_RESPONSE = auto()
    POST_ANSWER_INVITE = auto()
    POST_ANSWER_REMINDER = auto()
    NAV_ACK = auto()
    CLARIFY = auto()
    SLIDE_CHECKPOINT = auto()
    SLIDE_TRANSITION = auto()
    QUESTION_GATE = auto()


class QaWindDownStage(Enum):
    """Deterministic Q&A silence wind-down stages."""

    IDLE = auto()
    WAITING_AFTER_PROMPT = auto()
    REMINDER_PLAYED = auto()
    CLOSING = auto()
    DONE = auto()


class PostAnswerHoldStage(Enum):
    """Hold after a lesson interruption answer (not Q&A wind-down)."""

    IDLE = auto()
    WAITING = auto()
    REMINDED = auto()


class SlideCheckpointStage(Enum):
    """Wait after instructional slides 2–7 complete (not slide 1 or 8)."""

    IDLE = auto()
    WAITING = auto()
    REMINDED = auto()


class AnswerHoldContext(Enum):
    """Where a content question was asked — selects follow-up invite wording."""

    MID_SLIDE = auto()
    CHECKPOINT = auto()


@dataclass
class LessonDetourOrigin:
    """One-level navigation return point (application-owned)."""

    slide_index: int
    segment_index: int
    generation_id: Optional[str] = None


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
        OutputPurpose.QA_FOLLOWUP,
        OutputPurpose.QA_REMINDER,
        OutputPurpose.QA_CLOSING,
        OutputPurpose.NO_ANSWER_CONTINUE,
        OutputPurpose.CONTROL_RESPONSE,
        OutputPurpose.POST_ANSWER_INVITE,
        OutputPurpose.POST_ANSWER_REMINDER,
        OutputPurpose.NAV_ACK,
        OutputPurpose.CLARIFY,
        OutputPurpose.SLIDE_CHECKPOINT,
        OutputPurpose.SLIDE_TRANSITION,
        OutputPurpose.QUESTION_GATE,
    }
)
_QA_SPEECH_PURPOSES = frozenset(
    {
        OutputPurpose.QA_TRANSITION,
        OutputPurpose.QA_RESPONSE,
        OutputPurpose.QA_FOLLOWUP,
        OutputPurpose.QA_REMINDER,
        OutputPurpose.QA_CLOSING,
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
        messages_transform_frame_factory: Optional[Any] = None,
        on_state_changed: Optional[StateChangedCallback] = None,
        narration_max_characters: int = DEFAULT_MAX_SEGMENT_CHARACTERS,
        narration_min_characters: int = DEFAULT_MIN_SEGMENT_CHARACTERS,
        no_answer_timeout_seconds: float = DEFAULT_NO_ANSWER_TIMEOUT_SECONDS,
        qa_silence_timeout_seconds: float = DEFAULT_QA_SILENCE_TIMEOUT_SECONDS,
        lesson_followup_wait_seconds: float = DEFAULT_LESSON_FOLLOWUP_WAIT_SECONDS,
        tts_recovery_config: TtsRecoveryConfig | None = None,
        time_fn=None,
        create_task=None,
        sleep_fn=None,
    ) -> None:
        if not slide_prompts:
            raise ValueError("slide_prompts must be non-empty")
        count = slide_count if slide_count is not None else len(slide_prompts)
        if count != len(slide_prompts):
            raise ValueError("slide_count must match len(slide_prompts)")
        if narration_max_characters < 40:
            raise ValueError("narration_max_characters must be >= 40")
        if narration_min_characters < 1:
            raise ValueError("narration_min_characters must be >= 1")

        self._slide_prompts: List[str] = list(slide_prompts)
        self._frame_sink = frame_sink
        self._controller = controller or LessonController(slide_count=count)
        self._narration_max_characters = narration_max_characters
        self._narration_min_characters = narration_min_characters
        # Allow sub-second values in offline tests; production env loader clamps 5–30s.
        self._no_answer_timeout_s = max(0.05, float(no_answer_timeout_seconds))
        self._qa_silence_timeout_s = max(0.05, float(qa_silence_timeout_seconds))
        self._lesson_followup_wait_s = max(0.05, float(lesson_followup_wait_seconds))
        self._id_counter = itertools.count(1)
        self._lesson_started = False
        self._session_ended = False
        self._output_purpose = OutputPurpose.NONE
        self._bot_speaking = False
        self._utterance_audible = False
        # Only suppress BotStopped when an audible playback was cancelled and a
        # matching stop is therefore expected. Pre-audio cancels must not increment.
        # _cancel_stop_pending prevents double-counting the same in-flight speech.
        self._expected_suppressed_stops = 0
        self._cancel_stop_pending = False
        self._speaking_utterance_id: Optional[int] = None
        self._active_utterance_id = 0
        self._answer_speech_units_remaining = 0
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

        self._awaiting_student_reply = False
        self._pending_narration_after_wait = False
        self._continue_after_no_answer = False
        self._no_answer_task: Optional[asyncio.Task] = None
        self._no_answer_armed_once_for_qa = False
        self._qa_wind_down_stage = QaWindDownStage.IDLE
        self._qa_silence_task: Optional[asyncio.Task] = None
        self._qa_followup_queued = False
        self._qa_closing_started = False
        self._qa_session_finish_pending = False
        self._last_replay_segment_index: Optional[int] = None
        self._skip_next_resume_narration = False
        self._post_answer_hold_stage = PostAnswerHoldStage.IDLE
        self._post_answer_hold_task: Optional[asyncio.Task] = None
        self._post_answer_reminder_sent = False
        self._enter_post_answer_hold_on_resume = False
        self._answer_hold_context = AnswerHoldContext.MID_SLIDE
        self._slide_checkpoint_stage = SlideCheckpointStage.IDLE
        self._slide_checkpoint_task: Optional[asyncio.Task] = None
        self._slide_checkpoint_reminder_sent = False
        self._checkpoint_slide_index: Optional[int] = None
        self._question_reference_slide: Optional[int] = None  # 1-based
        self._awaiting_actual_question = False
        self._prefetch: Optional[NarrationPrefetchCache] = None
        self._nav_ack_text: Optional[str] = None
        self._detour_origin: Optional[LessonDetourOrigin] = None
        self._visited_slides: Set[int] = set()
        self._latest_student_question: str = ""
        self._lesson_context_chars = 0
        self._lesson_context_injections = 0
        self.slide_checkpoints = 0
        self.slide_checkpoint_exits = 0
        self.transitions_queued = 0
        self.question_references = 0
        self.continue_resolved_advance = 0
        self.continue_resolved_resume = 0
        self.nav_false_positive_prevented = 0

        # Public counters intentionally remain simple integers for offline tests.
        self.plans_created = 0
        self.segments_generated = 0
        self.segments_completed = 0
        self.segment_interruptions = 0
        self.segment_replays = 0
        self.stale_completions_ignored = 0
        self.cancelled_completions_suppressed = 0
        self.pre_audio_cancel_without_stop = 0
        self.output_safety_cancel_timeouts = 0
        self.narration_errors = 0
        self.segment_char_total = 0
        self.no_answer_continuations = 0
        self.answer_tts_units_emitted = 0
        self.tts_units_queued = 0
        self.tts_no_audio_failures = 0
        self.tts_first_audio_timeouts = 0
        self.tts_completion_timeouts = 0
        self.tts_retry_attempts = 0
        self.tts_retry_successes = 0
        self.tts_retry_exhaustion = 0
        self.tts_failed_narration_units = 0
        self.tts_failed_answer_units = 0
        self.tts_stale_failures_ignored = 0
        self.tts_inferred_completions = 0
        self.tts_retry_races_suppressed = 0
        self.voice_nav_commands = 0
        self.voice_nav_rejections = 0
        self.deterministic_continues = 0
        self.user_acknowledged_segments = 0
        self.deterministic_repeats = 0
        self.qa_reminders = 0
        self.qa_explicit_closes = 0
        self.qa_silence_closes = 0
        self.qa_closing_failures = 0
        self.sessions_finished_normally = 0
        self.control_intents_ambiguous = 0
        self.post_answer_holds = 0
        self.post_answer_reminders = 0
        self.post_answer_hold_exits = 0
        self.lesson_detours_created = 0
        self.lesson_detours_returned = 0
        self.classroom_control_matched = 0
        self.classroom_control_ambiguous = 0
        self.classroom_control_rejected_negation = 0
        self.stale_callbacks_discarded = 0
        self._answer_unit_texts: list[str] = []
        self._answer_unit_index = 0
        self._audio_warning: str | None = None
        self._tts = TtsDeliveryController(
            self,
            config=tts_recovery_config or load_tts_recovery_config(),
            time_fn=time_fn,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )

        # Factories allow offline tests without importing Pipecat frames.
        self._interruption_frame_factory = interruption_frame_factory
        self._messages_append_frame_factory = messages_append_frame_factory
        self._messages_transform_frame_factory = messages_transform_frame_factory
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

    def note_output_safety_cancel_timeout(self) -> None:
        """Content-free counter when output-safety task cancel waits too long."""
        self.output_safety_cancel_timeouts += 1
        obs = self._observability
        if obs is not None:
            try:
                obs.collector.note_answer_stage("output_safety_cancel_timeout")
            except Exception:  # noqa: BLE001
                pass

    def _current_playback_utterance_id(self) -> int:
        if self._speaking_utterance_id is not None:
            return self._speaking_utterance_id
        return self._active_utterance_id

    def _cancel_audible_playback_only(self) -> bool:
        """Expect one suppressed BotStopped only when audio actually started.

        Pre-audio cancellation (LLM buffering, no BotStartedSpeaking) must NOT
        suppress a future answer's BotStoppedSpeaking. Calling this twice for the
        same in-flight utterance must not double-count.
        """
        if self._bot_speaking or self._utterance_audible:
            if not self._cancel_stop_pending:
                self._expected_suppressed_stops += 1
                self._cancel_stop_pending = True
            self._utterance_audible = False
            self._answer_speech_units_remaining = 0
            return True
        self.pre_audio_cancel_without_stop += 1
        self._answer_speech_units_remaining = 0
        return False

    def _advance_utterance_generation(self) -> None:
        """Mint a new utterance id so the next speech is distinct from cancelled ones."""
        self._active_utterance_id += 1

    def begin_moderated_answer_speech(self, unit_count: int) -> None:
        """Register how many TTS units belong to the forthcoming moderated answer."""
        count = max(1, int(unit_count))
        self._advance_utterance_generation()
        self._answer_speech_units_remaining = count
        self.answer_tts_units_emitted += count
        if self._output_purpose is OutputPurpose.NONE:
            mode = self._controller.state.mode
            if mode is LessonMode.ANSWERING:
                self._output_purpose = OutputPurpose.INTERRUPTION_ANSWER
            elif mode is LessonMode.QA_MODE:
                self._output_purpose = OutputPurpose.QA_RESPONSE

    def should_segment_approved_output(self) -> bool:
        """Return whether an approved LLM response belongs to slide narration."""
        return (
            self._output_purpose in _NARRATION_PURPOSES
            and self._awaiting_narration_llm
        )

    def should_accept_llm_spoken_answer(self) -> bool:
        """Whether buffered LLM text may be released to TTS as an answer/Q&A reply.

        Prevents a late answer generation (e.g. after Pause during ANSWERING and
        Resume back to narration) from speaking over resumed segments.
        """
        if self._session_ended:
            return False
        mode = self._controller.state.mode
        if mode is LessonMode.PAUSED or mode is LessonMode.FINISHED:
            return False
        if mode is LessonMode.PRESENTING:
            # Narration owns the channel after return-from-answer / resume.
            return False
        if mode is LessonMode.ANSWERING:
            return True
        if mode is LessonMode.QA_MODE:
            return True
        if mode is LessonMode.INTERRUPTED:
            return True
        # IDLE (and similar): allow unit tests / frames before lesson start.
        return mode is LessonMode.IDLE

    def _cancel_no_answer_wait(self) -> None:
        self._awaiting_student_reply = False
        self._pending_narration_after_wait = False
        task = self._no_answer_task
        self._no_answer_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_qa_silence_wait(self) -> None:
        task = self._qa_silence_task
        self._qa_silence_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_student_wait_timers(self) -> None:
        self._cancel_no_answer_wait()
        self._cancel_qa_silence_wait()
        self._cancel_post_answer_hold_timer()
        self._cancel_slide_checkpoint_timer()

    def _cancel_post_answer_hold_timer(self) -> None:
        task = self._post_answer_hold_task
        self._post_answer_hold_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_slide_checkpoint_timer(self) -> None:
        task = self._slide_checkpoint_task
        self._slide_checkpoint_task = None
        if task is not None and not task.done():
            task.cancel()

    @property
    def in_slide_checkpoint(self) -> bool:
        return self._slide_checkpoint_stage is not SlideCheckpointStage.IDLE

    def set_prefetch_cache(self, cache: NarrationPrefetchCache) -> None:
        self._prefetch = cache

    def mark_student_interaction_priority(self, active: bool) -> None:
        if self._prefetch is not None:
            self._prefetch.mark_interaction_priority(active)

    def _format_transition(self, target_0based: int, *, sequential: bool) -> str:
        display = target_0based + 1
        try:
            title = slide_title(target_0based)
        except Exception:  # noqa: BLE001
            title = f"slide {display}"
        # Soften curriculum titles for speech.
        topic = title.strip()
        if sequential:
            return (
                f"Great, let's move to slide {display}, where we'll look at {topic}."
            )
        return f"Sure, let's move to slide {display}."

    def _checkpoint_prompt_for_slide(self, slide_0based: int) -> str:
        try:
            topic = slide_title(slide_0based)
        except Exception:  # noqa: BLE001
            topic = f"slide {slide_0based + 1}"
        return (
            f"That covers {topic}. Do you have any questions, or shall we move on?"
        )

    def _exit_post_answer_hold(self, *, reason: str) -> None:
        if self._post_answer_hold_stage is PostAnswerHoldStage.IDLE:
            return
        self._cancel_post_answer_hold_timer()
        self._post_answer_hold_stage = PostAnswerHoldStage.IDLE
        self._post_answer_reminder_sent = False
        self._enter_post_answer_hold_on_resume = False
        self.post_answer_hold_exits += 1
        self._note_diag("post_answer_hold_exited", reason=reason)

    def _note_diag(self, event: str, **fields: Any) -> None:
        obs = self._observability
        if obs is None:
            return
        try:
            obs.collector.note_answer_stage(event)
            # Content-free structured fields only when collector supports it.
            note = getattr(obs.collector, "note_classroom_event", None)
            if callable(note):
                note(event, **fields)
        except Exception:  # noqa: BLE001
            pass

    @property
    def in_post_answer_hold(self) -> bool:
        return self._post_answer_hold_stage is not PostAnswerHoldStage.IDLE

    def build_lesson_context_snapshot(self, *, latest_question: str = "") -> str:
        plan = self._narration_plan
        interrupt = self._controller.state.interruption_cursor
        verified: list[int] = []
        acked: list[int] = []
        active_seg = None
        total_seg = None
        if plan is not None and not plan.invalidated:
            verified = sorted(i + 1 for i in plan.completed_indexes)
            acked = sorted(i + 1 for i in plan.user_acknowledged_indexes)
            active_seg = plan.active_segment_index + 1
            total_seg = plan.total_segments
        question = latest_question or self._latest_student_question
        snap = build_lesson_context_snapshot(
            current_slide_1based=self._controller.state.cursor.slide_index + 1,
            slide_count=self._controller.state.slide_count,
            mode=self._controller.state.mode.name,
            interrupted_slide_1based=(
                interrupt.slide_index + 1 if interrupt is not None else None
            ),
            active_segment_1based=active_seg,
            total_segments=total_seg,
            visited_slides_1based=sorted(s + 1 for s in self._visited_slides),
            verified_completed_segments=verified,
            user_acknowledged_segments=acked,
            pending_resume=(
                plan is not None
                and not plan.invalidated
                and plan.active_status is SegmentStatus.INTERRUPTED
            )
            or self.in_post_answer_hold,
            return_origin_slide_1based=(
                self._detour_origin.slide_index + 1
                if self._detour_origin is not None
                else None
            ),
            post_answer_hold=self.in_post_answer_hold,
            slide_checkpoint=self.in_slide_checkpoint,
            question_reference_slide_1based=self._question_reference_slide,
            latest_student_question=question,
        )
        self._lesson_context_chars = len(snap)
        self._lesson_context_injections += 1
        self._note_diag(
            "lesson_context_injected",
            chars=self._lesson_context_chars,
            injections=self._lesson_context_injections,
        )
        return snap

    def note_student_question(self, text: str) -> None:
        self._latest_student_question = (text or "").strip()[:240]

    def _save_detour_origin(self) -> None:
        cursor = self._logical_cursor()
        plan = self._narration_plan
        gen = str(plan.generation_id) if plan is not None else None
        self._detour_origin = LessonDetourOrigin(
            slide_index=cursor.slide_index,
            segment_index=cursor.segment_index,
            generation_id=gen,
        )
        self.lesson_detours_created += 1
        self._note_diag(
            "lesson_detour_created",
            slide=cursor.slide_index + 1,
            segment=cursor.segment_index + 1,
        )

    async def _enter_post_answer_hold(self) -> None:
        """After interruption answer: invite once and wait (no auto-resume)."""
        self._cancel_post_answer_hold_timer()
        self._post_answer_hold_stage = PostAnswerHoldStage.WAITING
        self._post_answer_reminder_sent = False
        self.post_answer_holds += 1
        self._note_diag("post_answer_hold_entered")
        if self._question_reference_slide is not None:
            self._note_diag(
                "question_reference_cleared",
                slide=self._question_reference_slide,
            )
            self._question_reference_slide = None
        self.mark_student_interaction_priority(False)
        if self._answer_hold_context is AnswerHoldContext.CHECKPOINT:
            invite = POST_ANSWER_INVITE_CHECKPOINT
            # Restore checkpoint waiting after the answer invite.
            if self._checkpoint_slide_index is not None:
                self._slide_checkpoint_stage = SlideCheckpointStage.WAITING
        else:
            invite = POST_ANSWER_INVITE_MID_SLIDE
        self._output_purpose = OutputPurpose.POST_ANSWER_INVITE
        self._utterance_audible = False
        self._active_utterance_id += 1
        await self._queue_owned_speech(
            invite,
            kind=SpeechUnitKind.OTHER,
            purpose_name=OutputPurpose.POST_ANSWER_INVITE.name,
        )
        await self._notify_state_changed()

    async def _enter_slide_checkpoint(self, slide_0based: int) -> None:
        self._cancel_slide_checkpoint_timer()
        self._slide_checkpoint_stage = SlideCheckpointStage.WAITING
        self._slide_checkpoint_reminder_sent = False
        self._checkpoint_slide_index = slide_0based
        self.slide_checkpoints += 1
        self._note_diag("slide_checkpoint_entered", slide=slide_0based + 1)
        self._output_purpose = OutputPurpose.SLIDE_CHECKPOINT
        self._utterance_audible = False
        self._active_utterance_id += 1
        await self._queue_owned_speech(
            self._checkpoint_prompt_for_slide(slide_0based),
            kind=SpeechUnitKind.OTHER,
            purpose_name=OutputPurpose.SLIDE_CHECKPOINT.name,
        )
        await self._notify_state_changed()

    def _exit_slide_checkpoint(self, *, reason: str) -> None:
        if self._slide_checkpoint_stage is SlideCheckpointStage.IDLE:
            return
        self._cancel_slide_checkpoint_timer()
        self._slide_checkpoint_stage = SlideCheckpointStage.IDLE
        self._slide_checkpoint_reminder_sent = False
        self._checkpoint_slide_index = None
        self.slide_checkpoint_exits += 1
        self._note_diag("slide_checkpoint_exited", reason=reason)

    def _arm_slide_checkpoint_timer(self) -> None:
        self._cancel_slide_checkpoint_timer()
        if self._slide_checkpoint_stage is SlideCheckpointStage.IDLE:
            return
        if self._session_ended or self._bot_speaking:
            return
        timeout = self._lesson_followup_wait_s
        stage = self._slide_checkpoint_stage

        async def _fire() -> None:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._on_slide_checkpoint_timeout(stage)

        self._slide_checkpoint_task = asyncio.create_task(_fire())

    async def _on_slide_checkpoint_timeout(self, armed: SlideCheckpointStage) -> None:
        async with self._lock:
            if self._slide_checkpoint_stage is not armed:
                return
            if self._session_ended or self._bot_speaking:
                return
            if self._slide_checkpoint_reminder_sent:
                return
            self._slide_checkpoint_reminder_sent = True
            self._slide_checkpoint_stage = SlideCheckpointStage.REMINDED
            self._note_diag("slide_checkpoint_reminder")
            self._output_purpose = OutputPurpose.POST_ANSWER_REMINDER
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_owned_speech(
                CHECKPOINT_REMINDER_TEXT,
                kind=SpeechUnitKind.OTHER,
                purpose_name=OutputPurpose.POST_ANSWER_REMINDER.name,
            )
            await self._notify_state_changed()

    def _arm_post_answer_hold_timer(self) -> None:
        self._cancel_post_answer_hold_timer()
        if self._post_answer_hold_stage is PostAnswerHoldStage.IDLE:
            return
        if self._session_ended or self._bot_speaking:
            return
        timeout = self._lesson_followup_wait_s
        stage = self._post_answer_hold_stage

        async def _fire() -> None:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._on_post_answer_hold_timeout(stage)

        self._post_answer_hold_task = asyncio.create_task(_fire())

    async def _on_post_answer_hold_timeout(self, armed: PostAnswerHoldStage) -> None:
        async with self._lock:
            if self._post_answer_hold_stage is not armed:
                return
            if self._session_ended or self._bot_speaking:
                return
            if self._post_answer_reminder_sent:
                # Remain waiting; do not restart narration.
                return
            self._post_answer_reminder_sent = True
            self._post_answer_hold_stage = PostAnswerHoldStage.REMINDED
            self.post_answer_reminders += 1
            self._note_diag("post_answer_hold_reminder")
            self._output_purpose = OutputPurpose.POST_ANSWER_REMINDER
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_owned_speech(
                POST_ANSWER_REMINDER_TEXT,
                kind=SpeechUnitKind.OTHER,
                purpose_name=OutputPurpose.POST_ANSWER_REMINDER.name,
            )
            await self._notify_state_changed()

    def _arm_no_answer_wait(self, *, continue_narration: bool) -> None:
        """Start a one-shot thinking timer for PRESENTING comprehension checks."""
        self._cancel_no_answer_wait()
        if self._session_ended or self._bot_speaking:
            return
        if self._safety_status is not SafetyStatus.NORMAL:
            return
        mode = self._controller.state.mode
        # Q&A uses the dedicated wind-down timers, not this narration helper.
        if mode is LessonMode.QA_MODE:
            return
        if mode is not LessonMode.PRESENTING:
            return
        self._awaiting_student_reply = True
        self._pending_narration_after_wait = continue_narration
        timeout = self._no_answer_timeout_s

        async def _fire() -> None:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._on_no_answer_timeout()

        self._no_answer_task = asyncio.create_task(_fire())

    async def _on_no_answer_timeout(self) -> None:
        async with self._lock:
            if not self._awaiting_student_reply:
                return
            if self._session_ended or self._bot_speaking:
                self._awaiting_student_reply = False
                return
            if self._safety_status is not SafetyStatus.NORMAL:
                self._awaiting_student_reply = False
                return
            mode = self._controller.state.mode
            if mode is not LessonMode.PRESENTING:
                self._awaiting_student_reply = False
                return
            continue_narration = self._pending_narration_after_wait
            self._awaiting_student_reply = False
            self._pending_narration_after_wait = False
            self._no_answer_task = None
            self._continue_after_no_answer = continue_narration
            self.no_answer_continuations += 1
            self._output_purpose = OutputPurpose.NO_ANSWER_CONTINUE
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_tts_speak(NO_ANSWER_CONTINUE_TEXT)
            await self._notify_state_changed()
            obs = self._observability
            if obs is not None:
                try:
                    obs.collector.note_answer_stage("no_answer_continue")
                except Exception:  # noqa: BLE001
                    pass

    def _arm_qa_silence_wait(self) -> None:
        """Arm stage-1 or stage-2 Q&A silence timer based on wind-down stage."""
        self._cancel_qa_silence_wait()
        if self._session_ended or self._bot_speaking:
            return
        if self._safety_status is not SafetyStatus.NORMAL:
            return
        if self._controller.state.mode is not LessonMode.QA_MODE:
            return
        if self._qa_wind_down_stage in {
            QaWindDownStage.CLOSING,
            QaWindDownStage.DONE,
        }:
            return
        if self._qa_wind_down_stage is QaWindDownStage.IDLE:
            self._qa_wind_down_stage = QaWindDownStage.WAITING_AFTER_PROMPT
        stage = self._qa_wind_down_stage
        timeout = self._qa_silence_timeout_s

        async def _fire() -> None:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            await self._on_qa_silence_timeout(stage)

        self._qa_silence_task = asyncio.create_task(_fire())

    async def _on_qa_silence_timeout(self, armed_stage: QaWindDownStage) -> None:
        async with self._lock:
            if self._qa_silence_task is None and armed_stage is not self._qa_wind_down_stage:
                return
            if self._session_ended or self._bot_speaking:
                return
            if self._controller.state.mode is not LessonMode.QA_MODE:
                return
            if self._qa_wind_down_stage is not armed_stage:
                return
            self._qa_silence_task = None
            if armed_stage is QaWindDownStage.WAITING_AFTER_PROMPT:
                self.qa_reminders += 1
                self._qa_wind_down_stage = QaWindDownStage.REMINDER_PLAYED
                self._output_purpose = OutputPurpose.QA_REMINDER
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    QA_REMINDER_TEXT,
                    kind=SpeechUnitKind.QA,
                    purpose_name=OutputPurpose.QA_REMINDER.name,
                )
                await self._notify_state_changed()
                return
            if armed_stage is QaWindDownStage.REMINDER_PLAYED:
                self.qa_silence_closes += 1
                await self._begin_qa_closing(explicit=False)
                return

    async def _begin_qa_closing(self, *, explicit: bool) -> None:
        if self._qa_closing_started or self._qa_wind_down_stage is QaWindDownStage.DONE:
            return
        self._cancel_qa_silence_wait()
        self._qa_closing_started = True
        self._qa_wind_down_stage = QaWindDownStage.CLOSING
        if explicit:
            self.qa_explicit_closes += 1
        self._tts.invalidate_pending(reason="qa_closing")
        self._answer_unit_texts = []
        self._answer_speech_units_remaining = 0
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._cancel_audible_playback_only()
            await self._queue_interruption()
        self._output_purpose = OutputPurpose.QA_CLOSING
        self._utterance_audible = False
        self._active_utterance_id += 1
        await self._queue_owned_speech(
            QA_CLOSING_TEXT,
            kind=SpeechUnitKind.QA,
            purpose_name=OutputPurpose.QA_CLOSING.name,
        )
        await self._notify_state_changed()

    async def _finish_session_after_qa_closing(self) -> None:
        if self._session_ended or self._controller.state.mode is LessonMode.FINISHED:
            return
        self._qa_wind_down_stage = QaWindDownStage.DONE
        self._qa_session_finish_pending = False
        self.sessions_finished_normally += 1
        await self._dispatch(
            LessonEvent(
                type=LessonEventType.END_SESSION,
                event_id=self._new_event_id("qa-closing-end"),
            )
        )

    async def _queue_owned_speech(
        self,
        text: str,
        *,
        kind: SpeechUnitKind,
        purpose_name: str,
        slide_index: Optional[int] = None,
        narration_generation_id: Optional[str] = None,
        segment_index: Optional[int] = None,
    ) -> None:
        await self._tts.queue_unit(
            text,
            kind=kind,
            purpose_name=purpose_name,
            utterance_id=self._active_utterance_id,
            slide_index=slide_index,
            narration_generation_id=narration_generation_id,
            segment_index=segment_index,
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
                    min_characters=self._narration_min_characters,
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
            if self._prefetch is not None:
                try:
                    await self._prefetch.store_live_approved(
                        self._controller.state.cursor.slide_index, cleaned
                    )
                except Exception:  # noqa: BLE001
                    pass
            await self._queue_active_segment()
            await self._notify_state_changed()

    def _set_narration_error(self, code: str) -> None:
        self._narration_error = code
        self.narration_errors += 1

    def _invalidate_narration_plan(self) -> None:
        self._tts.invalidate_pending(reason="narration_invalidated")
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
        self._advance_utterance_generation()
        self._utterance_audible = False
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

    async def _emit_tts_speak_frame(self, text: str) -> None:
        if self._tts_speak_frame_factory is not None:
            frame = self._tts_speak_frame_factory(text=text)
        else:
            from pipecat.frames.frames import TTSSpeakFrame

            frame = TTSSpeakFrame(text=text)
        await self._frame_sink.queue_frames([frame])

    async def _queue_tts_speak(self, text: str) -> None:
        """Queue narration TTS with ownership metadata and start watchdog."""
        plan = self._narration_plan
        gen = str(plan.generation_id) if plan is not None else None
        seg = plan.active_segment_index if plan is not None else None
        await self._tts.queue_unit(
            text,
            kind=SpeechUnitKind.NARRATION,
            purpose_name=self._output_purpose.name,
            utterance_id=self._active_utterance_id,
            slide_index=self._controller.state.cursor.slide_index,
            narration_generation_id=gen,
            segment_index=seg,
        )

    def _speech_unit_still_current(self, unit) -> bool:
        if self._session_ended or unit.cancelled or unit.stale:
            return False
        if unit.kind is SpeechUnitKind.NARRATION:
            plan = self._narration_plan
            if plan is None or plan.invalidated:
                return False
            if unit.narration_generation_id != str(plan.generation_id):
                return False
            if unit.segment_index != plan.active_segment_index:
                return False
            if self._output_purpose not in _NARRATION_PURPOSES:
                return False
        elif unit.kind in {SpeechUnitKind.ANSWER, SpeechUnitKind.QA}:
            if unit.utterance_id != self._active_utterance_id:
                return False
        return True

    async def _flush_tts_before_retry(self) -> None:
        await self._queue_interruption()

    async def play_moderated_answer_units(self, units: list[str]) -> None:
        """Queue packed answer/Q&A TTS one owned unit at a time."""
        cleaned = [u.strip() for u in units if isinstance(u, str) and u.strip()]
        if not cleaned:
            return
        self.begin_moderated_answer_speech(len(cleaned))
        self._answer_unit_texts = cleaned
        self._answer_unit_index = 0
        await self._queue_next_answer_unit()

    async def _queue_next_answer_unit(self) -> None:
        if self._answer_unit_index >= len(self._answer_unit_texts):
            return
        text = self._answer_unit_texts[self._answer_unit_index]
        kind = (
            SpeechUnitKind.QA
            if self._output_purpose in _QA_SPEECH_PURPOSES
            else SpeechUnitKind.ANSWER
        )
        await self._tts.queue_unit(
            text,
            kind=kind,
            purpose_name=self._output_purpose.name,
            utterance_id=self._active_utterance_id,
            answer_unit_index=self._answer_unit_index,
            answer_unit_total=len(self._answer_unit_texts),
        )

    async def on_tts_error_frame(self, frame, *, tts_processor=None) -> None:
        async with self._lock:
            await self._tts.on_error_frame(frame, tts_processor=tts_processor)
            if self._tts.audio_warning:
                self._audio_warning = self._tts.audio_warning
                await self._notify_state_changed()

    async def _on_tts_start_timeout(self, unit_id: str, attempt: int) -> None:
        async with self._lock:
            unit = self._tts.pending
            if (
                unit is None
                or unit.unit_id != unit_id
                or unit.attempt != attempt
                or unit.audible_playback_started
                or unit.cancelled
                or unit.stale
            ):
                return
            if not self._speech_unit_still_current(unit):
                unit.stale = True
                self._tts.stale_failures_ignored += 1
                self.stale_callbacks_discarded += 1
                self._note_diag("stale_callback_discarded", cause="start_timeout")
                self._tts.sync_counters_to_host()
                self._tts.pending = None
                return
            self._tts.first_audio_timeouts += 1
            self._tts.consecutive_failures += 1
            self._tts.sync_counters_to_host()
            await self._tts._recover_failed_unit(unit, cause="start_timeout")
            if self._tts.audio_warning:
                self._audio_warning = self._tts.audio_warning
                await self._notify_state_changed()

    async def _on_tts_completion_timeout(self, unit_id: str, attempt: int) -> None:
        async with self._lock:
            unit = self._tts.pending
            if (
                unit is None
                or unit.unit_id != unit_id
                or unit.attempt != attempt
                or unit.cancelled
                or unit.stale
                or unit.normal_completion
            ):
                return
            if not unit.audible_playback_started:
                return
            if not self._speech_unit_still_current(unit):
                unit.stale = True
                self._tts.stale_failures_ignored += 1
                self.stale_callbacks_discarded += 1
                self._note_diag("stale_callback_discarded", cause="completion_timeout")
                self._tts.sync_counters_to_host()
                self._tts.pending = None
                return
            # Infer completion conservatively; do not double-complete.
            self._tts.completion_timeouts += 1
            self._tts.inferred_completions += 1
            self._tts.sync_counters_to_host()
            unit.inferred_completion = True
            self._tts.cancel_watchdogs(unit)
            self._tts.pending = None
            self._utterance_audible = True  # allow stop path / direct complete
            purpose = self._output_purpose
            if purpose in _NARRATION_PURPOSES:
                await self._complete_active_segment(playback_inferred=True)
            elif purpose is OutputPurpose.INTERRUPTION_ANSWER:
                await self._finish_answer_unit_or_complete(inferred=True)
            elif purpose in _QA_SPEECH_PURPOSES:
                await self._finish_qa_unit_or_idle(inferred=True)
            elif purpose is OutputPurpose.CONTROL_RESPONSE:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            elif purpose is OutputPurpose.NAV_ACK:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                await self._begin_slide_narration(resuming=False)
            elif purpose is OutputPurpose.POST_ANSWER_INVITE:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                if self.in_post_answer_hold:
                    self._arm_post_answer_hold_timer()
            elif purpose is OutputPurpose.POST_ANSWER_REMINDER:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            elif purpose is OutputPurpose.CLARIFY:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            await self._notify_state_changed()

    async def _finish_answer_unit_or_complete(self, *, inferred: bool = False) -> None:
        if self._answer_speech_units_remaining > 1:
            self._answer_speech_units_remaining -= 1
            self._answer_unit_index += 1
            await self._queue_next_answer_unit()
            return
        self._answer_speech_units_remaining = 0
        self._answer_unit_texts = []
        # Lesson interruption answers enter post-answer hold instead of
        # immediately resuming narration (Iteration 10.6).
        if self._controller.state.mode is LessonMode.ANSWERING:
            self._enter_post_answer_hold_on_resume = True
            self._skip_next_resume_narration = True
        await self._dispatch(
            LessonEvent(
                type=LessonEventType.ANSWER_COMPLETED,
                event_id=self._new_event_id(
                    f"answer-complete-u{self._active_utterance_id}"
                ),
            )
        )
        if self._enter_post_answer_hold_on_resume:
            self._enter_post_answer_hold_on_resume = False
            await self._enter_post_answer_hold()

    async def _finish_qa_unit_or_idle(self, *, inferred: bool = False) -> None:
        purpose = self._output_purpose
        if self._answer_speech_units_remaining > 1:
            self._answer_speech_units_remaining -= 1
            self._answer_unit_index += 1
            await self._queue_next_answer_unit()
            return
        self._answer_speech_units_remaining = 0
        self._answer_unit_texts = []

        if purpose is OutputPurpose.QA_CLOSING:
            self._output_purpose = OutputPurpose.NONE
            await self._finish_session_after_qa_closing()
            return

        if purpose is OutputPurpose.QA_RESPONSE:
            # One concise follow-up, then arm stage-1 silence.
            self._output_purpose = OutputPurpose.QA_FOLLOWUP
            self._utterance_audible = False
            self._active_utterance_id += 1
            self._qa_followup_queued = True
            await self._queue_owned_speech(
                QA_FOLLOWUP_TEXT,
                kind=SpeechUnitKind.QA,
                purpose_name=OutputPurpose.QA_FOLLOWUP.name,
            )
            return

        if purpose is OutputPurpose.QA_REMINDER:
            self._output_purpose = OutputPurpose.NONE
            self._arm_qa_silence_wait()
            return

        # QA_TRANSITION or QA_FOLLOWUP → wait for student / silence wind-down.
        self._output_purpose = OutputPurpose.NONE
        self._qa_followup_queued = False
        if self._qa_wind_down_stage is QaWindDownStage.IDLE:
            self._qa_wind_down_stage = QaWindDownStage.WAITING_AFTER_PROMPT
        elif purpose is OutputPurpose.QA_FOLLOWUP:
            self._qa_wind_down_stage = QaWindDownStage.WAITING_AFTER_PROMPT
        self._arm_qa_silence_wait()

    async def _on_tts_unit_exhausted(self, unit, *, cause: str) -> None:
        self._tts.pending = None
        self._tts.cancel_watchdogs(unit)
        if unit.kind is SpeechUnitKind.NARRATION:
            # Skip failed segment without counting verified completion.
            plan = self._narration_plan
            if plan is None or plan.invalidated:
                return
            index = plan.active_segment_index
            plan.completed_indexes = plan.completed_indexes | {index}
            plan.segment_queued = False
            plan.active_status = SegmentStatus.COMPLETED
            self._queued_generation_id = None
            # Do not increment segments_completed (verified heard).
            if plan.is_complete:
                slide = plan.slide_index
                generation = plan.generation_id
                last_index = self._controller.state.slide_count - 1
                if slide == last_index:
                    await self._dispatch(
                        LessonEvent(
                            type=LessonEventType.SLIDE_COMPLETED,
                            event_id=f"slide-complete-{slide}-g{generation}-final",
                        )
                    )
                    return
                if slide == 0:
                    self._nav_ack_text = SLIDE1_TO_SLIDE2_TRANSITION
                    self.transitions_queued += 1
                    await self._go_to_slide_locked(1)
                    return
                await self._enter_slide_checkpoint(slide)
                return
            plan.active_segment_index += 1
            plan.active_status = SegmentStatus.PENDING
            if self._controller.state.mode is LessonMode.PRESENTING:
                await self._queue_active_segment()
            await self._notify_state_changed()
            return
        # Answer / Q&A: leave ANSWERING / stay in QA safely.
        if unit.kind is SpeechUnitKind.ANSWER:
            self._answer_speech_units_remaining = 0
            self._answer_unit_texts = []
            if self._controller.state.mode is LessonMode.ANSWERING:
                self._enter_post_answer_hold_on_resume = True
                self._skip_next_resume_narration = True
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.ANSWER_COMPLETED,
                        event_id=self._new_event_id("answer-audio-failed"),
                    )
                )
                if self._enter_post_answer_hold_on_resume:
                    self._enter_post_answer_hold_on_resume = False
                    await self._enter_post_answer_hold()
            return
        if unit.kind is SpeechUnitKind.QA:
            self._answer_speech_units_remaining = 0
            self._answer_unit_texts = []
            purpose_name = getattr(unit, "purpose_name", "") or ""
            if purpose_name == OutputPurpose.QA_CLOSING.name or self._qa_closing_started:
                self.qa_closing_failures += 1
                self._output_purpose = OutputPurpose.NONE
                await self._finish_session_after_qa_closing()
                return
            self._output_purpose = OutputPurpose.NONE
            if self._controller.state.mode is LessonMode.QA_MODE:
                if self._qa_wind_down_stage is QaWindDownStage.IDLE:
                    self._qa_wind_down_stage = QaWindDownStage.WAITING_AFTER_PROMPT
                self._arm_qa_silence_wait()
            await self._notify_state_changed()

    @property
    def audio_warning(self) -> str | None:
        return self._audio_warning

    def clear_audio_warning(self) -> None:
        self._audio_warning = None
        self._tts.audio_warning = None

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

    async def _queue_slide_narration_instruction(self, slide_index: int) -> None:
        """Strip prior slide instructions, then append the current-slide curriculum."""
        instruction = format_slide_narration_instruction(
            slide_index=slide_index,
            slide_count=len(self._slide_prompts),
            curriculum_prompt=self._slide_prompts[slide_index],
        )
        frames: List[Any] = []
        if self._messages_transform_frame_factory is not None:
            frames.append(
                self._messages_transform_frame_factory(
                    transform=strip_slide_narration_instructions,
                    run_llm=False,
                )
            )
        else:
            from pipecat.frames.frames import LLMMessagesTransformFrame

            frames.append(
                LLMMessagesTransformFrame(
                    transform=strip_slide_narration_instructions,
                    run_llm=False,
                )
            )
        if self._messages_append_frame_factory is not None:
            frames.append(
                self._messages_append_frame_factory(
                    messages=[{"role": "system", "content": instruction}],
                    run_llm=True,
                )
            )
        else:
            from pipecat.frames.frames import LLMMessagesAppendFrame

            frames.append(
                LLMMessagesAppendFrame(
                    messages=[{"role": "system", "content": instruction}],
                    run_llm=True,
                )
            )
        await self._frame_sink.queue_frames(frames)

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
                obs.collector.note_answer_stage("interruption")
            elif event.type is LessonEventType.ANSWER_STARTED:
                obs.collector.note_answer_stage("answer_started")
            elif event.type is LessonEventType.ANSWER_COMPLETED:
                obs.collector.note_answer()
                obs.collector.note_answer_stage("answer_playback_complete")
                obs.collector.note_answer_stage("return_to_narration")
            elif event.type is LessonEventType.SLIDE_COMPLETED:
                obs.collector.note_slide_completed()
            elif event.type is LessonEventType.PAUSE_REQUESTED and (
                result.effect is LessonEffect.STOP_NARRATION
            ):
                obs.collector.note_pause()
            elif event.type is LessonEventType.RESUME_REQUESTED and (
                result.effect is not LessonEffect.NO_ACTION
            ):
                obs.collector.note_resume()
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
            obs.collector.note_tts_delivery_snapshot(
                units_queued=self.tts_units_queued,
                no_audio_failures=self.tts_no_audio_failures,
                first_audio_timeouts=self.tts_first_audio_timeouts,
                completion_timeouts=self.tts_completion_timeouts,
                retry_attempts=self.tts_retry_attempts,
                retry_successes=self.tts_retry_successes,
                retry_exhaustion=self.tts_retry_exhaustion,
                failed_narration_units=self.tts_failed_narration_units,
                failed_answer_units=self.tts_failed_answer_units,
                stale_failures_ignored=self.tts_stale_failures_ignored,
                inferred_completions=self.tts_inferred_completions,
                retry_races_suppressed=self.tts_retry_races_suppressed,
                voice_nav_commands=self.voice_nav_commands,
                voice_nav_rejections=self.voice_nav_rejections,
                deterministic_continues=self.deterministic_continues,
                user_acknowledged_segments=self.user_acknowledged_segments,
                deterministic_repeats=self.deterministic_repeats,
                qa_reminders=self.qa_reminders,
                qa_explicit_closes=self.qa_explicit_closes,
                qa_silence_closes=self.qa_silence_closes,
                qa_closing_failures=self.qa_closing_failures,
                sessions_finished_normally=self.sessions_finished_normally,
                control_intents_ambiguous=self.control_intents_ambiguous,
                post_answer_holds=self.post_answer_holds,
                post_answer_reminders=self.post_answer_reminders,
                post_answer_hold_exits=self.post_answer_hold_exits,
                lesson_detours_created=self.lesson_detours_created,
                lesson_detours_returned=self.lesson_detours_returned,
                classroom_control_matched=self.classroom_control_matched,
                classroom_control_ambiguous=self.classroom_control_ambiguous,
                classroom_control_rejected_negation=self.classroom_control_rejected_negation,
                stale_callbacks_discarded=self.stale_callbacks_discarded,
                lesson_context_injections=self._lesson_context_injections,
                lesson_context_chars=self._lesson_context_chars,
            )
        except Exception:  # noqa: BLE001
            pass

    async def _stop_active_narration_for_navigation(self) -> None:
        """Stop audible output before a slide jump; do not complete the prior slide."""
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._cancel_audible_playback_only()
            self._output_purpose = OutputPurpose.NONE
            self._advance_utterance_generation()
            await self._queue_interruption()

    async def _interpret_effect(self, effect: LessonEffect) -> None:
        if effect is LessonEffect.PRESENT_SLIDE:
            self._cancel_no_answer_wait()
            self._visited_slides.add(self._controller.state.cursor.slide_index)
            if self._nav_ack_text:
                ack = self._nav_ack_text
                self._nav_ack_text = None
                self._output_purpose = OutputPurpose.NAV_ACK
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    ack,
                    kind=SpeechUnitKind.OTHER,
                    purpose_name=OutputPurpose.NAV_ACK.name,
                )
                return
            await self._begin_slide_narration(resuming=False)
        elif effect is LessonEffect.RESUME_NARRATION:
            self._cancel_no_answer_wait()
            if self._skip_next_resume_narration:
                self._skip_next_resume_narration = False
                return
            await self._begin_slide_narration(resuming=True)
        elif effect is LessonEffect.STOP_NARRATION:
            self._cancel_no_answer_wait()
            self._tts.invalidate_pending(reason="stop_narration")
            active_purpose = self._output_purpose
            # Suppress only if audio actually started; pre-audio cancel must not
            # consume the forthcoming interruption-answer completion.
            self._cancel_audible_playback_only()
            if active_purpose in _NARRATION_PURPOSES:
                self._mark_active_segment_interrupted()
                plan = self._narration_plan
                if plan is not None:
                    plan.segment_queued = False
            self._output_purpose = OutputPurpose.NONE
            self._advance_utterance_generation()
            await self._queue_interruption()
        elif effect is LessonEffect.BEGIN_ANSWER:
            self._cancel_no_answer_wait()
            self._tts.invalidate_pending(reason="begin_answer")
            self._output_purpose = OutputPurpose.INTERRUPTION_ANSWER
            self._answer_speech_units_remaining = 0
            self._answer_unit_texts = []
            self._answer_unit_index = 0
        elif effect is LessonEffect.ENTER_QA:
            self._cancel_all_student_wait_timers()
            self._qa_wind_down_stage = QaWindDownStage.IDLE
            self._qa_closing_started = False
            self._qa_followup_queued = False
            await self._queue_qa_transition_once()
        elif effect is LessonEffect.SESSION_FINISHED:
            self._cancel_all_student_wait_timers()
            self._output_purpose = OutputPurpose.NONE
            self._session_ended = True
            self._qa_wind_down_stage = QaWindDownStage.DONE
        elif effect is LessonEffect.NO_ACTION:
            return
        else:
            raise RuntimeError(f"Unhandled lesson effect: {effect}")

    async def _begin_slide_narration(self, *, resuming: bool) -> None:
        slide_index = self._controller.state.cursor.slide_index
        self._active_utterance_id += 1
        self._utterance_audible = False
        self._narration_error = None
        self._visited_slides.add(slide_index)

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

            # Prefer cache before regenerating via live LLM.
            if await self._try_apply_prefetch_locked(slide_index, purpose=OutputPurpose.RESUMED_NARRATION):
                return
            self._invalidate_narration_plan()
            self._output_purpose = OutputPurpose.RESUMED_NARRATION
            self._awaiting_narration_llm = True
            self._presented_slide_instructions.add(slide_index)
            await self._queue_slide_narration_instruction(slide_index)
            return

        self._invalidate_narration_plan()
        self._narration_plan = None
        self._output_purpose = OutputPurpose.SLIDE_NARRATION
        if await self._try_apply_prefetch_locked(slide_index, purpose=OutputPurpose.SLIDE_NARRATION):
            return
        # Not ready: prioritize single-flight prefetch; await asynchronously.
        if self._prefetch is not None:
            entry = self._prefetch.entry(slide_index)
            if entry.state is PrefetchState.GENERATING or entry.state is PrefetchState.NOT_STARTED:
                self._prefetch.prioritize(slide_index)
                self._awaiting_narration_llm = True
                self._presented_slide_instructions.add(slide_index)
                asyncio.create_task(self._await_prefetch_and_apply(slide_index))
                return
        self._awaiting_narration_llm = True
        self._presented_slide_instructions.add(slide_index)
        await self._queue_slide_narration_instruction(slide_index)

    async def _try_apply_prefetch_locked(
        self, slide_index: int, *, purpose: OutputPurpose
    ) -> bool:
        if self._prefetch is None:
            return False
        text = self._prefetch.get_ready_text(slide_index)
        if not text:
            return False
        self._note_diag("prefetch_cache_hit", slide=slide_index + 1)
        self._output_purpose = purpose
        self._awaiting_narration_llm = False
        # Build plan in-place (already under runtime lock from callers).
        generation_id = self._active_generation_id or new_generation_id()
        try:
            plan = build_narration_plan(
                slide_index=slide_index,
                text=text,
                max_characters=self._narration_max_characters,
                min_characters=self._narration_min_characters,
                generation_id=generation_id,
            )
        except (TypeError, ValueError):
            return False
        if not plan.segments:
            return False
        self._narration_plan = plan
        self._active_generation_id = plan.generation_id
        self.plans_created += 1
        self.segments_generated += plan.total_segments
        self.segment_char_total += sum(len(s.text) for s in plan.segments)
        await self._queue_active_segment()
        await self._notify_state_changed()
        return True

    async def _await_prefetch_and_apply(self, slide_index: int) -> None:
        cache = self._prefetch
        if cache is None:
            return
        text = await cache.ensure_slide(slide_index)
        async with self._lock:
            if self._session_ended:
                return
            if self._controller.state.cursor.slide_index != slide_index:
                return
            if self._narration_plan is not None and not self._narration_plan.invalidated:
                return
            if text:
                self._output_purpose = OutputPurpose.SLIDE_NARRATION
                await self._try_apply_prefetch_locked(
                    slide_index, purpose=OutputPurpose.SLIDE_NARRATION
                )
                return
            # Failed: live LLM fallback.
            self._awaiting_narration_llm = True
            self._presented_slide_instructions.add(slide_index)
            await self._queue_slide_narration_instruction(slide_index)

    async def _queue_qa_transition_once(self) -> None:
        if self._qa_transition_queued:
            return
        self._qa_transition_queued = True
        self._active_utterance_id += 1
        self._utterance_audible = False
        self._output_purpose = OutputPurpose.QA_TRANSITION
        await self._queue_system_messages([QA_TRANSITION_PROMPT])

    async def handle_voice_navigation(self, intent: VoiceNavIntent) -> bool:
        """Apply a deterministic voice navigation intent. Return False if rejected."""
        async with self._lock:
            if self._session_ended or self._safety_status is SafetyStatus.HOLD:
                self.voice_nav_rejections += 1
                return False
            mode = self._controller.state.mode
            if mode is LessonMode.FINISHED:
                self.voice_nav_rejections += 1
                return False

            current = self._controller.state.cursor.slide_index
            target: int | None = None
            if intent.action is VoiceNavAction.NEXT:
                target = current + 1
            elif intent.action is VoiceNavAction.PREVIOUS:
                target = current - 1
            elif intent.action is VoiceNavAction.GOTO:
                target = intent.slide_index
            elif intent.action is VoiceNavAction.REPEAT:
                target = current
            else:
                self.voice_nav_rejections += 1
                return False

            if target is None or target < 0 or target >= self._controller.state.slide_count:
                self.voice_nav_rejections += 1
                self.classroom_control_ambiguous += 1
                self._note_diag("classroom_control_ambiguous", reason="out_of_range")
                return False

            self.classroom_control_matched += 1
            self.voice_nav_commands += 1
            self._note_diag(
                "classroom_control_matched",
                action=intent.action.value,
                target=target + 1,
            )
            self._exit_post_answer_hold(reason="navigation")
            self._exit_slide_checkpoint(reason="navigation")
            self._enter_post_answer_hold_on_resume = False
            self._skip_next_resume_narration = True
            self._awaiting_actual_question = False

            # One-level detour only: keep the first departure origin until returned.
            if target != current and self._detour_origin is None:
                self._save_detour_origin()

            self._tts.invalidate_pending(reason="voice_navigation")
            self._answer_unit_texts = []
            self._answer_speech_units_remaining = 0
            self._cancel_all_student_wait_timers()
            if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
                self._cancel_audible_playback_only()
                await self._queue_interruption()
            self._output_purpose = OutputPurpose.NONE
            self._advance_utterance_generation()

            if mode is LessonMode.ANSWERING or mode is LessonMode.INTERRUPTED:
                try:
                    await self._dispatch(
                        LessonEvent(
                            type=LessonEventType.ANSWER_COMPLETED,
                            event_id=self._new_event_id("voice-nav-drop-answer"),
                        )
                    )
                except InvalidLessonTransition:
                    pass

            # One short application-owned transition (not a confirmation question).
            if intent.action is VoiceNavAction.REPEAT:
                self._nav_ack_text = f"Restarting slide {target + 1}."
            elif target == current:
                self._nav_ack_text = f"Staying on slide {target + 1}."
            else:
                sequential = target == current + 1
                self._nav_ack_text = self._format_transition(
                    target, sequential=sequential
                )
            self.transitions_queued += 1
            self._note_diag("transition_queued", target=target + 1)

            if self._prefetch is not None and target != current:
                self._prefetch.prioritize(target)

            try:
                result = await self._go_to_slide_locked(target)
            except InvalidLessonTransition:
                self._nav_ack_text = None
                self.voice_nav_rejections += 1
                return False
            if self._observability is not None:
                try:
                    self._observability.collector.note_navigation()
                except Exception:  # noqa: BLE001
                    pass
            self._note_diag(
                "navigation_executed",
                target=target + 1,
                from_slide=current + 1,
            )
            return result is not None

    async def handle_classroom_control(self, intent: ClassroomControlIntent) -> bool:
        """Apply a deterministic classroom-control intent. Return False to fall through."""
        if intent is None:
            self.control_intents_ambiguous += 1
            return False

        if intent.kind is ClassroomControlKind.STAY:
            async with self._lock:
                self.classroom_control_rejected_negation += 1
                self.nav_false_positive_prevented += 1
                self._note_diag("classroom_control_rejected_negation")
                self._note_diag("navigation_false_positive_prevented", reason="negation")
                prompt = intent.clarify_prompt or STAY_ACK_TEXT
                self._tts.invalidate_pending(reason="stay")
                if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
                    self._cancel_audible_playback_only()
                    await self._queue_interruption()
                self._output_purpose = OutputPurpose.CLARIFY
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    prompt,
                    kind=SpeechUnitKind.OTHER,
                    purpose_name=OutputPurpose.CLARIFY.name,
                )
                return True

        if intent.kind is ClassroomControlKind.QUESTION_REFERENCE:
            async with self._lock:
                slide_n = intent.reference_slide_1based
                if slide_n is None:
                    self.control_intents_ambiguous += 1
                    return False
                self._question_reference_slide = slide_n
                self._awaiting_actual_question = True
                self.question_references += 1
                self.nav_false_positive_prevented += 1
                self._note_diag(
                    "question_reference_created",
                    slide=slide_n,
                )
                self._note_diag(
                    "navigation_false_positive_prevented",
                    reason="question_reference",
                )
                self._cancel_all_student_wait_timers()
                if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
                    self._cancel_audible_playback_only()
                    await self._queue_interruption()
                self._output_purpose = OutputPurpose.QUESTION_GATE
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    f"Of course. What would you like to ask about slide {slide_n}?",
                    kind=SpeechUnitKind.OTHER,
                    purpose_name=OutputPurpose.QUESTION_GATE.name,
                )
                return True

        if intent.kind is ClassroomControlKind.QUESTION_PREAMBLE:
            async with self._lock:
                self._awaiting_actual_question = True
                self.classroom_control_matched += 1
                self._note_diag("question_preamble")
                self._cancel_all_student_wait_timers()
                if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
                    self._cancel_audible_playback_only()
                    await self._queue_interruption()
                self._output_purpose = OutputPurpose.QUESTION_GATE
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    QUESTION_PREAMBLE_TEXT,
                    kind=SpeechUnitKind.OTHER,
                    purpose_name=OutputPurpose.QUESTION_GATE.name,
                )
                return True

        if intent.kind is ClassroomControlKind.CLARIFY:
            async with self._lock:
                lowered = intent.raw.lower()
                if "do not" in lowered or "don't" in lowered or "dont" in lowered:
                    self.classroom_control_rejected_negation += 1
                    self._note_diag("classroom_control_rejected_negation")
                else:
                    self.classroom_control_ambiguous += 1
                    self._note_diag("classroom_control_ambiguous")
                prompt = intent.clarify_prompt or (
                    "Did you want me to change slides? Say next slide or go to slide 3."
                )
                self._tts.invalidate_pending(reason="clarify")
                if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
                    self._cancel_audible_playback_only()
                    await self._queue_interruption()
                self._output_purpose = OutputPurpose.CLARIFY
                self._utterance_audible = False
                self._active_utterance_id += 1
                await self._queue_owned_speech(
                    prompt,
                    kind=SpeechUnitKind.OTHER,
                    purpose_name=OutputPurpose.CLARIFY.name,
                )
                return True

        if intent.kind is ClassroomControlKind.NAVIGATION:
            if intent.navigation is None:
                self.voice_nav_rejections += 1
                self.control_intents_ambiguous += 1
                return False
            return await self.handle_voice_navigation(intent.navigation)

        async with self._lock:
            if self._session_ended or self._safety_status is SafetyStatus.HOLD:
                self.control_intents_ambiguous += 1
                return False
            mode = self._controller.state.mode
            if mode is LessonMode.FINISHED:
                self.control_intents_ambiguous += 1
                return False

            if intent.kind is ClassroomControlKind.RETURN_ORIGIN:
                return await self._handle_return_origin_locked()

            if intent.kind is ClassroomControlKind.QA_COMPLETION:
                if mode is not LessonMode.QA_MODE:
                    self.control_intents_ambiguous += 1
                    return False
                await self._begin_qa_closing(explicit=True)
                return True

            if intent.kind is ClassroomControlKind.ACKNOWLEDGE:
                return await self._handle_acknowledge_locked()

            if intent.kind is ClassroomControlKind.CONTINUE:
                return await self._handle_continue_locked()

            if intent.kind is ClassroomControlKind.REPEAT:
                return await self._handle_repeat_locked()

            self.control_intents_ambiguous += 1
            return False

    async def _handle_return_origin_locked(self) -> bool:
        origin = self._detour_origin
        if origin is None:
            self.classroom_control_ambiguous += 1
            self._output_purpose = OutputPurpose.CLARIFY
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_owned_speech(
                RETURN_ORIGIN_CLARIFY_TEXT,
                kind=SpeechUnitKind.OTHER,
                purpose_name=OutputPurpose.CLARIFY.name,
            )
            return True

        self.classroom_control_matched += 1
        self.lesson_detours_returned += 1
        self._note_diag(
            "lesson_detour_returned",
            slide=origin.slide_index + 1,
            segment=origin.segment_index + 1,
        )
        self._exit_post_answer_hold(reason="return_origin")
        self._detour_origin = None
        self._nav_ack_text = f"Returning to slide {origin.slide_index + 1}."
        self._tts.invalidate_pending(reason="return_origin")
        self._answer_unit_texts = []
        self._answer_speech_units_remaining = 0
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._cancel_audible_playback_only()
            await self._queue_interruption()
        self._output_purpose = OutputPurpose.NONE
        self._advance_utterance_generation()
        mode = self._controller.state.mode
        if mode is LessonMode.ANSWERING or mode is LessonMode.INTERRUPTED:
            self._skip_next_resume_narration = True
            try:
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.ANSWER_COMPLETED,
                        event_id=self._new_event_id("return-origin-drop-answer"),
                    )
                )
            except InvalidLessonTransition:
                pass
        try:
            result = await self._go_to_slide_locked(origin.slide_index)
        except InvalidLessonTransition:
            self._nav_ack_text = None
            return False
        return result is not None

    async def _leave_answering_for_control(self, *, skip_resume: bool = False) -> None:
        mode = self._controller.state.mode
        self._tts.invalidate_pending(reason="classroom_control")
        self._answer_unit_texts = []
        self._answer_speech_units_remaining = 0
        self._cancel_all_student_wait_timers()
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._cancel_audible_playback_only()
            await self._queue_interruption()
        self._output_purpose = OutputPurpose.NONE
        self._advance_utterance_generation()
        if mode is LessonMode.ANSWERING or mode is LessonMode.INTERRUPTED:
            if skip_resume:
                self._skip_next_resume_narration = True
            try:
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.ANSWER_COMPLETED,
                        event_id=self._new_event_id("control-drop-answer"),
                    )
                )
            except InvalidLessonTransition:
                self._skip_next_resume_narration = False

    async def _handle_acknowledge_locked(self) -> bool:
        """Understood → skip interrupted/current point; do not count as heard."""
        mode = self._controller.state.mode
        if mode not in {
            LessonMode.PRESENTING,
            LessonMode.ANSWERING,
            LessonMode.INTERRUPTED,
        }:
            self.control_intents_ambiguous += 1
            return False

        self._exit_post_answer_hold(reason="acknowledge")
        await self._leave_answering_for_control(skip_resume=True)
        plan = self._narration_plan
        if (
            plan is None
            or plan.invalidated
            or plan.is_complete
            or self._controller.state.mode is not LessonMode.PRESENTING
        ):
            self.control_intents_ambiguous += 1
            return False

        index = plan.active_segment_index
        plan.completed_indexes = plan.completed_indexes | {index}
        plan.user_acknowledged_indexes = plan.user_acknowledged_indexes | {index}
        plan.segment_queued = False
        plan.active_status = SegmentStatus.USER_ACKNOWLEDGED
        self._queued_generation_id = None
        self.user_acknowledged_segments += 1
        # Do NOT increment segments_completed (verified playback).

        if plan.is_complete:
            slide = plan.slide_index
            generation = plan.generation_id
            await self._dispatch(
                LessonEvent(
                    type=LessonEventType.SLIDE_COMPLETED,
                    event_id=f"slide-complete-{slide}-g{generation}-ack",
                )
            )
            return True

        plan.active_segment_index += 1
        plan.active_status = SegmentStatus.PENDING
        self._resume_segment_pending = True
        self._output_purpose = OutputPurpose.RESUMED_NARRATION
        await self._queue_active_segment(replay=False)
        await self._notify_state_changed()
        return True

    async def _handle_continue_locked(self) -> bool:
        """Continue resolves by checkpoint / mid-slide hold / Q&A context."""
        mode = self._controller.state.mode
        if mode is LessonMode.QA_MODE:
            self.classroom_control_matched += 1
            self._note_diag("continue_resolved", action="stay_qa")
            return True
        if mode is LessonMode.FINISHED:
            self.control_intents_ambiguous += 1
            return False

        if mode not in {
            LessonMode.PRESENTING,
            LessonMode.ANSWERING,
            LessonMode.INTERRUPTED,
            LessonMode.PAUSED,
        }:
            self.control_intents_ambiguous += 1
            return False

        self.classroom_control_matched += 1

        # Checkpoint: advance to next slide (never invent slide 9).
        if self.in_slide_checkpoint or (
            self.in_post_answer_hold
            and self._answer_hold_context is AnswerHoldContext.CHECKPOINT
        ):
            self._exit_post_answer_hold(reason="continue")
            current = self._controller.state.cursor.slide_index
            last = self._controller.state.slide_count - 1
            if current >= last:
                self._exit_slide_checkpoint(reason="continue_at_last")
                self._note_diag("continue_resolved", action="stay_last")
                return True
            self._exit_slide_checkpoint(reason="continue")
            target = current + 1
            sequential = target == current + 1
            self._nav_ack_text = self._format_transition(target, sequential=True)
            self.transitions_queued += 1
            self.continue_resolved_advance += 1
            self.deterministic_continues += 1
            self._note_diag("continue_resolved", action="advance", target=target + 1)
            if mode is LessonMode.ANSWERING or mode is LessonMode.INTERRUPTED:
                await self._leave_answering_for_control(skip_resume=True)
            await self._go_to_slide_locked(target)
            return True

        self._exit_post_answer_hold(reason="continue")

        if mode is LessonMode.ANSWERING or mode is LessonMode.INTERRUPTED:
            await self._leave_answering_for_control(skip_resume=False)
            self.deterministic_continues += 1
            self.continue_resolved_resume += 1
            self._note_diag("continue_resolved", action="resume_segment")
            await self._notify_state_changed()
            return True

        if mode is LessonMode.PAUSED:
            await self._leave_answering_for_control(skip_resume=False)
            try:
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.RESUME_REQUESTED,
                        event_id=self._new_event_id("control-resume"),
                        cursor=self._logical_cursor(),
                    )
                )
            except InvalidLessonTransition:
                self.control_intents_ambiguous += 1
                return False
            self.deterministic_continues += 1
            return True

        plan = self._narration_plan
        if plan is None or plan.invalidated or plan.is_complete:
            # Completed instructional slide without active checkpoint marker.
            current = self._controller.state.cursor.slide_index
            last = self._controller.state.slide_count - 1
            if plan is not None and plan.is_complete and current < last:
                target = current + 1
                self._nav_ack_text = self._format_transition(target, sequential=True)
                self.transitions_queued += 1
                self.continue_resolved_advance += 1
                self.deterministic_continues += 1
                self._note_diag("continue_resolved", action="advance", target=target + 1)
                await self._go_to_slide_locked(target)
                return True
            self.deterministic_continues += 1
            self.continue_resolved_resume += 1
            self._output_purpose = OutputPurpose.RESUMED_NARRATION
            await self._begin_slide_narration(resuming=True)
            await self._notify_state_changed()
            return True

        self.deterministic_continues += 1
        self.continue_resolved_resume += 1
        self._note_diag("continue_resolved", action="resume_segment")
        self._tts.invalidate_pending(reason="deterministic_continue")
        if self._bot_speaking or self._output_purpose in _STOPPABLE_PURPOSES:
            self._cancel_audible_playback_only()
            await self._queue_interruption()
        plan.segment_queued = False
        self._queued_generation_id = None
        self._output_purpose = OutputPurpose.RESUMED_NARRATION
        replay = plan.active_status is SegmentStatus.INTERRUPTED
        await self._queue_active_segment(replay=replay)
        await self._notify_state_changed()
        return True

    async def _handle_repeat_locked(self) -> bool:
        """Replay exact moderated segment text; no RAG/LLM."""
        mode = self._controller.state.mode
        if mode in {LessonMode.QA_MODE, LessonMode.FINISHED, LessonMode.IDLE}:
            self.deterministic_repeats += 1
            self._cancel_all_student_wait_timers()
            if self._bot_speaking:
                self._cancel_audible_playback_only()
                await self._queue_interruption()
            self._output_purpose = OutputPurpose.CONTROL_RESPONSE
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_owned_speech(
                REPEAT_UNAVAILABLE_TEXT,
                kind=SpeechUnitKind.OTHER,
                purpose_name=OutputPurpose.CONTROL_RESPONSE.name,
            )
            return True

        await self._leave_answering_for_control(skip_resume=True)
        plan = self._narration_plan
        if plan is None or plan.invalidated or not plan.segments:
            self.deterministic_repeats += 1
            self._output_purpose = OutputPurpose.CONTROL_RESPONSE
            self._utterance_audible = False
            self._active_utterance_id += 1
            await self._queue_owned_speech(
                REPEAT_UNAVAILABLE_TEXT,
                kind=SpeechUnitKind.OTHER,
                purpose_name=OutputPurpose.CONTROL_RESPONSE.name,
            )
            return True

        if self._controller.state.mode is not LessonMode.PRESENTING:
            self.control_intents_ambiguous += 1
            return False

        index = plan.active_segment_index
        if index < 0 or index >= len(plan.segments):
            self.control_intents_ambiguous += 1
            return False

        plan.segment_queued = False
        plan.active_status = SegmentStatus.PENDING
        self._queued_generation_id = None
        self.deterministic_repeats += 1
        self.segment_replays += 1
        self._last_replay_segment_index = index
        self._output_purpose = OutputPurpose.RESUMED_NARRATION
        await self._queue_active_segment(replay=True)
        await self._notify_state_changed()
        return True

    async def prepare_and_start_session(self) -> Optional[TransitionResult]:
        """Ensure slide 1 narration is READY, then start presenting and prefetch."""
        cache = self._prefetch
        if cache is not None:
            await cache.ensure_slide(0)
            cache.start_background(after_slide=0)
        return await self.start_session()

    async def end_session_cleanup_tts(self) -> None:
        self._tts.invalidate_pending(reason="disconnect")
        self._tts.cancel_watchdogs()

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
            self._speaking_utterance_id = self._active_utterance_id
            self._cancel_stop_pending = False
            self._tts.on_audible_start()
            if (
                self._controller.state.mode is LessonMode.QA_MODE
                and self._output_purpose is OutputPurpose.NONE
            ):
                self._output_purpose = OutputPurpose.QA_RESPONSE

    async def on_bot_stopped_speaking(self) -> None:
        async with self._lock:
            self._bot_speaking = False
            self._speaking_utterance_id = None
            if self._expected_suppressed_stops > 0:
                self._expected_suppressed_stops -= 1
                self._cancel_stop_pending = False
                self.cancelled_completions_suppressed += 1
                self._utterance_audible = False
                return
            if not self._utterance_audible:
                return
            self._utterance_audible = False
            self._cancel_stop_pending = False

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
                self._tts.on_normal_completion()
                await self._complete_active_segment()
            elif purpose is OutputPurpose.INTERRUPTION_ANSWER:
                self._tts.on_normal_completion()
                await self._finish_answer_unit_or_complete()
            elif purpose in _QA_SPEECH_PURPOSES:
                self._tts.on_normal_completion()
                await self._finish_qa_unit_or_idle()
            elif purpose is OutputPurpose.CONTROL_RESPONSE:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            elif purpose is OutputPurpose.NAV_ACK:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                await self._begin_slide_narration(resuming=False)
            elif purpose is OutputPurpose.POST_ANSWER_INVITE:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                if self.in_post_answer_hold:
                    self._arm_post_answer_hold_timer()
            elif purpose is OutputPurpose.POST_ANSWER_REMINDER:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                # Remain in hold after reminder; do not resume narration.
            elif purpose is OutputPurpose.SLIDE_CHECKPOINT:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
                if self.in_slide_checkpoint:
                    self._arm_slide_checkpoint_timer()
            elif purpose is OutputPurpose.QUESTION_GATE:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            elif purpose is OutputPurpose.CLARIFY:
                self._tts.on_normal_completion()
                self._output_purpose = OutputPurpose.NONE
            elif purpose is OutputPurpose.NO_ANSWER_CONTINUE:
                self._output_purpose = OutputPurpose.NONE
                if (
                    self._continue_after_no_answer
                    and self._controller.state.mode is LessonMode.PRESENTING
                ):
                    self._continue_after_no_answer = False
                    await self._queue_active_segment()
                else:
                    self._continue_after_no_answer = False

    async def _complete_active_segment(self, *, playback_inferred: bool = False) -> None:
        plan = self._narration_plan
        if (
            plan is None
            or plan.invalidated
            or plan.generation_id != self._active_generation_id
            or self._queued_generation_id != self._active_generation_id
        ):
            self.stale_completions_ignored += 1
            self.stale_callbacks_discarded += 1
            self._note_diag("stale_callback_discarded", cause="segment_complete")
            self._output_purpose = OutputPurpose.NONE
            return

        index = plan.active_segment_index
        if index in plan.completed_indexes or plan.active_status is not SegmentStatus.ACTIVE:
            return

        completed_text = plan.segments[index].text.strip()
        plan.completed_indexes = plan.completed_indexes | {index}
        plan.segment_queued = False
        plan.active_status = SegmentStatus.COMPLETED
        self._queued_generation_id = None
        if not playback_inferred:
            self.segments_completed += 1

        if plan.is_complete:
            slide = plan.slide_index
            generation = plan.generation_id
            last_index = self._controller.state.slide_count - 1
            if slide == last_index:
                await self._dispatch(
                    LessonEvent(
                        type=LessonEventType.SLIDE_COMPLETED,
                        event_id=f"slide-complete-{slide}-g{generation}-final",
                    )
                )
                return
            if slide == 0:
                # Welcome slide: one natural transition, auto-start slide 2.
                self._nav_ack_text = SLIDE1_TO_SLIDE2_TRANSITION
                self.transitions_queued += 1
                self._note_diag("transition_queued", target=2, kind="intro_auto")
                await self._go_to_slide_locked(1)
                return
            # Instructional slides 2–7: checkpoint, do not auto-advance.
            await self._enter_slide_checkpoint(slide)
            return

        plan.active_segment_index += 1
        plan.active_status = SegmentStatus.PENDING
        self._resume_segment_pending = True
        if self._controller.state.mode is LessonMode.PRESENTING:
            # Comprehension-check style segments end with '?'; wait briefly for
            # a student reply before continuing (one-shot timeout).
            if completed_text.endswith("?"):
                self._output_purpose = OutputPurpose.NONE
                self._arm_no_answer_wait(continue_narration=True)
            else:
                await self._queue_active_segment()
        await self._notify_state_changed()

    async def on_user_started_speaking(self) -> None:
        async with self._lock:
            # Genuine speech cancels thinking / Q&A silence / post-answer timers.
            was_awaiting = self._awaiting_student_reply
            pending_narration = self._pending_narration_after_wait
            was_in_hold = self.in_post_answer_hold
            was_in_checkpoint = self.in_slide_checkpoint
            self._cancel_all_student_wait_timers()
            self.mark_student_interaction_priority(True)
            if was_in_hold:
                # New student speech during hold: cancel reminder; keep pending
                # resume until continue/ack/nav or a new answer cycle.
                self._cancel_post_answer_hold_timer()
            if self._awaiting_actual_question:
                # Actual question arrives after preamble/reference gate.
                self._awaiting_actual_question = False
            if was_in_checkpoint:
                self._answer_hold_context = AnswerHoldContext.CHECKPOINT
                # Stay conceptually at checkpoint until continue advances.
                self._cancel_slide_checkpoint_timer()
            else:
                self._answer_hold_context = AnswerHoldContext.MID_SLIDE
            if was_in_hold:
                # New student speech during hold: cancel reminder; keep pending
                # resume until continue/ack/nav or a new answer cycle.
                self._cancel_post_answer_hold_timer()
            if self._safety_status is SafetyStatus.HOLD:
                return
            if self._controller.state.mode is LessonMode.QA_MODE:
                # Student speech cancels wind-down; do not interrupt their turn.
                if self._qa_wind_down_stage not in {
                    QaWindDownStage.CLOSING,
                    QaWindDownStage.DONE,
                }:
                    self._qa_wind_down_stage = QaWindDownStage.IDLE
                    self._qa_closing_started = False
                return
            if self._controller.state.mode is LessonMode.ANSWERING:
                # Only suppress the answer currently playing. Silence in ANSWERING
                # (or a prior pre-audio cancel) must not eat the next completion.
                if self._bot_speaking or self._utterance_audible:
                    self._cancel_audible_playback_only()
                    await self._queue_interruption()
                self._advance_utterance_generation()
                self._output_purpose = OutputPurpose.INTERRUPTION_ANSWER
                self._answer_speech_units_remaining = 0
                return
            if self._controller.state.mode is not LessonMode.PRESENTING:
                return
            if self._output_purpose not in {
                OutputPurpose.SLIDE_NARRATION,
                OutputPurpose.RESUMED_NARRATION,
                OutputPurpose.NONE,
                OutputPurpose.SLIDE_CHECKPOINT,
                OutputPurpose.QUESTION_GATE,
                OutputPurpose.POST_ANSWER_INVITE,
                OutputPurpose.POST_ANSWER_REMINDER,
                OutputPurpose.NAV_ACK,
            }:
                return

            # Speech during a post-question wait is treated as an interruption
            # answer (not as a silent timeout continuation).
            if was_awaiting and pending_narration:
                pass

            if was_in_hold:
                self._exit_post_answer_hold(reason="new_question")

            if not was_in_checkpoint:
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
            audible = self._cancel_audible_playback_only()
            if audible or self._output_purpose is not OutputPurpose.NONE:
                await self._queue_interruption()
            self._advance_utterance_generation()

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
            self._tts.invalidate_pending(reason="pause")
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
            return await self._go_to_slide_locked(slide_index)

    async def _go_to_slide_locked(self, slide_index: int) -> TransitionResult:
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
        """End the lesson session; cancel TTS watchdogs and prefetch."""
        self._tts.invalidate_pending(reason="end_session")
        if self._prefetch is not None:
            await self._prefetch.cancel()
        async with self._lock:
            self._cancel_all_student_wait_timers()
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
            self.kind = "messages_append"

    return _Append()


def make_test_transform_frame(*, transform, run_llm: bool = False) -> Any:
    """Minimal stand-in for LLMMessagesTransformFrame used by offline tests."""

    class _Transform:
        def __init__(self) -> None:
            self.transform = transform
            self.run_llm = run_llm
            self.kind = "messages_transform"

    return _Transform()


def make_test_interruption_frame() -> Any:
    class _Interruption:
        kind = "interruption"

    return _Interruption()
