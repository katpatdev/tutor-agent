"""Deterministic tests for lesson_controller (no Pipecat, network, audio, or OpenAI)."""

from __future__ import annotations

import pytest

from tutor_agent.lesson.lesson_controller import (
    InvalidLessonTransition,
    LessonController,
    LessonEffect,
    LessonEvent,
    LessonEventType,
    LessonMode,
    NarrationCursor,
)


def ev(
    event_type: LessonEventType,
    event_id: str,
    *,
    slide_index: int | None = None,
    cursor: NarrationCursor | None = None,
) -> LessonEvent:
    return LessonEvent(
        type=event_type,
        event_id=event_id,
        slide_index=slide_index,
        cursor=cursor,
    )


def start(controller: LessonController, event_id: str = "start-1") -> None:
    result = controller.apply(ev(LessonEventType.START_LESSON, event_id))
    assert result.effect is LessonEffect.PRESENT_SLIDE
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor.slide_index == 0


def advance_to_slide(controller: LessonController, target_slide: int) -> None:
    """Complete slides until presenting ``target_slide`` (0-based)."""
    assert controller.state.mode is LessonMode.PRESENTING
    while controller.state.cursor.slide_index < target_slide:
        current = controller.state.cursor.slide_index
        result = controller.apply(
            ev(LessonEventType.SLIDE_COMPLETED, f"complete-{current}")
        )
        assert result.effect is LessonEffect.PRESENT_SLIDE
        assert controller.state.cursor.slide_index == current + 1


def reach_qa(controller: LessonController) -> None:
    start(controller)
    for i in range(controller.state.slide_count):
        result = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, f"done-{i}"))
        if i < controller.state.slide_count - 1:
            assert result.effect is LessonEffect.PRESENT_SLIDE
        else:
            assert result.effect is LessonEffect.ENTER_QA
    assert controller.state.mode is LessonMode.QA_MODE


def test_01_initial_state_is_idle() -> None:
    controller = LessonController(slide_count=8)
    assert controller.state.mode is LessonMode.IDLE
    assert controller.state.slide_count == 8
    assert controller.state.cursor == NarrationCursor(0, 0, 0)
    assert controller.state.mode_before_pause is None
    assert controller.state.interruption_cursor is None
    assert controller.state.processed_event_ids == frozenset()


def test_02_start_enters_presenting_on_slide_0() -> None:
    controller = LessonController()
    result = controller.apply(ev(LessonEventType.START_LESSON, "s1"))
    assert result.effect is LessonEffect.PRESENT_SLIDE
    assert result.state.mode is LessonMode.PRESENTING
    assert result.state.cursor == NarrationCursor(0, 0, 0)
    assert controller.state.mode is LessonMode.PRESENTING


def test_03_all_eight_slides_reached_in_order() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    seen = [controller.state.cursor.slide_index]
    for i in range(7):
        result = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, f"c-{i}"))
        assert result.effect is LessonEffect.PRESENT_SLIDE
        assert controller.state.mode is LessonMode.PRESENTING
        seen.append(controller.state.cursor.slide_index)
    assert seen == list(range(8))


def test_04_completing_slide_7_enters_qa_mode() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 7)
    result = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, "final-slide"))
    assert result.effect is LessonEffect.ENTER_QA
    assert controller.state.mode is LessonMode.QA_MODE
    assert controller.state.cursor.slide_index == 7


def test_05_final_slide_does_not_enter_finished() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 7)
    result = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, "final-slide"))
    assert controller.state.mode is LessonMode.QA_MODE
    assert controller.state.mode is not LessonMode.FINISHED
    assert result.effect is not LessonEffect.SESSION_FINISHED


def test_06_duplicate_slide_completed_event_id_does_not_advance_twice() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    first = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, "dup-slide"))
    assert first.effect is LessonEffect.PRESENT_SLIDE
    assert controller.state.cursor.slide_index == 1
    snapshot = controller.state
    second = controller.apply(ev(LessonEventType.SLIDE_COMPLETED, "dup-slide"))
    assert second.effect is LessonEffect.NO_ACTION
    assert controller.state.cursor.slide_index == 1
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == snapshot.cursor
    assert controller.state.mode == snapshot.mode


def test_07_interruption_saves_cursor() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 2)
    checkpoint = NarrationCursor(slide_index=2, segment_index=4, word_index=11)
    result = controller.apply(
        ev(LessonEventType.USER_INTERRUPTED, "int-1", cursor=checkpoint)
    )
    assert result.effect is LessonEffect.STOP_NARRATION
    assert controller.state.mode is LessonMode.INTERRUPTED
    assert controller.state.cursor == checkpoint
    assert controller.state.interruption_cursor == checkpoint


def test_08_answer_completion_restores_exact_saved_cursor() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 3)
    checkpoint = NarrationCursor(slide_index=3, segment_index=1, word_index=9)
    controller.apply(ev(LessonEventType.USER_INTERRUPTED, "int-1", cursor=checkpoint))
    begin = controller.apply(ev(LessonEventType.ANSWER_STARTED, "ans-start"))
    assert begin.effect is LessonEffect.BEGIN_ANSWER
    assert controller.state.mode is LessonMode.ANSWERING
    assert controller.state.interruption_cursor == checkpoint
    done = controller.apply(ev(LessonEventType.ANSWER_COMPLETED, "ans-done"))
    assert done.effect is LessonEffect.RESUME_NARRATION
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == checkpoint
    assert controller.state.interruption_cursor is None


