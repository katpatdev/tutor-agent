"""Comprehensive offline narration tests.

No test in this module opens /ws, calls OpenAI, or uses the production SQLite
path. Async runtime cases deliberately use ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from lesson_controller import LessonMode
from narration_plan import (
    NarrationConfigError,
    ResumeAccuracy,
    SegmentStatus,
    build_narration_plan,
    load_narration_max_characters,
    segment_narration_text,
)
from presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_transform_frame,
    make_test_interruption_frame,
)
from session_metrics import SessionMetricsCollector
from slide_narration_prompt import is_slide_narration_instruction


@dataclass
class FakeTTSSpeakFrame:
    text: str
    kind: str = "tts_speak"


def make_runtime(
    *,
    slide_count: int = 2,
    max_characters: int = 40,
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
    )
    runtime.set_tts_speak_frame_factory(FakeTTSSpeakFrame)
    return runtime, sink


async def complete_active_segment(runtime: PresentationRuntime) -> None:
    await runtime.on_bot_started_speaking()
    await runtime.on_bot_stopped_speaking()


def test_segmentation_is_deterministic_and_preserves_content() -> None:
    text = "Dr. Lee measured 2.5 meters. Then the water receded! Is that clear?"
    first = segment_narration_text(text, max_characters=40)
    second = segment_narration_text(text, max_characters=40)

    assert first == second
    assert [segment.index for segment in first] == list(range(len(first)))
    assert " ".join(segment.text for segment in first) == text
    assert any("2.5" in segment.text for segment in first)
    assert all(segment.text.strip() for segment in first)
    assert all(len(segment.text) <= 40 for segment in first)


def test_segmentation_handles_empty_and_long_unpunctuated_text() -> None:
    assert segment_narration_text(" \n\t ", max_characters=40) == ()
    segments = segment_narration_text(
        "one two three four five six seven eight nine ten eleven twelve",
        max_characters=40,
    )
    assert len(segments) >= 2
    assert " ".join(segment.text for segment in segments) == (
        "one two three four five six seven eight nine ten eleven twelve"
    )


def test_narration_configuration_validation() -> None:
    assert load_narration_max_characters({}) == 320
    assert load_narration_max_characters(
        {"NARRATION_SEGMENT_MAX_CHARACTERS": "80"}
    ) == 80
    with pytest.raises(NarrationConfigError):
        load_narration_max_characters(
            {"NARRATION_SEGMENT_MAX_CHARACTERS": "not-an-integer"}
        )
    with pytest.raises(NarrationConfigError):
        load_narration_max_characters({"NARRATION_SEGMENT_MAX_CHARACTERS": "39"})


def test_plan_lifecycle_defaults_and_public_progress_have_no_text() -> None:
    plan = build_narration_plan(
        slide_index=3,
        text="First section. Second section.",
        max_characters=40,
        min_characters=1,
    )
    progress = plan.progress().to_public_dict()

    assert plan.slide_index == 3
    assert plan.total_segments == 2
    assert plan.active_segment_index == 0
    assert plan.active_status is SegmentStatus.PENDING
    assert plan.completed_indexes == frozenset()
    assert plan.resume_accuracy is ResumeAccuracy.SEGMENT
    assert progress == {
        "current_segment": 1,
        "total_segments": 2,
        "resume_accuracy": "segment",
    }
    assert "text" not in json.dumps(progress).lower()


def test_segment_progression_completes_only_after_final_segment() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("First section. Second section.")

        assert sink.tts_texts == ["First section."]
        assert runtime.narration_progress == {
            "current_segment": 1,
            "total_segments": 2,
            "resume_accuracy": "segment",
        }
        await complete_active_segment(runtime)
        assert runtime.state.cursor.slide_index == 0
        assert sink.tts_texts == ["First section.", "Second section."]
        assert runtime.narration_progress["current_segment"] == 2

        await complete_active_segment(runtime)
        assert runtime.state.cursor.slide_index == 1
        assert runtime.segments_completed == 2

    asyncio.run(_run())


def test_pause_resume_replays_interrupted_segment_at_segment_boundary() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("First section. Second section.")
        await runtime.on_bot_started_speaking()
        await runtime.pause()

        plan = runtime.narration_plan
        assert runtime.state.mode is LessonMode.PAUSED
        assert plan is not None
        assert plan.active_status is SegmentStatus.INTERRUPTED
        assert runtime.segment_interruptions == 1

        # This is the cancelled stop from the interrupted transport audio.
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()

        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts == ["First section.", "First section."]
        assert runtime.segment_replays == 1
        assert runtime.narration_progress["resume_accuracy"] == "segment"

    asyncio.run(_run())


def test_pause_between_segments_resumes_next_pending_segment_without_replay() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("First section. Second section.")
        await complete_active_segment(runtime)

        assert sink.tts_texts[-1] == "Second section."
        await runtime.pause()
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()

        assert runtime.narration_plan is not None
        assert runtime.narration_plan.active_segment_index == 1
        assert runtime.segment_replays == 1
        assert sink.tts_texts[-1] == "Second section."

    asyncio.run(_run())


def test_interruption_answer_resumes_same_segment_without_advancing_slide() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("First section. Second section.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()

        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.state.cursor.slide_index == 0
        assert runtime.output_purpose is OutputPurpose.INTERRUPTION_ANSWER
        await runtime.on_bot_stopped_speaking()  # cancelled narration stop
        await runtime.on_bot_started_speaking()  # interruption answer
        await runtime.on_bot_stopped_speaking()

        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.state.cursor.slide_index == 0
        assert runtime.output_purpose is OutputPurpose.POST_ANSWER_INVITE
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        from classroom_control import parse_classroom_control
        assert await runtime.handle_classroom_control(parse_classroom_control("Continue."))
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts[0] == "First section."
        assert sink.tts_texts[-1] == "First section."
        assert runtime.segment_replays == 1

    asyncio.run(_run())


def test_navigation_invalidates_old_generation_and_suppresses_cancelled_stop() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Old first. Old second.")
        old_plan = runtime.narration_plan
        await runtime.on_bot_started_speaking()
        await runtime.go_to_slide(1)

        assert old_plan is not None and old_plan.invalidated
        assert runtime.narration_plan is None
        assert runtime.state.cursor.slide_index == 1
        assert is_slide_narration_instruction(sink.system_messages[-1])
        assert "SLIDE 1" in sink.system_messages[-1]

        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 1
        assert runtime.cancelled_completions_suppressed == 1
        await runtime.accept_approved_narration("New first. New second.")
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.generation_id != old_plan.generation_id

    asyncio.run(_run())


def test_final_slide_enters_qa_only_after_final_narration_segment() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime(slide_count=1)
        await runtime.start_session()
        # Force two segments under max=40 packing bounds.
        await runtime.accept_approved_narration(
            "Final first sentence is long enough alone. "
            "Final second sentence is also long enough."
        )
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.total_segments >= 2

        await complete_active_segment(runtime)
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.output_purpose is OutputPurpose.SLIDE_NARRATION

        while (
            runtime.narration_plan is not None
            and not runtime.narration_plan.is_complete
            and runtime.state.mode is LessonMode.PRESENTING
        ):
            await complete_active_segment(runtime)

        assert runtime.state.mode is LessonMode.QA_MODE
        assert runtime.output_purpose is OutputPurpose.QA_TRANSITION
        assert sum("open Q&A" in message for message in sink.system_messages) == 1

    asyncio.run(_run())


def test_narration_metrics_are_content_free() -> None:
    collector = SessionMetricsCollector()
    collector.note_narration_snapshot(
        plans_created=2,
        segments_generated=5,
        segments_completed=4,
        segment_interruptions=1,
        segment_replays=1,
        stale_completions_ignored=1,
        cancelled_completions_suppressed=2,
        narration_errors=0,
        segment_char_total=137,
        resume_accuracy="segment",
    )
    report = collector.build_disconnect_report()
    serialized = json.dumps(report)

    assert report["narration_plans_created"] == 2
    assert report["narration_average_segment_characters"] == 27.4
    assert report["narration_resume_accuracy"] == "segment"
    assert "narration_text" not in serialized
    assert "segment_text" not in serialized
    assert "transcript" not in serialized.replace("transcript_consent", "").replace(
        "transcript_storage_active", ""
    ).replace("transcript_saved", "")


def test_health_endpoints_are_offline_and_use_temporary_session_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import dotenv

    database_path = tmp_path / "health-test.sqlite3"
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("SESSION_DB_PATH", str(database_path))
    monkeypatch.setenv("METRICS_PERSISTENCE_ENABLED", "false")
    monkeypatch.setenv("TRANSCRIPT_PERSISTENCE_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-placeholder")

    for module_name in ("main", "agent"):
        sys.modules.pop(module_name, None)

    import embedding_service
    import moderation_service

    async def _network_forbidden(*args, **kwargs):
        raise AssertionError("health route attempted an OpenAI request")

    monkeypatch.setattr(
        moderation_service.OpenAIModerationClient,
        "moderate",
        _network_forbidden,
    )
    monkeypatch.setattr(
        embedding_service.OpenAIEmbeddingClient,
        "embed",
        _network_forbidden,
    )

    import main

    with TestClient(main.app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"]["prompt_registry"] == "ok"
    assert database_path != tmp_path.parent / "data" / "tutor_sessions.sqlite3"


def test_question_mark_and_decimal_segmentation() -> None:
    segs = segment_narration_text(
        "Is 2.5 larger? Yes it is.",
        min_characters=1,
    )
    assert len(segs) >= 2
    joined = " ".join(s.text for s in segs)
    assert "2.5" in joined
    assert "?" in segs[0].text


def test_source_label_preservation() -> None:
    segs = segment_narration_text(
        "According to [source:handout] Drop, cover, and hold on. Stay calm."
    )
    assert any("[source:handout]" in s.text for s in segs)


def test_empty_approved_narration_does_not_advance() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("   ")
        assert runtime.state.cursor.slide_index == 0
        assert runtime.narration_error == "empty_narration"
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 0

    asyncio.run(_run())


def test_duplicate_resume_is_idempotent() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Alpha sentence. Beta sentence.")
        await runtime.on_bot_started_speaking()
        await runtime.pause()
        before = len(sink.tts_texts)
        await runtime.resume()
        await runtime.resume()
        assert len(sink.tts_texts) == before + 1

    asyncio.run(_run())


def test_second_interrupt_during_answer_is_deterministic() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Only one sentence here.")
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        await runtime.on_bot_started_speaking()
        await runtime.on_user_started_speaking()
        assert runtime.state.mode is LessonMode.ANSWERING
        assert runtime.output_purpose is OutputPurpose.INTERRUPTION_ANSWER
        assert sink.interruption_count >= 2

    asyncio.run(_run())


def test_safety_redirect_does_not_complete_slide() -> None:
    async def _run() -> None:
        from safety_policy import PolicyDecision, SafetyDecision, SafetyReason, SafetySource

        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Keep this slide. Really keep it.")
        await runtime.on_bot_started_speaking()
        decision = PolicyDecision(
            decision=SafetyDecision.REDIRECT,
            reason=SafetyReason.GRAPHIC_CONTENT,
            template_key="graphic_redirect",
            notice="redirect",
        )
        await runtime.handle_blocked_user_input(decision)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.cursor.slide_index == 0

    asyncio.run(_run())


def test_disconnect_invalidates_active_plan() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Stay incomplete. Second part.")
        await runtime.end_session()
        plan = runtime.narration_plan
        assert plan is None or plan.invalidated
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        assert runtime.state.mode is LessonMode.FINISHED or runtime.session_ended

    asyncio.run(_run())
