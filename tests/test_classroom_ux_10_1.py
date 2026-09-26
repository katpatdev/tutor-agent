"""Iteration 10.1 classroom UX regression tests (offline, no OpenAI / WebSocket)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from tutor_agent.knowledge.knowledge_ingestion import is_conversational_ack, looks_like_question
from tutor_agent.lesson.lesson_controller import (
    LessonController,
    LessonEffect,
    LessonEvent,
    LessonEventType,
    LessonMode,
    NarrationCursor,
)
from tutor_agent.audio.presentation_runtime import (
    NO_ANSWER_CONTINUE_TEXT,
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_transform_frame,
    make_test_interruption_frame,
)
from tutor_agent.observability.session_metrics import SessionMetricsCollector
from tutor_agent.audio.voice_runtime_config import (
    load_no_answer_timeout_seconds,
    load_tts_speech_speed,
    load_vad_runtime_config,
)


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


def _ev(event_type: LessonEventType, event_id: str, **kwargs) -> LessonEvent:
    return LessonEvent(type=event_type, event_id=event_id, **kwargs)


def make_runtime(
    *,
    slide_count: int = 3,
    max_characters: int = 80,
    no_answer_timeout_seconds: float = 10.0,
) -> tuple[PresentationRuntime, RecordingFrameSink]:
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {index}" for index in range(slide_count)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=max_characters,
        narration_min_characters=1,
        no_answer_timeout_seconds=no_answer_timeout_seconds,
    )
    runtime.set_tts_speak_frame_factory(FakeTTSSpeakFrame)
    return runtime, sink


def test_controller_resume_from_answering_returns_to_narration() -> None:
    controller = LessonController(slide_count=8)
    controller.apply(_ev(LessonEventType.START_LESSON, "start"))
    checkpoint = NarrationCursor(slide_index=2, segment_index=1, word_index=0)
    controller.apply(
        _ev(LessonEventType.USER_INTERRUPTED, "int", cursor=checkpoint)
    )
    controller.apply(_ev(LessonEventType.ANSWER_STARTED, "ans"))
    assert controller.state.mode is LessonMode.ANSWERING
    controller.apply(
        _ev(LessonEventType.PAUSE_REQUESTED, "pause", cursor=checkpoint)
    )
    assert controller.state.mode is LessonMode.PAUSED
    assert controller.state.mode_before_pause is LessonMode.ANSWERING
    result = controller.apply(_ev(LessonEventType.RESUME_REQUESTED, "resume"))
    assert result.effect is LessonEffect.RESUME_NARRATION
    assert controller.state.mode is LessonMode.PRESENTING
    assert controller.state.cursor == checkpoint
    assert controller.state.interruption_cursor is None


def test_a_answer_lifecycle_returns_from_answering() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "First section about storms. Second section about safety."
        )
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        slide = runtime.state.cursor.slide_index
        segment = runtime.narration_plan.active_segment_index if runtime.narration_plan else 0
        await runtime.on_bot_stopped_speaking()  # cancelled narration
        await runtime.on_bot_started_speaking()  # answer
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == slide
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        from tutor_agent.lesson.classroom_control import parse_classroom_control
        assert await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert runtime.output_purpose is OutputPurpose.RESUME_BRIDGE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.active_segment_index == segment
        assert sink.tts_texts[-1] == "First section about storms."

    asyncio.run(_run())


def test_b_cancelled_answer_completion_cannot_advance_or_duplicate() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Alpha sentence. Beta sentence.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Stale cancelled stop while answering must not complete the answer.
        runtime._expected_suppressed_stops = 1
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.state.cursor.slide_index == 0
        before = list(sink.tts_texts)
        # Valid answer completion
        runtime.begin_moderated_answer_speech(1)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 0
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        from tutor_agent.lesson.classroom_control import parse_classroom_control
        assert await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert runtime.output_purpose is OutputPurpose.RESUME_BRIDGE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Exactly one segment replay queued after continue; no slide skip.
        assert sink.tts_texts[len(before) :][-1] == "Alpha sentence."

    asyncio.run(_run())


def test_c_silence_timeout_continues_once() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(
            no_answer_timeout_seconds=0.05,
            max_characters=40,
        )
        await runtime.start_session()
        await runtime.go_to_slide(2)
        # Two segments; first ends with '?' so the runtime arms a one-shot wait.
        await runtime.accept_approved_narration(
            "What is a hurricane? Next we cover preparedness steps."
        )
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.total_segments >= 2
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime._awaiting_student_reply is True
        assert runtime.state.mode is LessonMode.PRESENTING
        await asyncio.sleep(0.12)
        assert runtime.no_answer_continuations == 1
        assert NO_ANSWER_CONTINUE_TEXT in sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Continues to next segment once; does not re-arm immediately on continue.
        assert runtime.no_answer_continuations == 1
        assert runtime.state.mode is LessonMode.PRESENTING
        assert any("preparedness" in text for text in sink.tts_texts)

    asyncio.run(_run())


def test_d_silence_timer_cancelled_by_student_speech() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(no_answer_timeout_seconds=0.2)
        await runtime.start_session()
        await runtime.accept_approved_narration(
            "Why do earthquakes happen? Then we discuss safety."
        )
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime._awaiting_student_reply is True
        await runtime.on_user_started_speaking()
        assert runtime._awaiting_student_reply is False
        assert runtime.state.mode is LessonMode.ANSWERING
        await asyncio.sleep(0.25)
        assert runtime.no_answer_continuations == 0
        assert NO_ANSWER_CONTINUE_TEXT not in sink.tts_texts

    asyncio.run(_run())


def test_e_pause_stops_without_advancing() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("One. Two. Three.")
        await runtime.on_bot_started_speaking()
        slide = runtime.state.cursor.slide_index
        before_interrupts = sink.interruption_count
        await runtime.pause()
        assert runtime.state.mode is LessonMode.PAUSED
        assert runtime.state.cursor.slide_index == slide
        assert runtime.output_purpose is OutputPurpose.NONE
        assert sink.interruption_count > before_interrupts
        # No next segment should begin while paused.
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.segment_queued is False

    asyncio.run(_run())


def test_f_resume_without_student_speech_replays_interrupted_segment() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Segment A here. Segment B here.")
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.active_segment_index == 1
        await runtime.pause()
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.narration_plan.active_segment_index == 1
        assert runtime.segment_replays == 1
        assert sink.tts_texts[-1] == "Segment B here."

    asyncio.run(_run())


def test_f2_resume_from_answering_does_not_require_speech() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Keep going A. Keep going B.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.pause()
        await runtime.resume()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts[-1] == "Keep going A."
        assert runtime.segment_replays >= 1

    asyncio.run(_run())


def test_g_slide_progression_after_interruption_answer() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime(slide_count=3)
        await runtime.start_session()
        await runtime.accept_approved_narration("Only one segment on slide zero.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        await runtime.on_bot_stopped_speaking()
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 0
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        from tutor_agent.lesson.classroom_control import parse_classroom_control
        assert await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert runtime.output_purpose is OutputPurpose.RESUME_BRIDGE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        # Finish resumed segment → advance to next slide.
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 1
        assert runtime.state.mode is LessonMode.PRESENTING

    asyncio.run(_run())


def test_conversational_acks_skip_question_heuristic() -> None:
    assert is_conversational_ack("okay")
    assert is_conversational_ack("Repeat that!")
    assert not looks_like_question("yes")
    assert looks_like_question("What causes earthquakes?")


def test_voice_config_defaults_are_safe() -> None:
    assert 8.0 <= load_no_answer_timeout_seconds() <= 12.0 or (
        load_no_answer_timeout_seconds() == 10.0
    )
    speed = load_tts_speech_speed()
    assert 1.0 <= speed <= 1.1
    vad = load_vad_runtime_config()
    assert vad.stop_secs <= 0.35
    assert vad.confidence == 0.85


def test_answer_stage_metrics_are_content_free() -> None:
    collector = SessionMetricsCollector(session_id="s-ux")
    collector.note_answer_stage("answer_started")
    collector.note_answer_stage("tts_start")
    collector.note_answer_stage("answer_playback_complete")
    report = collector.build_disconnect_report()
    assert report["answer_lifecycle_stages"]["answer_started"] == 1
    assert "transcript" not in report
    assert "text" not in report
