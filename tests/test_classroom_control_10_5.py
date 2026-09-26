"""Iteration 10.5: classroom controls, ack/repeat, Q&A wind-down, TTS race safety."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional

import pytest

from classroom_control import (
    ClassroomControlKind,
    parse_classroom_control,
    parse_navigation_intent,
)
from lesson_controller import LessonMode
from narration_plan import SegmentStatus
from presentation_runtime import (
    QA_CLOSING_TEXT,
    QA_FOLLOWUP_TEXT,
    QA_REMINDER_TEXT,
    OutputPurpose,
    PresentationRuntime,
    QaWindDownStage,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)
from tts_unit import OwnedSpeechUnit, SpeechUnitKind, TtsRecoveryConfig, is_tts_no_audio_error
from voice_navigation import VoiceNavAction


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


@dataclass
class FakeErrorFrame:
    error: str
    processor: Any = None
    fatal: bool = False


class FakeTTSProcessor:
    pass


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
    config: TtsRecoveryConfig | None = None,
    slide_count: int = 8,
    qa_silence_timeout_seconds: float = 0.05,
    no_answer_timeout_seconds: float = 0.05,
) -> tuple[PresentationRuntime, RecordingFrameSink, FakeClock]:
    clock = clock or FakeClock()
    sink = RecordingFrameSink()
    cfg = config or TtsRecoveryConfig(
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
        no_answer_timeout_seconds=no_answer_timeout_seconds,
        qa_silence_timeout_seconds=qa_silence_timeout_seconds,
        tts_recovery_config=cfg,
        time_fn=clock.time,
        create_task=create_task,
        sleep_fn=clock.sleep,
    )
    runtime.set_tts_speak_frame_factory(lambda text: FakeTTSSpeakFrame(text=text))
    return runtime, sink, clock


async def start_presenting(runtime: PresentationRuntime) -> None:
    await runtime.start_session()
    assert runtime.state.mode is LessonMode.PRESENTING


async def load_two_segments(runtime: PresentationRuntime) -> None:
    await start_presenting(runtime)
    await runtime.accept_approved_narration(
        "First point about earthquakes and safety. Second point about floods and water."
    )
    assert runtime.narration_plan is not None
    assert runtime.narration_plan.total_segments >= 2


# --- Navigation parsing ---


@pytest.mark.parametrize(
    "utterance",
    [
        "Next slide.",
        "Go to the next slide.",
        "Can we go to the next slide?",
        "Let's move to the next slide then, if possible.",
        "Please continue to the next slide.",
        "I'm ready to move forward.",
        "Move forward to the next slide.",
    ],
)
def test_natural_next_slide_variants(utterance: str) -> None:
    intent = parse_navigation_intent(utterance)
    assert intent is not None
    assert intent.action is VoiceNavAction.NEXT


@pytest.mark.parametrize(
    "utterance",
    [
        "Previous slide.",
        "Go back to the previous slide.",
        "Can we jump back to the previous slide?",
        "Take me back one slide.",
        "Go back one slide.",
    ],
)
def test_natural_previous_slide_variants(utterance: str) -> None:
    intent = parse_navigation_intent(utterance)
    assert intent is not None
    assert intent.action is VoiceNavAction.PREVIOUS


def test_goto_digit_and_word() -> None:
    d = parse_navigation_intent("Go to slide 6.")
    w = parse_navigation_intent("Take me to slide six.")
    assert d is not None and d.action is VoiceNavAction.GOTO and d.slide_index == 5
    assert w is not None and w.action is VoiceNavAction.GOTO and w.slide_index == 5


@pytest.mark.parametrize(
    "utterance",
    [
        "What is on slide 6?",
        "Can you explain slide 6?",
        "Why is the next slide important?",
        "When will we reach slide 6?",
        "What did you say on the previous slide?",
        "How many slides are there?",
        "Are we on slide 3?",
        "Are we repeating this slide?",
    ],
)
def test_questions_do_not_navigate(utterance: str) -> None:
    assert parse_navigation_intent(utterance) is None
    assert parse_classroom_control(utterance) is None


def test_navigation_from_answering_and_bounds() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Let's move to the next slide then, if possible.")
        )
        assert ok is True
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 1
        assert runtime.voice_nav_commands == 1
        # No LLM system append for navigation promise.
        assert not any("proceed" in m.lower() for m in sink.system_messages)

        bad = await runtime.handle_classroom_control(
            parse_classroom_control("Go to slide 99.")
        )
        assert bad is False
        assert runtime.voice_nav_rejections >= 1


    async def test_duplicate_nav_transcription_safe() -> None:
        runtime, _, _ = make_runtime()
        await start_presenting(runtime)
        intent = parse_classroom_control("Next slide.")
        assert intent is not None
        assert await runtime.handle_classroom_control(intent) is True
        slide = runtime.state.cursor.slide_index
        # Second identical command advances again (intentional) or stays if at edge —
        # from slide 0 next → 1; another next → 2.
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.state.cursor.slide_index == slide + 1
        assert runtime.voice_nav_commands == 2


    # --- Ack / continue / repeat ---
    asyncio.run(_run())

def test_duplicate_nav_transcription_safe() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await start_presenting(runtime)
        intent = parse_classroom_control("Next slide.")
        assert intent is not None
        assert await runtime.handle_classroom_control(intent) is True
        slide = runtime.state.cursor.slide_index
        # Second identical command advances again (intentional) or stays if at edge —
        # from slide 0 next → 1; another next → 2.
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.state.cursor.slide_index == slide + 1
        assert runtime.voice_nav_commands == 2


    # --- Ack / continue / repeat ---
    asyncio.run(_run())

def test_got_it_advances_without_llm_and_not_verified_complete() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        plan = runtime.narration_plan
        assert plan is not None
        first_text = plan.segments[0].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING

        before_completed = runtime.segments_completed
        intent = parse_classroom_control("Yeah, I got that point.")
        assert intent is not None and intent.kind is ClassroomControlKind.ACKNOWLEDGE
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.user_acknowledged_segments == 1
        assert runtime.segments_completed == before_completed
        plan2 = runtime.narration_plan
        assert plan2 is not None
        assert 0 in plan2.user_acknowledged_indexes
        assert plan2.active_segment_index == 1
        # Next segment queued (not a re-teach of first).
        assert any(first_text not in t for t in sink.tts_texts) or sink.tts_texts[-1] != first_text


    async def test_continue_resumes_interrupted_segment() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        plan = runtime.narration_plan
        assert plan is not None
        first = plan.segments[0].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        intent = parse_classroom_control("Continue.")
        assert intent is not None and intent.kind is ClassroomControlKind.CONTINUE
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.deterministic_continues == 1
        assert first in sink.tts_texts[-1]
    asyncio.run(_run())

def test_continue_resumes_interrupted_segment() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        plan = runtime.narration_plan
        assert plan is not None
        first = plan.segments[0].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        intent = parse_classroom_control("Continue.")
        assert intent is not None and intent.kind is ClassroomControlKind.CONTINUE
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.deterministic_continues == 1
        assert first in sink.tts_texts[-1]
    asyncio.run(_run())

def test_repeat_this_line_replays_exact_text() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        plan = runtime.narration_plan
        assert plan is not None
        first = plan.segments[0].text
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        intent = parse_classroom_control("Can you repeat this line?")
        assert intent is not None and intent.kind is ClassroomControlKind.REPEAT
        assert await runtime.handle_classroom_control(intent) is True
        assert runtime.deterministic_repeats == 1
        assert sink.tts_texts[-1] == first
        assert runtime.state.cursor.slide_index == 0


    def test_explain_differently_is_not_repeat() -> None:
        assert parse_classroom_control("Can you explain that differently?") is None


    # --- Q&A wind-down ---
    asyncio.run(_run())

async def enter_qa(runtime: PresentationRuntime, sink: RecordingFrameSink) -> None:
    await start_presenting(runtime)
    # Jump to final slide and complete it.
    await runtime.go_to_slide(7)
    await runtime.accept_approved_narration("Final wrap-up about preparedness today.")
    await runtime.on_bot_started_speaking()
    await runtime.on_bot_stopped_speaking()
    # May need multiple segments; force slide complete if still presenting.
    while runtime.state.mode is LessonMode.PRESENTING and runtime.narration_plan:
        plan = runtime.narration_plan
        if plan is None or plan.is_complete:
            break
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
    assert runtime.state.mode is LessonMode.QA_MODE
    # Finish QA invitation speech if system→LLM path not used in tests:
    # transition is via system messages; simulate QA transition TTS completion.
    runtime._output_purpose = OutputPurpose.QA_TRANSITION  # noqa: SLF001
    runtime._utterance_audible = True  # noqa: SLF001
    await runtime.on_bot_started_speaking()
    await runtime.on_bot_stopped_speaking()


def test_qa_two_stage_silence_and_closing() -> None:
    async def _run() -> None:
        runtime, sink, clock = make_runtime(qa_silence_timeout_seconds=0.05)
        await enter_qa(runtime, sink)
        assert runtime._qa_wind_down_stage is QaWindDownStage.WAITING_AFTER_PROMPT  # noqa: SLF001

        clock.advance(0.06)
        await asyncio.sleep(0.02)
        # Allow timeout task to run.
        for _ in range(20):
            if runtime.qa_reminders >= 1:
                break
            clock.advance(0.05)
            await asyncio.sleep(0.01)
        assert runtime.qa_reminders == 1
        assert QA_REMINDER_TEXT in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()

        for _ in range(20):
            if runtime.qa_silence_closes >= 1:
                break
            clock.advance(0.05)
            await asyncio.sleep(0.01)
        assert runtime.qa_silence_closes == 1
        assert QA_CLOSING_TEXT in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.FINISHED
        assert runtime.sessions_finished_normally == 1
    asyncio.run(_run())

def test_qa_speech_cancels_timer_and_explicit_close() -> None:
    async def _run() -> None:
        runtime, sink, clock = make_runtime(qa_silence_timeout_seconds=0.2)
        await enter_qa(runtime, sink)
        await runtime.on_user_started_speaking()
        assert runtime._qa_wind_down_stage is QaWindDownStage.IDLE  # noqa: SLF001
        clock.advance(0.5)
        await asyncio.sleep(0.05)
        assert runtime.qa_reminders == 0

        # Ambiguous no must not close.
        amb = parse_classroom_control(
            "No, I would not like to know about a specific type."
        )
        assert amb is None

        done = parse_classroom_control("No, I guess that's all from my end.")
        assert done is not None and done.kind is ClassroomControlKind.QA_COMPLETION
        assert await runtime.handle_classroom_control(done) is True
        assert runtime.qa_explicit_closes == 1
        assert QA_CLOSING_TEXT in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.FINISHED
    asyncio.run(_run())

def test_qa_answer_rearms_stage_one_with_followup() -> None:
    async def _run() -> None:
        runtime, sink, clock = make_runtime(qa_silence_timeout_seconds=0.05)
        await enter_qa(runtime, sink)
        # Simulate a Q&A answer completing.
        runtime._output_purpose = OutputPurpose.QA_RESPONSE  # noqa: SLF001
        runtime._answer_speech_units_remaining = 1  # noqa: SLF001
        runtime._answer_unit_texts = ["Earthquakes can be dangerous."]  # noqa: SLF001
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert QA_FOLLOWUP_TEXT in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime._qa_wind_down_stage is QaWindDownStage.WAITING_AFTER_PROMPT  # noqa: SLF001
    asyncio.run(_run())

def test_qa_closing_tts_failure_still_finishes() -> None:
    async def _run() -> None:
        runtime, sink, clock = make_runtime(
            qa_silence_timeout_seconds=0.05,
            config=TtsRecoveryConfig(max_retries=0, first_audio_timeout_s=0.05),
        )
        await enter_qa(runtime, sink)
        done = parse_classroom_control("That's all from my end.")
        assert done is not None
        assert await runtime.handle_classroom_control(done) is True
        unit = runtime._tts.pending  # noqa: SLF001
        assert unit is not None
        await runtime._on_tts_unit_exhausted(unit, cause="no_audio")  # noqa: SLF001
        assert runtime.qa_closing_failures == 1
        assert runtime.state.mode is LessonMode.FINISHED


    # --- TTS race safety ---
    asyncio.run(_run())

def test_errorframe_and_watchdog_race_one_retry() -> None:
    async def _run() -> None:
        runtime, sink, clock = make_runtime(
            config=TtsRecoveryConfig(max_retries=1, first_audio_timeout_s=0.05)
        )
        await load_two_segments(runtime)
        tts = FakeTTSProcessor()
        unit = runtime._tts.pending  # noqa: SLF001
        assert unit is not None
        before = len(sink.tts_texts)
        err = FakeErrorFrame(
            error=f"TTS context {unit.unit_id} completed with no audio",
            processor=tts,
        )
        # Concurrent-ish: error then start-timeout for same attempt.
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        clock.advance(0.1)
        await asyncio.sleep(0.05)
        assert runtime.tts_retry_attempts == 1
        # Extra silent error while ignore_error_frames is set.
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        assert runtime.tts_retry_races_suppressed >= 1
        assert len(sink.tts_texts) == before + 1  # one retry emit only


    async def test_audible_unit_not_retried_as_no_audio() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        await runtime.on_bot_started_speaking()
        before = runtime.tts_retry_attempts
        tts = FakeTTSProcessor()
        unit = runtime._tts.pending  # noqa: SLF001
        # Pending cleared on audible? on_audible_start keeps pending until completion.
        err = FakeErrorFrame(
            error="TTS context deadbeef-0000-0000-0000-000000000001 completed with no audio",
            processor=tts,
        )
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        assert runtime.tts_retry_attempts == before
        assert runtime.tts_stale_failures_ignored >= 1
    asyncio.run(_run())

def test_audible_unit_not_retried_as_no_audio() -> None:
    async def _run() -> None:
        runtime, sink, _ = make_runtime()
        await load_two_segments(runtime)
        await runtime.on_bot_started_speaking()
        before = runtime.tts_retry_attempts
        tts = FakeTTSProcessor()
        unit = runtime._tts.pending  # noqa: SLF001
        # Pending cleared on audible? on_audible_start keeps pending until completion.
        err = FakeErrorFrame(
            error="TTS context deadbeef-0000-0000-0000-000000000001 completed with no audio",
            processor=tts,
        )
        await runtime.on_tts_error_frame(err, tts_processor=tts)
        assert runtime.tts_retry_attempts == before
        assert runtime.tts_stale_failures_ignored >= 1
    asyncio.run(_run())

def test_completed_unit_ignores_late_no_audio() -> None:
    async def _run() -> None:
        runtime, _, _ = make_runtime()
        await load_two_segments(runtime)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        runtime._tts.invalidate_pending(reason="test")  # noqa: SLF001
        before = runtime.tts_retry_attempts
        tts = FakeTTSProcessor()
        await runtime.on_tts_error_frame(
            FakeErrorFrame(
                error="TTS context deadbeef-0000-0000-0000-000000000002 completed with no audio",
                processor=tts,
            ),
            tts_processor=tts,
        )
        assert runtime.tts_retry_attempts == before
        assert runtime.tts_stale_failures_ignored >= 1
    asyncio.run(_run())

def test_metrics_snapshot_has_no_lesson_text() -> None:
    from session_metrics import SessionMetricsCollector

    c = SessionMetricsCollector()
    c.note_tts_delivery_snapshot(
        units_queued=1,
        retry_races_suppressed=2,
        deterministic_repeats=1,
        qa_reminders=1,
        sessions_finished_normally=1,
    )
    report = c.build_disconnect_report()
    blob = str(report)
    assert "earthquake" not in blob.lower()
    assert report["tts_retry_races_suppressed"] == 2
    assert report["deterministic_repeats"] == 1
