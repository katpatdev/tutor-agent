"""Pause/resume must not duplicate Live Conversation narration entries."""

from __future__ import annotations

import asyncio
from typing import List

from tutor_agent.lesson.classroom_control import parse_classroom_control
from tutor_agent.lesson.conversation_ledger import ConversationLedger, PlaybackStatus, TutorSource
from tutor_agent.lesson.lesson_controller import LessonMode
from tutor_agent.narration.narration_plan import SegmentStatus
from tutor_agent.audio.presentation_runtime import (
    OutputPurpose,
    PresentationRuntime,
    RecordingFrameSink,
    make_test_append_frame,
    make_test_interruption_frame,
    make_test_transform_frame,
)
from tutor_agent.audio.tts_unit import OwnedSpeechUnit, SpeechUnitKind


def make_runtime() -> tuple[PresentationRuntime, RecordingFrameSink]:
    sink = RecordingFrameSink()
    runtime = PresentationRuntime(
        slide_prompts=[f"SLIDE {i}" for i in range(8)],
        frame_sink=sink,
        interruption_frame_factory=make_test_interruption_frame,
        messages_append_frame_factory=make_test_append_frame,
        messages_transform_frame_factory=make_test_transform_frame,
        narration_max_characters=80,
        narration_min_characters=1,
    )
    return runtime, sink


TWO = (
    "First section about earthquakes and safety prep. "
    "Second section about floods and water care."
)


def _tutor_entries(runtime: PresentationRuntime) -> List[dict]:
    return [
        e
        for e in runtime.conversation_ledger.snapshot()
        if e.get("role") == "assistant"
    ]


def test_initial_narration_creates_one_tutor_entry() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        tutors = _tutor_entries(runtime)
        assert len(tutors) == 1
        assert tutors[0]["source"] == TutorSource.NARRATION.value
        assert tutors[0]["playback_status"] == PlaybackStatus.QUEUED.value
        await runtime.on_bot_started_speaking()
        tutors = _tutor_entries(runtime)
        assert len(tutors) == 1
        assert tutors[0]["playback_status"] == PlaybackStatus.SPEAKING.value

    asyncio.run(_run())


def test_pause_resume_requeues_same_segment_without_duplicate_entry() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        first_id = _tutor_entries(runtime)[0]["entry_id"]
        first_seq = _tutor_entries(runtime)[0]["sequence"]
        await runtime.pause()
        assert runtime.state.mode is LessonMode.PAUSED
        plan = runtime.narration_plan
        assert plan is not None
        assert plan.active_status is SegmentStatus.INTERRUPTED
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()
        assert runtime.output_purpose is OutputPurpose.RESUMED_NARRATION
        assert sink.tts_texts.count("First section about earthquakes and safety prep.") >= 2
        tutors = _tutor_entries(runtime)
        narration = [t for t in tutors if t.get("source") == TutorSource.NARRATION.value]
        assert len(narration) == 1
        assert narration[0]["entry_id"] == first_id
        assert narration[0]["sequence"] == first_seq
        assert narration[0]["playback_status"] == PlaybackStatus.QUEUED.value

    asyncio.run(_run())


def test_repeated_pause_resume_still_one_entry() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        entry_id = _tutor_entries(runtime)[0]["entry_id"]
        for _ in range(3):
            await runtime.pause()
            await runtime.on_bot_stopped_speaking()
            await runtime.resume()
            await runtime.on_bot_started_speaking()
        narration = [
            t
            for t in _tutor_entries(runtime)
            if t.get("source") == TutorSource.NARRATION.value
        ]
        assert len(narration) == 1
        assert narration[0]["entry_id"] == entry_id
        assert runtime.state.cursor.slide_index == 0

    asyncio.run(_run())


def test_tts_retry_preserves_logical_id_conversation_entry() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        unit = runtime._tts.pending
        assert unit is not None
        logical = unit.logical_id
        # Simulate no-audio retry identity rotation (unit_id changes, logical stays).
        unit.unit_id = OwnedSpeechUnit.new_id()
        unit.attempt = 2
        assert unit.logical_id == logical
        await runtime._mirror_tutor_unit(unit)
        narration = [
            t
            for t in _tutor_entries(runtime)
            if t.get("source") == TutorSource.NARRATION.value
        ]
        assert len(narration) == 1
        assert narration[0]["tts_unit_id"] == logical

    asyncio.run(_run())


def test_next_segment_creates_new_entry() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        tutors = _tutor_entries(runtime)
        assert len(tutors) == 2
        assert tutors[0]["entry_id"] != tutors[1]["entry_id"]
        assert tutors[0]["text"] != tutors[1]["text"]

    asyncio.run(_run())


