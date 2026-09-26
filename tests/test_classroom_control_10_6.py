"""Iteration 10.6: robust classroom commands, post-answer hold, bounded context."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional

import pytest

from tutor_agent.lesson.classroom_control import (
    ClassroomControlKind,
    parse_classroom_control,
    parse_navigation_intent,
)
from tutor_agent.lesson.lesson_context import (
    LESSON_CONTEXT_BEGIN,
    build_lesson_context_snapshot,
    is_lesson_context_message,
    lesson_context_message_dict,
    strip_lesson_context_messages,
)
from tutor_agent.lesson.lesson_controller import LessonMode
from tutor_agent.narration.narration_plan import SegmentStatus
from tutor_agent.audio.presentation_runtime import (
    POST_ANSWER_INVITE_TEXT,
    POST_ANSWER_REMINDER_TEXT,
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)
from tutor_agent.audio.tts_unit import TtsRecoveryConfig
from tutor_agent.lesson.voice_navigation import VoiceNavAction


@dataclass
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: List[tuple[float, asyncio.Future]] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._sleepers.append((self.now + delay, fut))
        await fut

    def advance(self, seconds: float) -> None:
        self.now += seconds
        ready = [item for item in self._sleepers if item[0] <= self.now]
        self._sleepers = [item for item in self._sleepers if item[0] > self.now]
        for _, fut in ready:
            if not fut.done():
                fut.set_result(None)


def make_runtime(
    *,
    clock: FakeClock | None = None,
    slide_count: int = 8,
    lesson_followup_wait_seconds: float = 0.05,
    qa_silence_timeout_seconds: float = 12.0,
) -> tuple[PresentationRuntime, RecordingFrameSink, FakeClock]:
    clock = clock or FakeClock()
    sink = RecordingFrameSink()
    cfg = TtsRecoveryConfig(
        max_retries=1,
        first_audio_timeout_s=2.0,
        completion_timeout_base_s=5.0,
        completion_seconds_per_char=0.01,
        completion_timeout_max_s=20.0,
        max_consecutive_failures=3,
    )

    def create_task(coro):
        return asyncio.create_task(coro)

    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {i}" for i in range(slide_count)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=80,
        narration_min_characters=1,
        tts_recovery_config=cfg,
        create_task=create_task,
        time_fn=clock.time,
        sleep_fn=clock.sleep,
        lesson_followup_wait_seconds=lesson_followup_wait_seconds,
        qa_silence_timeout_seconds=qa_silence_timeout_seconds,
    )
    return runtime, sink, clock


TWO_SEG = (
    "First section about earthquakes and safety prep. "
    "Second section about floods and water care."
)


async def load_plan(runtime: PresentationRuntime, text: str = TWO_SEG) -> None:
    await runtime.start_session()
    await runtime.accept_approved_narration(text)


# --- Parser table ---

POSITIVE_NAV = [
    ("next slide", VoiceNavAction.NEXT, None),
    ("go to the next slide", VoiceNavAction.NEXT, None),
    ("move to the next slide", VoiceNavAction.NEXT, None),
    ("proceed to the next slide", VoiceNavAction.NEXT, None),
    ("skip to the next slide", VoiceNavAction.NEXT, None),
    ("let’s move to the next slide then, if possible", VoiceNavAction.NEXT, None),
    ("this is good, can we go to the next slide now", VoiceNavAction.NEXT, None),
    ("This is good. Can we go to the next slide now?", VoiceNavAction.NEXT, None),
    ("previous slide", VoiceNavAction.PREVIOUS, None),
    ("go back to the previous slide", VoiceNavAction.PREVIOUS, None),
    ("jump back to the previous slide", VoiceNavAction.PREVIOUS, None),
    ("Can we go back to slide 3?", VoiceNavAction.GOTO, 2),
    ("go back to slide 3", VoiceNavAction.GOTO, 2),
    ("return to slide 3", VoiceNavAction.GOTO, 2),
    ("move to slide 6", VoiceNavAction.GOTO, 5),
    ("skip ahead to slide 8", VoiceNavAction.GOTO, 7),
    ("go to the sixth slide", VoiceNavAction.GOTO, 5),
    ("Can we move to the eighth slide now?", VoiceNavAction.GOTO, 7),
    ("move to the eighth slide", VoiceNavAction.GOTO, 7),
    ("take me to slide eight", VoiceNavAction.GOTO, 7),
    ("slide 8, please", VoiceNavAction.GOTO, 7),
]


@pytest.mark.parametrize("utterance,action,index", POSITIVE_NAV)
def test_positive_navigation_forms(utterance: str, action: VoiceNavAction, index: Optional[int]) -> None:
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.NAVIGATION
    assert intent.navigation is not None
    assert intent.navigation.action is action
    if index is not None:
        assert intent.navigation.slide_index == index


NEGATIVE_CONTENT = [
    "What is on slide six?",
    "Can you explain slide 3?",
    "What did slide 3 say?",
    "The next slide explains preparation.",
    "Can you go back and explain when floods occur?",
]


@pytest.mark.parametrize("utterance", NEGATIVE_CONTENT)
def test_content_questions_do_not_navigate(utterance: str) -> None:
    assert parse_navigation_intent(utterance, slide_count=8) is None
    assert parse_classroom_control(utterance, slide_count=8) is None


NEGATED = [
    "Do not go to slide 6.",
    "Don’t move to the next slide.",
    "Don't go to slide 6",
]


@pytest.mark.parametrize("utterance", NEGATED)
def test_negated_commands_do_not_navigate(utterance: str) -> None:
    assert parse_navigation_intent(utterance, slide_count=8) is None
    intent = parse_classroom_control(utterance, slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.STAY
    assert intent.navigation is None


def test_out_of_range_is_clarify_not_nav() -> None:
    intent = parse_classroom_control("Go to slide 99.", slide_count=8)
    assert intent is not None
    assert intent.kind is ClassroomControlKind.CLARIFY
    assert intent.navigation is None


def test_return_origin_phrases() -> None:
    for phrase in (
        "Return to where we were.",
        "Go back to the slide we were on.",
        "Let’s continue from where we left off.",
        "Then we can come back here.",
    ):
        intent = parse_classroom_control(phrase, slide_count=8)
        assert intent is not None
        assert intent.kind is ClassroomControlKind.RETURN_ORIGIN


# --- Integration / state machine ---


def test_01_go_back_to_slide_3_from_4() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(3)
        assert runtime.state.cursor.slide_index == 3
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Can we go back to slide 3?", slide_count=8)
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == 2
        assert any("slide 3" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_02_chatter_prefixed_next() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(3)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control(
                "This is good. Can we go to the next slide now?", slide_count=8
            )
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == 4
        assert any("slide 5" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_03_ordinal_eighth_slide_from_6() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(5)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control(
                "Can we move to the eighth slide now?", slide_count=8
            )
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == 7
        assert any("slide 8" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_04_no_old_slide_narration_after_skip() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime, "Slide six narration stays here.")
        await runtime.go_to_slide(5)
        await runtime.accept_approved_narration("Old six text. More six.")
        old_plan = runtime.narration_plan
        await runtime.on_bot_started_speaking()
        before = list(sink.tts_texts)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("skip ahead to slide 8", slide_count=8)
        )
        assert ok is True
        assert old_plan is not None and old_plan.invalidated
        assert runtime.state.cursor.slide_index == 7
        # After nav ack, no remaining old-plan segment text may play.
        new_texts = sink.tts_texts[len(before) :]
        assert "More six." not in new_texts
        assert all("six" not in t.lower() or "slide 8" in t.lower() for t in new_texts)

    asyncio.run(_run())


def test_05_what_is_on_slide_six_no_nav() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(5)
        intent = parse_classroom_control("What is on slide six?", slide_count=8)
        assert intent is None
        assert runtime.state.cursor.slide_index == 5

    asyncio.run(_run())


def test_06_negation_no_nav() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_plan(runtime)
        slide = runtime.state.cursor.slide_index
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Do not go to slide 6.", slide_count=8)
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == slide
        assert runtime.classroom_control_rejected_negation >= 1

    asyncio.run(_run())


def test_07_controls_do_not_append_llm_system_nav_promise() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.handle_classroom_control(
            parse_classroom_control("Next slide.", slide_count=8)
        )
        # Nav uses owned TTS ack + slide instruction; no speculative LLM promise text.
        assert not any("sure" in m.lower() and "skip" in m.lower() for m in sink.system_messages)

    asyncio.run(_run())


def test_08_unresolved_command_no_false_nav_ack() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        slide = runtime.state.cursor.slide_index
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Go to slide 99.", slide_count=8)
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == slide
        assert not any("Moving to slide 99" in t for t in sink.tts_texts)
        assert any("slides 1 through 8" in t for t in sink.tts_texts)

    asyncio.run(_run())


def test_09_post_answer_hold_no_auto_resume() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        assert POST_ANSWER_INVITE_TEXT in sink.tts_texts
        # Narration not auto-resumed.
        assert runtime.output_purpose is not OutputPurpose.RESUMED_NARRATION

    asyncio.run(_run())


def test_10_reminder_at_most_once() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime(lesson_followup_wait_seconds=0.05)
        await load_plan(runtime)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Finish invite speech → arm hold timer.
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        await asyncio.sleep(0.08)
        reminders = [t for t in sink.tts_texts if t == POST_ANSWER_REMINDER_TEXT]
        assert len(reminders) == 1
        assert runtime.post_answer_reminders == 1
        # Second timeout must not speak another reminder.
        if runtime.output_purpose is OutputPurpose.POST_ANSWER_REMINDER:
            await runtime.on_bot_started_speaking()
            await runtime.on_bot_stopped_speaking()
        await asyncio.sleep(0.12)
        reminders = [t for t in sink.tts_texts if t == POST_ANSWER_REMINDER_TEXT]
        assert len(reminders) == 1
        assert runtime.in_post_answer_hold

    asyncio.run(_run())


def test_11_continue_resumes_interrupted_segment() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.total_segments >= 2
        first = runtime.narration_plan.segments[0].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        plan = runtime.narration_plan
        assert plan is not None
        assert plan.active_status is SegmentStatus.INTERRUPTED
        ok = await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert ok is True
        assert not runtime.in_post_answer_hold
        assert runtime.output_purpose is OutputPurpose.RESUME_BRIDGE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts[-1] == first

    asyncio.run(_run())


def test_12_i_got_it_advances_without_replay() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.total_segments >= 2
        first = runtime.narration_plan.segments[0].text
        second = runtime.narration_plan.segments[1].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        before = list(sink.tts_texts)
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("I got it.")
        )
        assert ok is True
        assert not runtime.in_post_answer_hold
        # Should advance to second segment, not replay first.
        new = sink.tts_texts[len(before) :]
        assert first not in new
        assert any(second in t for t in new)

    asyncio.run(_run())


def test_13_nav_during_hold_cancels_resume() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        old = runtime.narration_plan
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Next slide.", slide_count=8)
        )
        assert ok is True
        assert not runtime.in_post_answer_hold
        assert runtime.state.cursor.slide_index == 1
        assert old is None or old.invalidated

    asyncio.run(_run())


def test_14_second_question_stays_in_hold_flow() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_plan(runtime)
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        # New content question while holding.
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.on_bot_stopped_speaking()
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.in_post_answer_hold
        assert runtime.post_answer_holds >= 2

    asyncio.run(_run())


def test_15_one_level_detour_return() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(3)  # slide 4
        assert runtime.state.cursor.slide_index == 3
        await runtime.accept_approved_narration("Slide four content here.")
        await runtime.on_bot_started_speaking()
        # Detour to slide 3.
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Can we go back to slide 3?", slide_count=8)
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == 2
        assert runtime.lesson_detours_created == 1
        # Return to origin.
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Return to where we were.", slide_count=8)
        )
        assert ok is True
        assert runtime.state.cursor.slide_index == 3
        assert runtime.lesson_detours_returned == 1
        assert any("returning to slide 4" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_16_stale_segment_complete_after_nav_discarded() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_plan(runtime, "Old first. Old second.")
        old = runtime.narration_plan
        await runtime.on_bot_started_speaking()
        await runtime.go_to_slide(7)
        assert old is not None and old.invalidated
        before = runtime.stale_callbacks_discarded
        # Simulate stale completion against invalidated plan.
        await runtime._complete_active_segment()  # noqa: SLF001
        assert runtime.stale_callbacks_discarded >= before
        assert runtime.state.cursor.slide_index == 7

    asyncio.run(_run())


def test_17_context_snapshot_bounded_authoritative() -> None:
    snap = build_lesson_context_snapshot(
        current_slide_1based=4,
        slide_count=8,
        mode="PRESENTING",
        interrupted_slide_1based=4,
        active_segment_1based=1,
        total_segments=2,
        visited_slides_1based=[1, 2, 3, 4],
        verified_completed_segments=[0],
        user_acknowledged_segments=[1],
        pending_resume=True,
        return_origin_slide_1based=4,
        post_answer_hold=True,
        latest_student_question="What causes floods?",
    )
    assert LESSON_CONTEXT_BEGIN in snap
    assert "Current slide: 4 of 8" in snap
    assert "not verified heard" in snap.lower() or "acknowledged by student" in snap
    assert len(snap) < 2000
    assert "Bot:" not in snap
    assert "error" not in snap.lower() or "do not" in snap.lower()


def test_18_conversation_messages_not_duplicated() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        lesson_context_message_dict("<<<LESSON_CONTEXT>>>\nx\n<<<END_LESSON_CONTEXT>>>"),
        {"role": "assistant", "content": "hello"},
        lesson_context_message_dict("<<<LESSON_CONTEXT>>>\ny\n<<<END_LESSON_CONTEXT>>>"),
    ]
    cleaned = strip_lesson_context_messages(messages)
    assert len(cleaned) == 2
    assert all(not is_lesson_context_message(str(m.get("content") or "")) for m in cleaned)
    # Re-inject once
    cleaned.append(lesson_context_message_dict("<<<LESSON_CONTEXT>>>\nz\n<<<END_LESSON_CONTEXT>>>"))
    assert sum(1 for m in cleaned if is_lesson_context_message(str(m.get("content") or ""))) == 1


def test_runtime_snapshot_uses_authoritative_slide() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_plan(runtime)
        await runtime.go_to_slide(5)
        snap = runtime.build_lesson_context_snapshot(latest_question="Why?")
        assert "Current slide: 6 of 8" in snap
        assert "Why?" in snap

    asyncio.run(_run())
