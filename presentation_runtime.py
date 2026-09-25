"""Pipecat lesson runtime adapter.

Owns per-session LessonController integration: maps pipeline lifecycle signals to
controller events and translates effects into frame queue actions.

Pipecat-specific I/O stays here; lesson_controller.py remains framework-free.
"""

from __future__ import annotations

import itertools
from enum import Enum, auto
from typing import Any, List, Optional, Protocol, Sequence

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

BASE_TUTOR_PROMPT = (
    "You are a patient and encouraging science tutor teaching students about "
    "natural disasters. Use clear, age-appropriate language. Explain one concept "
    "at a time. When a student interrupts with a question, answer briefly and "
    "clearly, then return to the lesson when instructed. If you are unsure of a "
    "fact, say so instead of inventing information. Avoid graphic or sensational "
    "descriptions of harm, injury, or destruction."
)

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


class FrameSink(Protocol):
    """Narrow async adapter around PipelineTask.queue_frames (and fakes)."""

    async def queue_frames(self, frames: Sequence[Any]) -> None: ...


class PresentationRuntime:
    """Per-WebSocket-session lesson runtime.

    One instance per client session. Not shared globally.
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
    ) -> None:
        if not slide_prompts:
            raise ValueError("slide_prompts must be non-empty")
        count = slide_count if slide_count is not None else len(slide_prompts)
        if count != len(slide_prompts):
            raise ValueError("slide_count must match len(slide_prompts)")

        self._slide_prompts: List[str] = list(slide_prompts)
        self._frame_sink = frame_sink
        self._controller = controller or LessonController(slide_count=count)
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

        # Factories allow offline tests without importing Pipecat frames.
        self._interruption_frame_factory = interruption_frame_factory
        self._messages_append_frame_factory = messages_append_frame_factory

    @property
    def state(self) -> LessonState:
        return self._controller.state

    @property
    def output_purpose(self) -> OutputPurpose:
        return self._output_purpose

    @property
    def controller(self) -> LessonController:
        return self._controller

    def _new_event_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._id_counter)}"

    def _logical_cursor(self) -> NarrationCursor:
        return self._controller.state.cursor

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
        result = self._controller.apply(event)
        await self._interpret_effect(result.effect)
        return result

    async def _interpret_effect(self, effect: LessonEffect) -> None:
        if effect is LessonEffect.PRESENT_SLIDE:
            await self._begin_slide_narration(resuming=False)
        elif effect is LessonEffect.RESUME_NARRATION:
            await self._begin_slide_narration(resuming=True)
        elif effect is LessonEffect.STOP_NARRATION:
            self._suppress_next_bot_stopped = self._bot_speaking or (
                self._output_purpose
                in {
                    OutputPurpose.SLIDE_NARRATION,
                    OutputPurpose.RESUMED_NARRATION,
                    OutputPurpose.INTERRUPTION_ANSWER,
                    OutputPurpose.QA_TRANSITION,
                    OutputPurpose.QA_RESPONSE,
                }
            )
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
        self._suppress_next_bot_stopped = False
        self._utterance_audible = False
        self._output_purpose = (
            OutputPurpose.RESUMED_NARRATION if resuming else OutputPurpose.SLIDE_NARRATION
        )

        if resuming:
            cursor = self._controller.state.cursor
            await self._queue_system_messages(
                [
                    (
                        f"Resume presenting slide index {cursor.slide_index} "
                        f"(logical segment {cursor.segment_index}, word {cursor.word_index}). "
                        "Continue from the saved logical position. Do not restart the whole "
                        "slide unless needed for clarity. Do not invent a different slide."
                    )
                ]
            )
            return

        # Present each slide instruction once per arrival at that index via PRESENT_SLIDE.
        instruction_key = slide_index
        if instruction_key in self._presented_slide_instructions:
            # Re-present after goto resets the set entry below; if still marked, force speak.
            pass
        self._presented_slide_instructions.add(instruction_key)
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
        self._bot_speaking = True
        self._utterance_audible = True
        if (
            self._controller.state.mode is LessonMode.QA_MODE
            and self._output_purpose is OutputPurpose.NONE
        ):
            self._output_purpose = OutputPurpose.QA_RESPONSE

    async def on_bot_stopped_speaking(self) -> None:
        self._bot_speaking = False
        if self._suppress_next_bot_stopped:
            self._suppress_next_bot_stopped = False
            self._utterance_audible = False
            return
        if not self._utterance_audible:
            return
        self._utterance_audible = False

        purpose = self._output_purpose
        if purpose in {OutputPurpose.SLIDE_NARRATION, OutputPurpose.RESUMED_NARRATION}:
            slide = self._controller.state.cursor.slide_index
            utterance = self._active_utterance_id
            await self._dispatch(
                LessonEvent(
                    type=LessonEventType.SLIDE_COMPLETED,
                    event_id=f"slide-complete-{slide}-u{utterance}",
                )
            )
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

    async def on_user_started_speaking(self) -> None:
        if self._controller.state.mode is not LessonMode.PRESENTING:
            return
        if self._output_purpose not in {
            OutputPurpose.SLIDE_NARRATION,
            OutputPurpose.RESUMED_NARRATION,
            OutputPurpose.NONE,
        }:
            return

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
        # Answer phase begins as the pipeline prepares to handle the student turn.
        await self._dispatch(
            LessonEvent(
                type=LessonEventType.ANSWER_STARTED,
                event_id=self._new_event_id(
                    f"answer-start-u{self._active_utterance_id}"
                ),
            )
        )

    async def pause(self, cursor: Optional[NarrationCursor] = None) -> TransitionResult:
        return await self._dispatch(
            LessonEvent(
                type=LessonEventType.PAUSE_REQUESTED,
                event_id=self._new_event_id("pause"),
                cursor=cursor or self._logical_cursor(),
            )
        )

    async def resume(self) -> TransitionResult:
        return await self._dispatch(
            LessonEvent(
                type=LessonEventType.RESUME_REQUESTED,
                event_id=self._new_event_id("resume"),
            )
        )

    async def go_to_slide(self, slide_index: int) -> TransitionResult:
        # Clear prior presentation mark so the target slide instruction is queued again.
        self._presented_slide_instructions.discard(slide_index)
        return await self._dispatch(
            LessonEvent(
                type=LessonEventType.GOTO_SLIDE_REQUESTED,
                event_id=self._new_event_id(f"goto-{slide_index}"),
                slide_index=slide_index,
            )
        )

    async def end_session(self) -> Optional[TransitionResult]:
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