def test_09_pause_preserves_cursor() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 1)
    checkpoint = NarrationCursor(slide_index=1, segment_index=2, word_index=5)
    result = controller.apply(
        ev(LessonEventType.PAUSE_REQUESTED, "pause-1", cursor=checkpoint)
    )
    assert result.effect is LessonEffect.STOP_NARRATION
    assert controller.state.mode is LessonMode.PAUSED
    assert controller.state.cursor == checkpoint
    assert controller.state.mode_before_pause is LessonMode.PRESENTING


def test_10_resume_restores_previous_mode_and_cursor() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 4)
    checkpoint = NarrationCursor(slide_index=4, segment_index=0, word_index=3)
    controller.apply(ev(LessonEventType.PAUSE_REQUESTED, "pause-1", cursor=checkpoint))
    result = controller.apply(ev(LessonEventType.RESUME_REQUESTED, "resume-1"))
    assert result.effect is LessonEffect.RESUME_NARRATION
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == checkpoint
    assert controller.state.mode_before_pause is None


def test_11_duplicate_pause_is_idempotent() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    checkpoint = NarrationCursor(slide_index=0, segment_index=1, word_index=2)
    controller.apply(ev(LessonEventType.PAUSE_REQUESTED, "pause-1", cursor=checkpoint))
    before = controller.state
    result = controller.apply(ev(LessonEventType.PAUSE_REQUESTED, "pause-2"))
    assert result.effect is LessonEffect.NO_ACTION
    assert controller.state.mode is LessonMode.PAUSED
    assert controller.state.cursor == checkpoint
    assert controller.state.mode_before_pause is before.mode_before_pause


def test_12_duplicate_resume_is_idempotent() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    controller.apply(ev(LessonEventType.PAUSE_REQUESTED, "pause-1"))
    first = controller.apply(ev(LessonEventType.RESUME_REQUESTED, "resume-1"))
    assert first.effect is LessonEffect.RESUME_NARRATION
    assert controller.state.mode is LessonMode.PRESENTING
    cursor_after = controller.state.cursor
    second = controller.apply(ev(LessonEventType.RESUME_REQUESTED, "resume-2"))
    assert second.effect is LessonEffect.NO_ACTION
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == cursor_after


def test_13_goto_slide_works_from_presenting() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    advance_to_slide(controller, 2)
    result = controller.apply(
        ev(LessonEventType.GOTO_SLIDE_REQUESTED, "goto-5", slide_index=5)
    )
    assert result.effect is LessonEffect.PRESENT_SLIDE
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == NarrationCursor(5, 0, 0)


def test_14_goto_slide_works_from_qa_mode() -> None:
    controller = LessonController(slide_count=8)
    reach_qa(controller)
    result = controller.apply(
        ev(LessonEventType.GOTO_SLIDE_REQUESTED, "goto-1", slide_index=1)
    )
    assert result.effect is LessonEffect.PRESENT_SLIDE
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == NarrationCursor(1, 0, 0)


def test_15_invalid_negative_slide_is_rejected() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    before = controller.state
    with pytest.raises(InvalidLessonTransition):
        controller.apply(
            ev(LessonEventType.GOTO_SLIDE_REQUESTED, "bad-neg", slide_index=-1)
        )
    assert controller.state is before
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor.slide_index == 0


def test_16_slide_index_equal_to_slide_count_is_rejected() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    before = controller.state
    with pytest.raises(InvalidLessonTransition):
        controller.apply(
            ev(LessonEventType.GOTO_SLIDE_REQUESTED, "bad-eq", slide_index=8)
        )
    assert controller.state is before


def test_17_only_end_session_enters_finished() -> None:
    controller = LessonController(slide_count=8)
    reach_qa(controller)
    assert controller.state.mode is LessonMode.QA_MODE
    result = controller.apply(ev(LessonEventType.END_SESSION, "end-1"))
    assert result.effect is LessonEffect.SESSION_FINISHED
    assert controller.state.mode is LessonMode.FINISHED


def test_18_events_after_finished_cannot_restart_or_mutate() -> None:
    controller = LessonController(slide_count=8)
    start(controller)
    controller.apply(ev(LessonEventType.END_SESSION, "end-1"))
    assert controller.state.mode is LessonMode.FINISHED
    before = controller.state
    with pytest.raises(InvalidLessonTransition):
        controller.apply(ev(LessonEventType.START_LESSON, "restart"))
    assert controller.state is before
    with pytest.raises(InvalidLessonTransition):
        controller.apply(ev(LessonEventType.GOTO_SLIDE_REQUESTED, "g", slide_index=0))
    assert controller.state is before
    with pytest.raises(InvalidLessonTransition):
        controller.apply(ev(LessonEventType.END_SESSION, "end-2"))
    assert controller.state is before


def test_19_invalid_transitions_leave_immutable_state_unchanged() -> None:
    controller = LessonController(slide_count=8)
    before = controller.state
    with pytest.raises(InvalidLessonTransition):
        controller.apply(ev(LessonEventType.SLIDE_COMPLETED, "too-early"))
    assert controller.state is before
    assert controller.state.mode is LessonMode.IDLE

    start(controller)
    presenting = controller.state
    with pytest.raises(InvalidLessonTransition):
        controller.apply(ev(LessonEventType.ANSWER_STARTED, "bad-answer"))
    assert controller.state is presenting
    assert controller.state.mode is LessonMode.PRESENTING


def test_20_lesson_controller_has_no_pipecat_or_openai_imports() -> None:
    import ast
    from pathlib import Path

    import tutor_agent.lesson.lesson_controller as lesson_controller

    source = Path(lesson_controller.__file__).resolve()
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    forbidden = {"pipecat", "openai", "fastapi", "uvicorn", "httpx", "aiohttp"}
    assert imported_roots.isdisjoint(forbidden)
    assert imported_roots <= {"__future__", "dataclasses", "enum", "typing"}
