"""Deterministic lesson presentation state machine.

Framework-independent business logic: no Pipecat, network, audio, or OpenAI.
Slide indexes are zero-based. The narration cursor is a logical checkpoint only;
exact audible/browser audio resume is intentionally out of scope for this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import FrozenSet, Optional


class LessonMode(Enum):
    IDLE = auto()
    PRESENTING = auto()
    INTERRUPTED = auto()
    ANSWERING = auto()
    PAUSED = auto()
    QA_MODE = auto()
    FINISHED = auto()


class LessonEventType(Enum):
    START_LESSON = auto()
    SLIDE_COMPLETED = auto()
    USER_INTERRUPTED = auto()
    ANSWER_STARTED = auto()
    ANSWER_COMPLETED = auto()
    PAUSE_REQUESTED = auto()
    RESUME_REQUESTED = auto()
    GOTO_SLIDE_REQUESTED = auto()
    END_SESSION = auto()


class LessonEffect(Enum):
    PRESENT_SLIDE = auto()
    STOP_NARRATION = auto()
    BEGIN_ANSWER = auto()
    RESUME_NARRATION = auto()
    ENTER_QA = auto()
    SESSION_FINISHED = auto()
    NO_ACTION = auto()


class InvalidLessonTransition(Exception):
    """Raised when an event is not legal for the current lesson state.

    On raise, the controller leaves its previous immutable state unchanged.
    """

    def __init__(self, message: str, *, mode: LessonMode, event_type: LessonEventType):
        self.mode = mode
        self.event_type = event_type
        super().__init__(message)


@dataclass(frozen=True)
class NarrationCursor:
    """Logical resumable position within the lesson (not an audio byte offset)."""

    slide_index: int
    segment_index: int
    word_index: int

    def __post_init__(self) -> None:
        if self.slide_index < 0 or self.segment_index < 0 or self.word_index < 0:
            raise ValueError("NarrationCursor fields must be non-negative")


@dataclass(frozen=True)
class LessonEvent:
    """Explicit control event. event_id must be unique per intended occurrence."""

    type: LessonEventType
    event_id: str
    slide_index: Optional[int] = None
    cursor: Optional[NarrationCursor] = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("LessonEvent.event_id must be a non-empty string")


@dataclass(frozen=True)
class LessonState:
    mode: LessonMode
    cursor: NarrationCursor
    slide_count: int
    mode_before_pause: Optional[LessonMode]
    interruption_cursor: Optional[NarrationCursor]
    processed_event_ids: FrozenSet[str] = field(default_factory=frozenset)
    last_event_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.slide_count < 1:
            raise ValueError("slide_count must be >= 1")
        if self.cursor.slide_index >= self.slide_count:
            raise ValueError(
                f"cursor.slide_index {self.cursor.slide_index} out of range "
                f"for slide_count {self.slide_count}"
            )
        if (
            self.interruption_cursor is not None
            and self.interruption_cursor.slide_index >= self.slide_count
        ):
            raise ValueError("interruption_cursor.slide_index out of lesson range")

    @staticmethod
    def initial(slide_count: int = 8) -> LessonState:
        return LessonState(
            mode=LessonMode.IDLE,
            cursor=NarrationCursor(slide_index=0, segment_index=0, word_index=0),
            slide_count=slide_count,
            mode_before_pause=None,
            interruption_cursor=None,
            processed_event_ids=frozenset(),
            last_event_id=None,
        )


@dataclass(frozen=True)
class TransitionResult:
    state: LessonState
    effect: LessonEffect


_ACTIVE_MODES_FOR_END = frozenset(
    {
        LessonMode.PRESENTING,
        LessonMode.INTERRUPTED,
        LessonMode.ANSWERING,
        LessonMode.PAUSED,
        LessonMode.QA_MODE,
    }
)

_PAUSEABLE_MODES = frozenset(
    {
        LessonMode.PRESENTING,
        LessonMode.INTERRUPTED,
        LessonMode.ANSWERING,
        LessonMode.QA_MODE,
    }
)


class LessonController:
    """Owns lesson mode, slide, and logical narration cursor.

    Apply events via :meth:`apply`. Successful transitions replace the controller's
    immutable state snapshot. Invalid transitions raise
    :class:`InvalidLessonTransition` and leave the previous state unchanged.
    Duplicate ``event_id`` values yield ``NO_ACTION`` without advancing.
    """

    def __init__(self, slide_count: int = 8) -> None:
        self._state = LessonState.initial(slide_count=slide_count)

    @property
    def state(self) -> LessonState:
        return self._state

    def apply(self, event: LessonEvent) -> TransitionResult:
        previous = self._state
        try:
            result = self._transition(previous, event)
        except InvalidLessonTransition:
            # Guarantee no mutation on failure.
            assert self._state is previous
            raise

        self._state = result.state
        return result

    def _transition(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if event.event_id in state.processed_event_ids:
            return TransitionResult(state=state, effect=LessonEffect.NO_ACTION)

        if state.mode is LessonMode.FINISHED:
            raise InvalidLessonTransition(
                "Session is FINISHED; no further mutations are allowed",
                mode=state.mode,
                event_type=event.type,
            )

        handlers = {
            LessonEventType.START_LESSON: self._start_lesson,
            LessonEventType.SLIDE_COMPLETED: self._slide_completed,
            LessonEventType.USER_INTERRUPTED: self._user_interrupted,
            LessonEventType.ANSWER_STARTED: self._answer_started,
            LessonEventType.ANSWER_COMPLETED: self._answer_completed,
            LessonEventType.PAUSE_REQUESTED: self._pause_requested,
            LessonEventType.RESUME_REQUESTED: self._resume_requested,
            LessonEventType.GOTO_SLIDE_REQUESTED: self._goto_slide,
            LessonEventType.END_SESSION: self._end_session,
        }
        handler = handlers[event.type]
        return handler(state, event)

    def _mark(self, state: LessonState, event: LessonEvent, **updates: object) -> LessonState:
        return replace(
            state,
            processed_event_ids=state.processed_event_ids | {event.event_id},
            last_event_id=event.event_id,
            **updates,
        )

    def _validate_cursor(
        self,
        state: LessonState,
        cursor: NarrationCursor,
        event: LessonEvent,
    ) -> None:
        if cursor.slide_index >= state.slide_count:
            raise InvalidLessonTransition(
                f"cursor.slide_index {cursor.slide_index} outside lesson range "
                f"[0, {state.slide_count})",
                mode=state.mode,
                event_type=event.type,
            )

    def _start_lesson(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.IDLE:
            raise InvalidLessonTransition(
                "START_LESSON is only valid from IDLE",
                mode=state.mode,
                event_type=event.type,
            )
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.PRESENTING,
            cursor=NarrationCursor(slide_index=0, segment_index=0, word_index=0),
            mode_before_pause=None,
            interruption_cursor=None,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.PRESENT_SLIDE)

    def _slide_completed(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.PRESENTING:
            raise InvalidLessonTransition(
                "SLIDE_COMPLETED is only valid while PRESENTING",
                mode=state.mode,
                event_type=event.type,
            )
        last_index = state.slide_count - 1
        if state.cursor.slide_index == last_index:
            new_state = self._mark(
                state,
                event,
                mode=LessonMode.QA_MODE,
                cursor=NarrationCursor(
                    slide_index=last_index, segment_index=0, word_index=0
                ),
                interruption_cursor=None,
            )
            return TransitionResult(state=new_state, effect=LessonEffect.ENTER_QA)

        next_slide = state.cursor.slide_index + 1
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.PRESENTING,
            cursor=NarrationCursor(
                slide_index=next_slide, segment_index=0, word_index=0
            ),
        )
        return TransitionResult(state=new_state, effect=LessonEffect.PRESENT_SLIDE)

    def _user_interrupted(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.PRESENTING:
            raise InvalidLessonTransition(
                "USER_INTERRUPTED is only valid while PRESENTING",
                mode=state.mode,
                event_type=event.type,
            )
        cursor = event.cursor if event.cursor is not None else state.cursor
        self._validate_cursor(state, cursor, event)
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.INTERRUPTED,
            cursor=cursor,
            interruption_cursor=cursor,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.STOP_NARRATION)

    def _answer_started(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.INTERRUPTED:
            raise InvalidLessonTransition(
                "ANSWER_STARTED is only valid while INTERRUPTED",
                mode=state.mode,
                event_type=event.type,
            )
        new_state = self._mark(state, event, mode=LessonMode.ANSWERING)
        return TransitionResult(state=new_state, effect=LessonEffect.BEGIN_ANSWER)

    def _answer_completed(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.ANSWERING:
            raise InvalidLessonTransition(
                "ANSWER_COMPLETED is only valid while ANSWERING",
                mode=state.mode,
                event_type=event.type,
            )
        if state.interruption_cursor is None:
            raise InvalidLessonTransition(
                "ANSWER_COMPLETED requires a saved interruption cursor",
                mode=state.mode,
                event_type=event.type,
            )
        restored = state.interruption_cursor
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.PRESENTING,
            cursor=restored,
            interruption_cursor=None,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.RESUME_NARRATION)

    def _pause_requested(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is LessonMode.PAUSED:
            # Idempotent: record event id so retries are NO_ACTION, state unchanged.
            marked = self._mark(state, event)
            return TransitionResult(state=marked, effect=LessonEffect.NO_ACTION)

        if state.mode not in _PAUSEABLE_MODES:
            raise InvalidLessonTransition(
                f"PAUSE_REQUESTED is not valid in mode {state.mode.name}",
                mode=state.mode,
                event_type=event.type,
            )

        cursor = event.cursor if event.cursor is not None else state.cursor
        self._validate_cursor(state, cursor, event)
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.PAUSED,
            cursor=cursor,
            mode_before_pause=state.mode,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.STOP_NARRATION)

    def _resume_requested(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode is not LessonMode.PAUSED:
            # Idempotent when not paused.
            marked = self._mark(state, event)
            return TransitionResult(state=marked, effect=LessonEffect.NO_ACTION)

        restore_mode = state.mode_before_pause
        if restore_mode is None:
            raise InvalidLessonTransition(
                "RESUME_REQUESTED requires mode_before_pause",
                mode=state.mode,
                event_type=event.type,
            )

        if restore_mode is LessonMode.PRESENTING:
            effect = LessonEffect.RESUME_NARRATION
        elif restore_mode is LessonMode.ANSWERING:
            effect = LessonEffect.BEGIN_ANSWER
        elif restore_mode is LessonMode.QA_MODE:
            effect = LessonEffect.ENTER_QA
        elif restore_mode is LessonMode.INTERRUPTED:
            effect = LessonEffect.STOP_NARRATION
        else:
            effect = LessonEffect.NO_ACTION

        new_state = self._mark(
            state,
            event,
            mode=restore_mode,
            mode_before_pause=None,
        )
        return TransitionResult(state=new_state, effect=effect)

    def _goto_slide(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode not in {LessonMode.PRESENTING, LessonMode.QA_MODE}:
            raise InvalidLessonTransition(
                "GOTO_SLIDE_REQUESTED is only valid from PRESENTING or QA_MODE",
                mode=state.mode,
                event_type=event.type,
            )
        if event.slide_index is None:
            raise InvalidLessonTransition(
                "GOTO_SLIDE_REQUESTED requires slide_index",
                mode=state.mode,
                event_type=event.type,
            )
        target = event.slide_index
        if target < 0 or target >= state.slide_count:
            raise InvalidLessonTransition(
                f"slide_index {target} is outside valid range [0, {state.slide_count})",
                mode=state.mode,
                event_type=event.type,
            )
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.PRESENTING,
            cursor=NarrationCursor(
                slide_index=target, segment_index=0, word_index=0
            ),
            interruption_cursor=None,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.PRESENT_SLIDE)

    def _end_session(self, state: LessonState, event: LessonEvent) -> TransitionResult:
        if state.mode not in _ACTIVE_MODES_FOR_END:
            raise InvalidLessonTransition(
                f"END_SESSION is not valid in mode {state.mode.name}",
                mode=state.mode,
                event_type=event.type,
            )
        new_state = self._mark(
            state,
            event,
            mode=LessonMode.FINISHED,
            mode_before_pause=None,
            interruption_cursor=None,
        )
        return TransitionResult(state=new_state, effect=LessonEffect.SESSION_FINISHED)