def test_new_generation_creates_new_entry() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration("Alpha sentence about storms.")
        first = _tutor_entries(runtime)[0]["entry_id"]
        first_logical = _tutor_entries(runtime)[0]["tts_unit_id"]
        # Force a new plan/generation via slide jump then back.
        await runtime.go_to_slide(1)
        await runtime.accept_approved_narration("Alpha sentence about storms.")
        tutors = [
            t
            for t in _tutor_entries(runtime)
            if t.get("text") == "Alpha sentence about storms."
        ]
        assert len(tutors) >= 2
        assert tutors[0]["entry_id"] == first
        assert tutors[-1]["entry_id"] != first
        assert tutors[-1]["tts_unit_id"] != first_logical

    asyncio.run(_run())


def test_navigation_creates_entries_for_new_slide() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        before = len(_tutor_entries(runtime))
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Go to slide 3.", slide_count=8)
        )
        assert ok
        # Nav ack queued as tutor speech.
        assert len(_tutor_entries(runtime)) >= before + 1
        assert any("moving to slide 3" in t.lower() for t in sink.tts_texts)

    asyncio.run(_run())


def test_explicit_repeat_creates_separate_repeat_entry() -> None:
    async def _run() -> None:
        runtime, sink = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        narr_id = _tutor_entries(runtime)[0]["entry_id"]
        ok = await runtime.handle_classroom_control(
            parse_classroom_control("Can you repeat this line?")
        )
        assert ok
        tutors = _tutor_entries(runtime)
        repeats = [t for t in tutors if t.get("source") == TutorSource.REPEAT.value]
        narration = [t for t in tutors if t.get("source") == TutorSource.NARRATION.value]
        assert len(narration) == 1
        assert narration[0]["entry_id"] == narr_id
        assert len(repeats) == 1
        assert repeats[0]["entry_id"] != narr_id
        assert sink.tts_texts[-1] == narration[0]["text"]

    asyncio.run(_run())


def test_identical_text_different_segments_not_deduped_by_text() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        # Two segments with identical wording (forced via short max chars + repeated sentences).
        await runtime.accept_approved_narration(
            "Same sentence about safety. Same sentence about safety."
        )
        plan = runtime.narration_plan
        assert plan is not None
        # Ensure we get two segments; if not, complete first and inject second via mirror ids.
        await runtime.on_bot_started_speaking()
        await runtime.on_bot_stopped_speaking()
        tutors = _tutor_entries(runtime)
        assert len(tutors) >= 2
        assert tutors[0]["entry_id"] != tutors[1]["entry_id"]
        assert tutors[0]["tts_unit_id"] != tutors[1]["tts_unit_id"]

    asyncio.run(_run())


def test_ledger_snapshot_plus_same_tts_id_does_not_duplicate() -> None:
    async def _run() -> None:
        ledger = ConversationLedger()
        await ledger.add_or_update_tutor(
            "Hello",
            source=TutorSource.NARRATION,
            tts_unit_id="narration:g1:0",
            playback_status=PlaybackStatus.QUEUED,
        )
        await ledger.add_or_update_tutor(
            "Hello",
            source=TutorSource.NARRATION,
            tts_unit_id="narration:g1:0",
            playback_status=PlaybackStatus.SPEAKING,
        )
        assert ledger.entries_created == 1
        assert ledger.duplicates_ignored == 1
        assert len(ledger.snapshot()) == 1
        assert ledger.snapshot()[0]["playback_status"] == PlaybackStatus.SPEAKING.value

    asyncio.run(_run())


def test_observer_failure_does_not_block_pause_resume_audio() -> None:
    async def _run() -> None:
        async def _boom(_msg: dict) -> None:
            raise RuntimeError("publish failed")

        runtime, sink = make_runtime()
        runtime.set_conversation_publisher(_boom)
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        assert sink.tts_texts
        await runtime.on_bot_started_speaking()
        await runtime.pause()
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()
        assert runtime.state.mode is LessonMode.PRESENTING
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.active_status is SegmentStatus.ACTIVE
        assert runtime.conversation_ledger.publish_failures >= 1

    asyncio.run(_run())


def test_pause_resume_preserves_narration_cursor() -> None:
    async def _run() -> None:
        runtime, _ = make_runtime()
        await runtime.start_session()
        await runtime.accept_approved_narration(TWO)
        await runtime.on_bot_started_speaking()
        slide = runtime.state.cursor.slide_index
        seg = runtime.narration_plan.active_segment_index if runtime.narration_plan else -1
        await runtime.pause()
        await runtime.on_bot_stopped_speaking()
        await runtime.resume()
        assert runtime.state.cursor.slide_index == slide
        assert runtime.narration_plan is not None
        assert runtime.narration_plan.active_segment_index == seg

    asyncio.run(_run())


def test_narration_conversation_id_helper() -> None:
    stable = PresentationRuntime._narration_conversation_id(
        generation_id="gen-a", segment_index=2
    )
    again = PresentationRuntime._narration_conversation_id(
        generation_id="gen-a", segment_index=2
    )
    assert stable == again == "narration:gen-a:2"
    repeat = PresentationRuntime._narration_conversation_id(
        generation_id="gen-a", segment_index=2, explicit_repeat=True
    )
    assert repeat.startswith("narration:gen-a:2:repeat:")
    assert repeat != stable
